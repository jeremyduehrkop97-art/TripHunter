"""Tests for dispatch/telegram.py. All HTTP is faked via a fake session -
no real network calls anywhere in this file.
"""

from __future__ import annotations

from datetime import date

import pytest
import requests

from trip_hunter.dispatch.telegram import get_bot_token, get_chat_id, send_telegram_alert
from trip_hunter.models import AccommodationOffer, Deal, DealScore, DealType, FlightOffer

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_CHAT_ID", raising=False)


def _deal(*, deal_type: DealType = DealType.FLIGHT_DROP) -> Deal:
    flight = FlightOffer(
        origin="HAM", destination="PMI", departure_date=_FRI, return_date=_SUN,
        price=79.0, currency="EUR", airline="Eurowings", stops=0, provider="test",
        booking_link="https://example.com/book/flight",
    )
    return Deal(
        deal_type=deal_type, flight=flight, accommodation=None,
        expected_flight_price=140.0, expected_accommodation_price=None,
        score=DealScore(total=70, breakdown={}),
        savings_absolute=61.0, savings_percentage=0.436,
    )


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
    assert "FLIGHT DROP" in captured  # the fallback message itself


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
    assert "FLIGHT DROP" in call["data"]["text"]
    assert "HAM" in call["data"]["text"] and "PMI" in call["data"]["text"]
    assert call["timeout"] == 10.0


def test_message_text_matches_instant_alert_formatter():
    from trip_hunter.alerts.instant_alert_formatter import format_instant_alert

    deal = _deal()
    session = _FakeSession(response=_FakeResponse(status_code=200, json_data={"ok": True}))

    send_telegram_alert(deal, bot_token="123:ABC", chat_id="42", session=session)

    assert session.post_calls[0]["data"]["text"] == format_instant_alert(deal)


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
