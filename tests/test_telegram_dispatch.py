"""Tests for dispatch/telegram.py. All HTTP is faked via a fake session -
no real network calls anywhere in this file.
"""

from __future__ import annotations

from datetime import date

import pytest
import requests

from trip_hunter.dispatch.telegram import (
    dispatch_deal_alert,
    get_bot_token,
    get_chat_id,
    get_free_chat_id,
    get_vip_chat_id,
    send_telegram_alert,
)
from trip_hunter.models import AccommodationOffer, Deal, DealScore, DealType, FlightOffer

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_FREE_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_VIP_CHAT_ID", raising=False)


def _deal(
    *, deal_type: DealType = DealType.FLIGHT_DROP, savings_percentage: float | None = 0.436
) -> Deal:
    flight = FlightOffer(
        origin="HAM", destination="PMI", departure_date=_FRI, return_date=_SUN,
        price=79.0, currency="EUR", airline="Eurowings", stops=0, provider="test",
        booking_link="https://example.com/book/flight",
    )
    return Deal(
        deal_type=deal_type, flight=flight, accommodation=None,
        expected_flight_price=140.0, expected_accommodation_price=None,
        score=DealScore(total=70, breakdown={}),
        savings_absolute=61.0, savings_percentage=savings_percentage,
    )


def _deal_with_hotel() -> Deal:
    from dataclasses import replace

    hotel = AccommodationOffer(
        destination="PMI", check_in=_FRI, check_out=_SUN, total_price=90.0, currency="EUR",
        name="Hostal Born Boutique", rating=4.3, provider="test", booking_link="https://example.com/book/hotel",
    )
    return replace(_deal(), accommodation=hotel)


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("invalid json")
        return self._json_data


class _FakeSession:
    def __init__(self, response=None, raise_on_post=None):
        self._response = response
        self._raise_on_post = raise_on_post
        self.post_calls: list[dict] = []

    def post(self, url, data=None, timeout=None):
        self.post_calls.append({"url": url, "data": data, "timeout": timeout})
        if self._raise_on_post:
            raise self._raise_on_post
        return self._response


# --- get_bot_token / get_chat_id ---------------------------------------------


def test_get_bot_token_returns_none_when_unset():
    assert get_bot_token() is None


def test_get_bot_token_returns_configured_value(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", "123:ABC")
    assert get_bot_token() == "123:ABC"


def test_get_chat_id_returns_none_when_unset():
    assert get_chat_id() is None


def test_get_chat_id_returns_configured_value(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_CHAT_ID", "42")
    assert get_chat_id() == "42"


# --- missing credentials: fallback, no network --------------------------------


def test_missing_bot_token_prints_fallback_and_returns_false(monkeypatch, capsys):
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_CHAT_ID", "42")
    session = _FakeSession()

    result = send_telegram_alert(_deal(), session=session)

    assert result is False
    assert session.post_calls == []
    captured = capsys.readouterr().out
    assert "nicht konfiguriert" in captured
    assert "nach Palma de Mallorca" in captured  # the fallback message itself


def test_missing_chat_id_prints_fallback_and_returns_false(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", "123:ABC")
    session = _FakeSession()

    result = send_telegram_alert(_deal(), session=session)

    assert result is False
    assert session.post_calls == []


def test_missing_both_credentials_prints_fallback_and_returns_false():
    session = _FakeSession()

    result = send_telegram_alert(_deal(), session=session)

    assert result is False
    assert session.post_calls == []


def test_explicit_credentials_override_environment(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_CHAT_ID", "env-chat")
    session = _FakeSession(response=_FakeResponse(status_code=200, json_data={"ok": True}))

    send_telegram_alert(_deal(), bot_token="explicit-token", chat_id="explicit-chat", session=session)

    assert "explicit-token" in session.post_calls[0]["url"]
    assert "env-token" not in session.post_calls[0]["url"]
    assert session.post_calls[0]["data"]["chat_id"] == "explicit-chat"


# --- successful send -----------------------------------------------------------


def test_successful_send_returns_true():
    session = _FakeSession(response=_FakeResponse(status_code=200, json_data={"ok": True, "result": {}}))

    result = send_telegram_alert(_deal(), bot_token="123:ABC", chat_id="42", session=session)

    assert result is True


def test_payload_structure_is_correct():
    session = _FakeSession(response=_FakeResponse(status_code=200, json_data={"ok": True}))

    send_telegram_alert(_deal(), bot_token="123:ABC", chat_id="42", session=session)

    assert len(session.post_calls) == 1
    call = session.post_calls[0]
    assert call["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert call["data"]["chat_id"] == "42"
    assert "nach Palma de Mallorca" in call["data"]["text"]
    assert "Hamburg nach" in call["data"]["text"]
    assert call["timeout"] == 10.0


def test_message_text_matches_instant_alert_formatter():
    from trip_hunter.alerts.instant_alert_formatter import format_instant_alert

    deal = _deal()
    session = _FakeSession(response=_FakeResponse(status_code=200, json_data={"ok": True}))

    send_telegram_alert(deal, bot_token="123:ABC", chat_id="42", session=session)

    assert session.post_calls[0]["data"]["text"] == format_instant_alert(deal, link_lines=False)


# --- error handling: never crash, never leak the token -----------------------


def test_timeout_is_caught_and_returns_false():
    session = _FakeSession(raise_on_post=requests.exceptions.Timeout())

    result = send_telegram_alert(_deal(), bot_token="secret-token-123", chat_id="42", session=session)

    assert result is False


def test_connection_error_is_caught_and_returns_false():
    session = _FakeSession(raise_on_post=requests.exceptions.ConnectionError())

    result = send_telegram_alert(_deal(), bot_token="secret-token-123", chat_id="42", session=session)

    assert result is False


def test_network_error_message_never_contains_the_bot_token(monkeypatch, capsys):
    """Regression-style guard: requests/urllib3 exceptions commonly embed
    the request URL (which contains the token) in their str(). This
    module must never print that raw exception."""

    class _LeakyException(requests.exceptions.RequestException):
        def __str__(self):
            return "HTTPSConnectionPool(host='api.telegram.org', port=443): https://api.telegram.org/botsecret-token-123/sendMessage timed out"

    session = _FakeSession(raise_on_post=_LeakyException())

    send_telegram_alert(_deal(), bot_token="secret-token-123", chat_id="42", session=session)

    captured = capsys.readouterr().out
    assert "secret-token-123" not in captured


def test_http_error_status_returns_false_and_does_not_crash():
    session = _FakeSession(response=_FakeResponse(status_code=401, text="Unauthorized"))

    result = send_telegram_alert(_deal(), bot_token="bad-token", chat_id="42", session=session)

    assert result is False


def test_http_error_message_never_contains_the_bot_token(capsys):
    session = _FakeSession(response=_FakeResponse(status_code=401, text="Unauthorized"))

    send_telegram_alert(_deal(), bot_token="secret-token-123", chat_id="42", session=session)

    assert "secret-token-123" not in capsys.readouterr().out


def test_invalid_json_response_returns_false_and_does_not_crash():
    session = _FakeSession(response=_FakeResponse(status_code=200, json_data=None))

    result = send_telegram_alert(_deal(), bot_token="123:ABC", chat_id="42", session=session)

    assert result is False


def test_telegram_level_api_error_returns_false():
    session = _FakeSession(
        response=_FakeResponse(status_code=200, json_data={"ok": False, "description": "chat not found"})
    )

    result = send_telegram_alert(_deal(), bot_token="123:ABC", chat_id="wrong-chat", session=session)

    assert result is False


# --- get_free_chat_id / get_vip_chat_id ---------------------------------------


def test_get_free_chat_id_returns_none_when_unset():
    assert get_free_chat_id() is None


def test_get_free_chat_id_returns_configured_value(monkeypatch):
    monkeypatch.setenv("TELEGRAM_FREE_CHAT_ID", "-1004455242286")
    assert get_free_chat_id() == "-1004455242286"


def test_get_vip_chat_id_returns_none_when_unset():
    assert get_vip_chat_id() is None


def test_get_vip_chat_id_returns_configured_value(monkeypatch):
    monkeypatch.setenv("TELEGRAM_VIP_CHAT_ID", "-1004325690521")
    assert get_vip_chat_id() == "-1004325690521"


# --- dispatch_deal_alert: dual-channel routing --------------------------------


class _FakeSessionSequence:
    """Like _FakeSession, but returns a different response per call, in
    order - needed to test that a VIP-channel failure doesn't prevent the
    Free-channel send from being attempted (or vice versa)."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.post_calls: list[dict] = []

    def post(self, url, data=None, timeout=None):
        self.post_calls.append({"url": url, "data": data, "timeout": timeout})
        return self._responses[len(self.post_calls) - 1]


_OK_RESPONSE = _FakeResponse(status_code=200, json_data={"ok": True})


def test_neither_channel_configured_falls_back_to_default_chat_id(monkeypatch):
    """"Fallback beibehalten: Falls nur TELEGRAM_CHAT_ID gesetzt ist,
    fungiert diese als Standard." - full-detail alert, single send."""
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_CHAT_ID", "legacy-chat")
    session = _FakeSession(response=_OK_RESPONSE)

    result = dispatch_deal_alert(_deal(), bot_token="123:ABC", session=session)

    assert result is True
    assert len(session.post_calls) == 1
    assert session.post_calls[0]["data"]["chat_id"] == "legacy-chat"
    from trip_hunter.alerts.instant_alert_formatter import format_instant_alert

    assert session.post_calls[0]["data"]["text"] == format_instant_alert(_deal(), link_lines=False)


def test_neither_channel_nor_default_configured_sends_nothing(capsys):
    session = _FakeSession()

    result = dispatch_deal_alert(_deal(), bot_token="123:ABC", session=session)

    assert result is False
    assert session.post_calls == []
    assert "nicht konfiguriert" in capsys.readouterr().out


def test_vip_only_gets_full_detail_alert_with_links():
    from trip_hunter.alerts.instant_alert_formatter import format_instant_alert

    session = _FakeSession(response=_OK_RESPONSE)
    deal = _deal()

    result = dispatch_deal_alert(deal, bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    assert result is True
    assert len(session.post_calls) == 1
    call = session.post_calls[0]
    assert call["data"]["chat_id"] == "vip-chat"
    assert call["data"]["caption"] == format_instant_alert(deal, link_lines=False)
    assert "👉" not in call["data"]["caption"]  # the links are buttons now
    assert "inline_keyboard" in call["data"]["reply_markup"]


def test_free_only_gets_teaser_without_the_actual_booking_links():
    from trip_hunter.alerts.instant_alert_formatter import format_teaser_alert

    session = _FakeSession(response=_OK_RESPONSE)
    deal = _deal()

    result = dispatch_deal_alert(deal, bot_token="123:ABC", free_chat_id="free-chat", session=session)

    assert result is True
    assert len(session.post_calls) == 1
    call = session.post_calls[0]
    assert call["data"]["chat_id"] == "free-chat"
    assert call["data"]["caption"] == format_teaser_alert(deal)
    assert "example.com/book" not in call["data"]["caption"]
    assert "Buchungslinks im VIP-Kanal" in call["data"]["caption"]


def test_both_channels_configured_sends_two_distinct_messages():
    from trip_hunter.alerts.instant_alert_formatter import format_instant_alert, format_teaser_alert

    session = _FakeSession(response=_OK_RESPONSE)
    deal = _deal()

    result = dispatch_deal_alert(
        deal, bot_token="123:ABC", free_chat_id="free-chat", vip_chat_id="vip-chat", session=session
    )

    assert result is True
    assert len(session.post_calls) == 2
    by_chat = {call["data"]["chat_id"]: call["data"]["caption"] for call in session.post_calls}
    assert by_chat["vip-chat"] == format_instant_alert(deal, link_lines=False)
    assert by_chat["free-chat"] == format_teaser_alert(deal)
    assert "example.com/book" not in by_chat["free-chat"]


def test_both_channels_read_from_environment_when_not_passed_explicitly(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_FREE_CHAT_ID", "-1004455242286")
    monkeypatch.setenv("TELEGRAM_VIP_CHAT_ID", "-1004325690521")
    session = _FakeSession(response=_OK_RESPONSE)

    result = dispatch_deal_alert(_deal(), session=session)

    assert result is True
    chat_ids = {call["data"]["chat_id"] for call in session.post_calls}
    assert chat_ids == {"-1004455242286", "-1004325690521"}
    assert "env-token" in session.post_calls[0]["url"]


def test_one_channel_failing_does_not_prevent_the_other_from_being_attempted():
    """VIP send fails (e.g. bad chat id); Free send must still be
    attempted and, if it succeeds, the overall result is True."""
    vip_failure = _FakeResponse(status_code=200, json_data={"ok": False, "description": "chat not found"})
    # VIP: photo fails, text fallback fails too; Free: photo succeeds.
    session = _FakeSessionSequence([vip_failure, vip_failure, _OK_RESPONSE])

    result = dispatch_deal_alert(
        _deal(), bot_token="123:ABC", free_chat_id="free-chat", vip_chat_id="vip-chat", session=session
    )

    assert result is True
    assert len(session.post_calls) == 3
    assert session.post_calls[2]["data"]["chat_id"] == "free-chat"


def test_both_channels_failing_returns_false():
    failure = _FakeResponse(status_code=200, json_data={"ok": False, "description": "chat not found"})
    session = _FakeSessionSequence([failure] * 4)  # photo + text fallback, per channel

    result = dispatch_deal_alert(
        _deal(), bot_token="123:ABC", free_chat_id="free-chat", vip_chat_id="vip-chat", session=session
    )

    assert result is False
    assert len(session.post_calls) == 4


def test_missing_bot_token_with_channels_configured_sends_nothing(capsys):
    session = _FakeSession()

    result = dispatch_deal_alert(_deal(), free_chat_id="free-chat", vip_chat_id="vip-chat", session=session)

    assert result is False
    assert session.post_calls == []
    assert "nicht konfiguriert" in capsys.readouterr().out


def test_missing_bot_token_with_channels_configured_never_prints_a_token(monkeypatch, capsys):
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", "")
    session = _FakeSession()

    dispatch_deal_alert(_deal(), free_chat_id="free-chat", vip_chat_id="vip-chat", session=session)

    assert "secret-token" not in capsys.readouterr().out


def test_explicit_params_override_environment(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_VIP_CHAT_ID", "env-vip-chat")
    session = _FakeSession(response=_OK_RESPONSE)

    dispatch_deal_alert(_deal(), bot_token="explicit-token", vip_chat_id="explicit-vip-chat", session=session)

    assert "explicit-token" in session.post_calls[0]["url"]
    assert "env-token" not in session.post_calls[0]["url"]
    assert session.post_calls[0]["data"]["chat_id"] == "explicit-vip-chat"


# --- 3-tier channel routing (engine/alert_tier.py) ---------------------------


def test_tier_3_deal_is_vip_exclusive_free_channel_is_skipped():
    """UNUSUALLY_LOW / HOTEL_DROP (Tier 3, "Good Deal") must never reach
    the Free channel - VIP gets it, Free is silently skipped, not sent an
    empty/broken message."""
    deal = _deal(deal_type=DealType.UNUSUALLY_LOW, savings_percentage=0.18)
    session = _FakeSession(response=_OK_RESPONSE)

    result = dispatch_deal_alert(
        deal, bot_token="123:ABC", free_chat_id="free-chat", vip_chat_id="vip-chat", session=session
    )

    assert result is True
    assert len(session.post_calls) == 1
    assert session.post_calls[0]["data"]["chat_id"] == "vip-chat"


def test_tier_3_deal_with_only_a_free_channel_configured_sends_nothing():
    deal = _deal(deal_type=DealType.HOTEL_DROP, savings_percentage=0.28)
    session = _FakeSession(response=_OK_RESPONSE)

    result = dispatch_deal_alert(deal, bot_token="123:ABC", free_chat_id="free-chat", session=session)

    assert result is False
    assert session.post_calls == []


def test_tier_1_error_fare_still_reaches_both_channels():
    deal = _deal(deal_type=DealType.ERROR_FARE, savings_percentage=0.75)
    session = _FakeSession(response=_OK_RESPONSE)

    result = dispatch_deal_alert(
        deal, bot_token="123:ABC", free_chat_id="free-chat", vip_chat_id="vip-chat", session=session
    )

    assert result is True
    assert len(session.post_calls) == 2
    chat_ids = {call["data"]["chat_id"] for call in session.post_calls}
    assert chat_ids == {"free-chat", "vip-chat"}


def test_tier_2_flight_drop_still_reaches_both_channels():
    deal = _deal(deal_type=DealType.FLIGHT_DROP, savings_percentage=0.35)
    session = _FakeSession(response=_OK_RESPONSE)

    result = dispatch_deal_alert(
        deal, bot_token="123:ABC", free_chat_id="free-chat", vip_chat_id="vip-chat", session=session
    )

    assert result is True
    assert len(session.post_calls) == 2
    chat_ids = {call["data"]["chat_id"] for call in session.post_calls}
    assert chat_ids == {"free-chat", "vip-chat"}


def test_high_savings_percentage_promotes_a_deal_to_tier_1_reaches_both_channels():
    """A COMBINED_TRIP_DROP whose blended savings clears 60% counts as
    Tier 1 for routing purposes (see engine/alert_tier.py), even though
    its own deal_type isn't literally ERROR_FARE."""
    deal = _deal(deal_type=DealType.COMBINED_TRIP_DROP, savings_percentage=0.65)
    session = _FakeSession(response=_OK_RESPONSE)

    dispatch_deal_alert(
        deal, bot_token="123:ABC", free_chat_id="free-chat", vip_chat_id="vip-chat", session=session
    )

    assert len(session.post_calls) == 2


def test_single_channel_fallback_ignores_tier_entirely():
    """The legacy single-chat fallback (no Free/VIP distinction to
    enforce) must still deliver a Tier 3 deal - tier filtering only
    applies to the dual-channel Free/VIP path."""
    deal = _deal(deal_type=DealType.UNUSUALLY_LOW, savings_percentage=0.18)
    session = _FakeSession(response=_OK_RESPONSE)

    result = dispatch_deal_alert(deal, bot_token="123:ABC", default_chat_id="legacy-chat", session=session)

    assert result is True
    assert len(session.post_calls) == 1
    assert session.post_calls[0]["data"]["chat_id"] == "legacy-chat"


# --- dispatch_deal_alert: photos (sendPhoto) ---------------------------------


def test_vip_photo_is_clear_and_uses_the_destination_image():
    from trip_hunter.alerts.destination_images import destination_image_url

    session = _FakeSession(response=_OK_RESPONSE)
    dispatch_deal_alert(_deal(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    call = session.post_calls[0]
    assert call["url"] == "https://api.telegram.org/bot123:ABC/sendPhoto"
    assert call["data"]["photo"] == destination_image_url("PMI")
    assert "has_spoiler" not in call["data"]
    assert "text" not in call["data"]


def test_free_photo_is_sent_as_a_spoiler():
    session = _FakeSession(response=_OK_RESPONSE)
    dispatch_deal_alert(_deal(), bot_token="123:ABC", free_chat_id="free-chat", session=session)

    call = session.post_calls[0]
    assert call["url"].endswith("/sendPhoto")
    assert call["data"]["has_spoiler"] == "true"


def test_photo_failure_falls_back_to_plain_text_on_the_same_channel():
    from trip_hunter.alerts.instant_alert_formatter import format_instant_alert

    photo_failure = _FakeResponse(status_code=400, text="Bad Request: failed to get HTTP URL content")
    session = _FakeSessionSequence([photo_failure, _OK_RESPONSE])
    deal = _deal()

    result = dispatch_deal_alert(deal, bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    assert result is True
    assert [c["url"].rsplit("/", 1)[1] for c in session.post_calls] == ["sendPhoto", "sendMessage"]
    assert session.post_calls[1]["data"]["text"] == format_instant_alert(deal, link_lines=False)


def test_photo_network_error_falls_back_to_plain_text():
    class _PhotoRaisesSession(_FakeSession):
        def post(self, url, data=None, timeout=None):
            self.post_calls.append({"url": url, "data": data, "timeout": timeout})
            if url.endswith("/sendPhoto"):
                raise requests.exceptions.ConnectionError("boom")
            return _OK_RESPONSE

    session = _PhotoRaisesSession()
    result = dispatch_deal_alert(_deal(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    assert result is True
    assert session.post_calls[-1]["url"].endswith("/sendMessage")


def test_caption_over_telegram_limit_skips_the_photo_and_sends_text(monkeypatch):
    monkeypatch.setattr("trip_hunter.dispatch.telegram.format_instant_alert", lambda deal, **kw: "x" * 1025)
    session = _FakeSession(response=_OK_RESPONSE)

    result = dispatch_deal_alert(_deal(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    assert result is True
    assert len(session.post_calls) == 1
    assert session.post_calls[0]["url"].endswith("/sendMessage")


def test_unknown_destination_uses_the_fallback_image():
    from dataclasses import replace

    from trip_hunter.alerts.destination_images import FALLBACK_IMAGE_URL

    deal = _deal()
    deal = replace(deal, flight=replace(deal.flight, destination="XYZ"))
    session = _FakeSession(response=_OK_RESPONSE)

    dispatch_deal_alert(deal, bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    assert session.post_calls[0]["data"]["photo"] == FALLBACK_IMAGE_URL


def test_text_and_photo_messages_are_sent_with_html_parse_mode():
    session = _FakeSession(response=_OK_RESPONSE)
    dispatch_deal_alert(_deal(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session)
    assert session.post_calls[0]["data"]["parse_mode"] == "HTML"  # sendPhoto caption

    text_session = _FakeSession(response=_OK_RESPONSE)
    send_telegram_alert(_deal(), bot_token="123:ABC", chat_id="42", session=text_session)
    assert text_session.post_calls[0]["data"]["parse_mode"] == "HTML"  # sendMessage


# --- inline keyboard buttons ---------------------------------------------------

import json  # noqa: E402


@pytest.fixture
def _no_partner_ids(monkeypatch):
    for name in ("BOOKING_AFFILIATE_ID", "TRAVELPAYOUTS_MARKER", "FLIGHT_LINK_PROVIDER", "HOTEL_LINK_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


def _keyboard_of(call) -> list[list[dict]]:
    return json.loads(call["data"]["reply_markup"])["inline_keyboard"]


def test_vip_alert_carries_one_deal_sheet_web_app_button_and_no_link_lines(_no_partner_ids):
    session = _FakeSession(response=_OK_RESPONSE)
    deal = _deal_with_hotel()

    dispatch_deal_alert(deal, bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    call = session.post_calls[0]
    (row,) = _keyboard_of(call)
    assert len(row) == 1 and row[0]["text"] == "👉 Deal sichern (124 € p.P.)"  # ONE dominant button
    assert row[0]["web_app"]["url"].startswith("https://") and "deal.html?" in row[0]["web_app"]["url"]
    assert "👉" not in call["data"]["caption"] and "example.com/book" not in call["data"]["caption"]
    assert call["data"]["parse_mode"] == "HTML"


def test_free_teaser_gets_the_two_upsell_buttons_and_no_booking_link(_no_partner_ids, monkeypatch):
    for name in ("VIP_SUBSCRIPTION_URL", "TELEGRAM_BOT_USERNAME", "FAQ_URL", "FREE_CHANNEL_MODE"):
        monkeypatch.delenv(name, raising=False)
    session = _FakeSession(response=_OK_RESPONSE)

    dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", free_chat_id="free-chat", session=session)

    call = session.post_calls[0]
    rows = _keyboard_of(call)
    assert [r[0]["text"] for r in rows] == ["⚡️ Jetzt Deal buchen (VIP freischalten)", "ℹ️ Wie funktioniert Trip Hunter?"]
    assert all(set(r[0]) == {"text", "url"} for r in rows)  # plain URL buttons, no web_app
    assert "Buchungslinks im VIP-Kanal" in call["data"]["caption"]


def test_text_fallback_keeps_the_buttons(_no_partner_ids):  # noqa: D103
    photo_failure = _FakeResponse(status_code=400, text="Bad Request")
    session = _FakeSessionSequence([photo_failure, _OK_RESPONSE])

    dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    assert [c["url"].rsplit("/", 1)[1] for c in session.post_calls] == ["sendPhoto", "sendMessage"]
    assert _keyboard_of(session.post_calls[1]) == _keyboard_of(session.post_calls[0])


def test_legacy_single_channel_alert_also_uses_buttons(_no_partner_ids):
    session = _FakeSession(response=_OK_RESPONSE)

    send_telegram_alert(_deal_with_hotel(), bot_token="123:ABC", chat_id="42", session=session)

    call = session.post_calls[0]
    assert call["url"].endswith("/sendMessage") and "👉" not in call["data"]["text"]
    assert len(_keyboard_of(call)[0]) == 1 and "web_app" in _keyboard_of(call)[0][0]


def test_unconfigured_printout_still_shows_the_links_as_text(monkeypatch, capsys):
    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", raising=False)
    send_telegram_alert(_deal_with_hotel(), chat_id="42")
    assert "👉" in capsys.readouterr().out


def test_reply_markup_is_valid_json_with_unescaped_umlauts(_no_partner_ids):
    session = _FakeSession(response=_OK_RESPONSE)
    dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    raw = session.post_calls[0]["data"]["reply_markup"]
    assert "👉" in raw and "\\ud83d" not in raw.lower()  # ensure_ascii=False


# --- keyboard fallback chain -----------------------------------------------------

_BUTTON_ERROR = _FakeResponse(
    status_code=400, text='{"ok":false,"error_code":400,"description":"Bad Request: BUTTON_TYPE_INVALID"}'
)


def _kinds(session) -> list[str]:
    """Per call: which button type the (single-button) keyboard used."""
    kinds = []
    for call in session.post_calls:
        markup = json.loads(call["data"].get("reply_markup", "{}") or "{}")
        first = markup["inline_keyboard"][0]
        kinds.append("web_app" if "web_app" in first[0] else "sheet_url" if len(first) == 1 else "direct")
    return kinds


def test_a_channel_rejecting_web_app_buttons_gets_the_sheet_url_button(_no_partner_ids, capsys):
    session = _FakeSessionSequence([_BUTTON_ERROR, _OK_RESPONSE])

    result = dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    assert result is True
    assert [c["url"].rsplit("/", 1)[1] for c in session.post_calls] == ["sendPhoto", "sendPhoto"]  # same message again
    assert _kinds(session) == ["web_app", "sheet_url"]
    out = capsys.readouterr().out
    assert "BUTTON_TYPE_INVALID" not in out  # the expected rejection is not printed as a failure
    assert "Fallback-Variante 2 von 3" in out


def test_both_single_button_variants_rejected_falls_through_to_the_direct_buttons(_no_partner_ids):
    session = _FakeSessionSequence([_BUTTON_ERROR, _BUTTON_ERROR, _OK_RESPONSE])

    assert dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session) is True

    assert _kinds(session) == ["web_app", "sheet_url", "direct"]
    assert len(json.loads(session.post_calls[2]["data"]["reply_markup"])["inline_keyboard"][0]) == 2


def test_a_non_button_error_does_not_walk_the_keyboard_chain(_no_partner_ids):
    photo_failure = _FakeResponse(status_code=400, text='{"ok":false,"description":"Bad Request: wrong file identifier"}')
    session = _FakeSessionSequence([photo_failure, _OK_RESPONSE])

    assert dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session) is True

    # photo failed for a non-button reason -> straight to the text fallback (still web_app first)
    assert [c["url"].rsplit("/", 1)[1] for c in session.post_calls] == ["sendPhoto", "sendMessage"]
    assert _kinds(session) == ["web_app", "web_app"]


def test_text_fallback_also_walks_the_chain(_no_partner_ids):
    photo_failure = _FakeResponse(status_code=400, text='{"ok":false,"description":"Bad Request: wrong file identifier"}')
    session = _FakeSessionSequence([photo_failure, _BUTTON_ERROR, _OK_RESPONSE])

    assert dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session) is True

    assert [c["url"].rsplit("/", 1)[1] for c in session.post_calls] == ["sendPhoto", "sendMessage", "sendMessage"]
    assert _kinds(session) == ["web_app", "web_app", "sheet_url"]


def test_every_keyboard_rejected_returns_false_after_the_text_fallback(_no_partner_ids):
    session = _FakeSessionSequence([_BUTTON_ERROR] * 6)
    assert dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session) is False
    assert len(session.post_calls) == 6  # 3 keyboards x (photo + text)


def test_disabled_sheet_sends_only_the_direct_buttons(monkeypatch, _no_partner_ids):
    monkeypatch.setenv("DEAL_SHEET_URL", "off")
    session = _FakeSession(response=_OK_RESPONSE)

    dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session)

    assert len(session.post_calls) == 1 and _kinds(session) == ["direct"]


def test_first_success_needs_no_retry(_no_partner_ids):
    session = _FakeSession(response=_OK_RESPONSE)
    dispatch_deal_alert(_deal_with_hotel(), bot_token="123:ABC", vip_chat_id="vip-chat", session=session)
    assert len(session.post_calls) == 1 and _kinds(session) == ["web_app"]


def test_the_token_is_never_printed_on_a_button_rejection(_no_partner_ids, capsys):
    session = _FakeSessionSequence([_BUTTON_ERROR] * 6)
    dispatch_deal_alert(_deal_with_hotel(), bot_token="123:SECRET", vip_chat_id="vip-chat", session=session)
    assert "SECRET" not in capsys.readouterr().out
