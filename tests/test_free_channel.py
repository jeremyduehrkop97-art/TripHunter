"""Free channel: teaser vs delayed_full, the delay queue and the funnel
guarantees (no booking links to Free; VIP keeps the direct deal button)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from trip_hunter.daily_sampler import run_sampler
from trip_hunter.dispatch.telegram import dispatch_deal_alert, flush_free_queue
from trip_hunter.free_queue_repository import MAX_QUEUE_AGE, FreeQueueRepository
from trip_hunter.models import AccommodationOffer, Deal, DealScore, DealType, FlightOffer

_NOW = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
_DEP, _RET = date(2026, 10, 2), date(2026, 10, 4)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("VIP_SUBSCRIPTION_URL", "TELEGRAM_BOT_USERNAME", "FAQ_URL", "FREE_CHANNEL_MODE",
                 "FREE_CHANNEL_DELAY_HOURS", "BOOKING_AFFILIATE_ID", "TRAVELPAYOUTS_MARKER",
                 "DEAL_SHEET_URL", "FLIGHT_LINK_PROVIDER", "HOTEL_LINK_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


def _deal(deal_type=DealType.FLIGHT_DROP, savings=0.45, price=79.0) -> Deal:
    flight = FlightOffer(
        origin="HAM", destination="PMI", departure_date=_DEP, return_date=_RET, price=price, currency="EUR",
        airline="Eurowings", stops=0, provider="test", booking_link="https://example.com/book/flight",
    )
    hotel = AccommodationOffer(
        destination="PMI", check_in=_DEP, check_out=_RET, total_price=90.0, currency="EUR",
        name="Hotel Secretissimo", rating=4.3, provider="test", booking_link="https://example.com/book/hotel",
    )
    return Deal(
        deal_type=deal_type, flight=flight, accommodation=hotel, expected_flight_price=140.0,
        expected_accommodation_price=150.0, score=DealScore(total=80, breakdown={}),
        savings_absolute=61.0, savings_percentage=savings,
    )


class _Resp:
    status_code = 200
    text = ""

    def json(self):
        return {"ok": True}


class _Fail(_Resp):
    status_code = 500
    text = "boom"


class _Session:
    def __init__(self, responses=None):
        self.calls, self._responses = [], list(responses or [])

    def post(self, url, data=None, timeout=None):
        self.calls.append({"method": url.rsplit("/", 1)[1], "data": data})
        return self._responses.pop(0) if self._responses else _Resp()


def _send(deal, session, **kw):
    return dispatch_deal_alert(
        deal, bot_token="123:ABC", free_chat_id="free", vip_chat_id="vip", session=session, **kw
    )


def _by_chat(session):
    return {c["data"]["chat_id"]: c for c in session.calls}


def _rows(call):
    return json.loads(call["data"]["reply_markup"])["inline_keyboard"]


# --- teaser mode (default) ----------------------------------------------------------


def test_free_alert_contains_no_direct_booking_deeplinks():
    session = _Session()
    _send(_deal(), session)
    free = _by_chat(session)["free"]
    payload = free["data"]["caption"] + free["data"]["reply_markup"]

    for forbidden in ("booking.com", "google.com/travel", "example.com/book", "aviasales", "skyscanner",
                      "tp.media", "deal.html", "Hotel Secretissimo"):
        assert forbidden not in payload, forbidden


def test_free_buttons_link_to_the_vip_upsell_and_the_explainer(monkeypatch):
    monkeypatch.setenv("VIP_SUBSCRIPTION_URL", "https://buy.stripe.com/vip")
    monkeypatch.setenv("FAQ_URL", "https://example.org/how")
    session = _Session()
    _send(_deal(), session)

    rows = _rows(_by_chat(session)["free"])

    assert rows == [
        [{"text": "⚡️ Jetzt Deal buchen (VIP freischalten)", "url": "https://buy.stripe.com/vip"}],
        [{"text": "ℹ️ Wie funktioniert Trip Hunter?", "url": "https://example.org/how"}],
    ]


def test_free_teaser_shows_only_the_rough_period_and_blurs_the_photo():
    session = _Session()
    _send(_deal(), session)
    free = _by_chat(session)["free"]

    assert "Oktober 2026, 2 Nächte" in free["data"]["caption"] and "02.10.2026" not in free["data"]["caption"]
    assert free["method"] == "sendPhoto" and free["data"]["has_spoiler"] == "true"


def test_vip_still_gets_the_direct_deal_sheet_button_and_full_details():
    session = _Session()
    _send(_deal(), session)
    vip = _by_chat(session)["vip"]

    (button,) = _rows(vip)[0]
    assert button["text"].startswith("👉 Deal sichern (") and "deal.html?" in button["web_app"]["url"]
    assert "Hotel Secretissimo" in vip["data"]["caption"] and "02.10.2026" in vip["data"]["caption"]
    assert "has_spoiler" not in vip["data"]


def test_vip_and_free_are_sent_immediately_in_teaser_mode(tmp_path):
    queue = FreeQueueRepository(tmp_path / "q.db")
    session = _Session()

    assert _send(_deal(), session, free_queue=queue) is True

    assert set(_by_chat(session)) == {"vip", "free"} and queue.pending_count() == 0


def test_tier_3_deals_never_reach_free_in_either_mode(monkeypatch, tmp_path):
    for mode in ("teaser", "delayed_full"):
        monkeypatch.setenv("FREE_CHANNEL_MODE", mode)
        queue = FreeQueueRepository(tmp_path / f"{mode}.db")
        session = _Session()
        _send(_deal(deal_type=DealType.HOTEL_DROP, savings=0.2), session, free_queue=queue)
        assert set(_by_chat(session)) == {"vip"} and queue.pending_count() == 0


# --- delayed_full mode ------------------------------------------------------------


def test_delayed_full_sends_vip_now_and_queues_free(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_CHANNEL_MODE", "delayed_full")
    monkeypatch.setenv("FREE_CHANNEL_DELAY_HOURS", "12")
    queue = FreeQueueRepository(tmp_path / "q.db")
    session = _Session()

    result = _send(_deal(), session, free_queue=queue, now=_NOW)

    assert result is True and set(_by_chat(session)) == {"vip"}  # Free: nothing sent yet
    assert queue.pending_count() == 1
    assert queue.due(_NOW + timedelta(hours=11, minutes=59)) == []
    (item,) = queue.due(_NOW + timedelta(hours=12))
    assert item.due_at == _NOW + timedelta(hours=12)


def test_queued_item_is_the_complete_alert_with_the_note(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_CHANNEL_MODE", "delayed_full")
    queue = FreeQueueRepository(tmp_path / "q.db")
    _send(_deal(), _Session(), free_queue=queue, now=_NOW)

    (item,) = queue.due(_NOW + timedelta(hours=24))

    assert item.text.startswith("⏱ Dieser Deal ging vor 24 Std. an den VIP-Kanal")
    assert "Hotel Secretissimo" in item.text and "02.10.2026" in item.text
    assert "web_app" in item.keyboards[0]["inline_keyboard"][0][0]
    assert item.photo_url.startswith("https://images.unsplash.com/")


def test_flush_before_the_delay_sends_nothing_and_makes_no_request(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_CHANNEL_MODE", "delayed_full")
    queue = FreeQueueRepository(tmp_path / "q.db")
    _send(_deal(), _Session(), free_queue=queue, now=_NOW)
    session = _Session()

    sent = flush_free_queue("123:ABC", "free", queue=queue, now=_NOW + timedelta(hours=23), session=session)

    assert sent == 0 and session.calls == [] and queue.pending_count() == 1


def test_flush_after_the_delay_sends_the_full_alert_to_free_once(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_CHANNEL_MODE", "delayed_full")
    queue = FreeQueueRepository(tmp_path / "q.db")
    _send(_deal(), _Session(), free_queue=queue, now=_NOW)
    session = _Session()
    later = _NOW + timedelta(hours=30)

    assert flush_free_queue("123:ABC", "free", queue=queue, now=later, session=session) == 1
    assert flush_free_queue("123:ABC", "free", queue=queue, now=later, session=session) == 0  # not again

    (call,) = session.calls
    assert call["method"] == "sendPhoto" and call["data"]["chat_id"] == "free"
    assert "has_spoiler" not in call["data"]  # a delayed alert is no teaser: clear photo
    assert "Hotel Secretissimo" in call["data"]["caption"] and "⏱" in call["data"]["caption"]
    assert queue.pending_count() == 0


def test_a_failed_send_stays_queued_for_the_next_flush(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_CHANNEL_MODE", "delayed_full")
    queue = FreeQueueRepository(tmp_path / "q.db")
    _send(_deal(), _Session(), free_queue=queue, now=_NOW)
    later = _NOW + timedelta(hours=30)

    assert flush_free_queue("123:ABC", "free", queue=queue, now=later, session=_Session([_Fail(), _Fail()])) == 0
    assert queue.pending_count() == 1
    assert flush_free_queue("123:ABC", "free", queue=queue, now=later, session=_Session()) == 1


def test_items_older_than_a_week_expire_instead_of_being_sent(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_CHANNEL_MODE", "delayed_full")
    queue = FreeQueueRepository(tmp_path / "q.db")
    _send(_deal(), _Session(), free_queue=queue, now=_NOW)
    session = _Session()

    sent = flush_free_queue("123:ABC", "free", queue=queue, now=_NOW + MAX_QUEUE_AGE + timedelta(hours=1), session=session)

    assert sent == 0 and session.calls == [] and queue.pending_count() == 0


def test_flush_without_token_or_free_channel_does_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_FREE_CHAT_ID", raising=False)
    queue = FreeQueueRepository(tmp_path / "q.db")
    queue.enqueue(text="x", photo_url=None, keyboards=[], due_at=_NOW, now=_NOW)

    assert flush_free_queue(queue=queue, now=_NOW + timedelta(days=1)) == 0
    assert flush_free_queue("123:ABC", None, queue=queue, now=_NOW + timedelta(days=1)) == 0
    assert queue.pending_count() == 1


def test_queue_survives_a_new_repository_instance_and_keeps_order(tmp_path):
    first = FreeQueueRepository(tmp_path / "q.db")
    first.enqueue(text="second", photo_url=None, keyboards=[], due_at=_NOW + timedelta(hours=2), now=_NOW)
    first.enqueue(text="first", photo_url="https://images.unsplash.com/x", keyboards=[{"inline_keyboard": []}], due_at=_NOW + timedelta(hours=1), now=_NOW)

    items = FreeQueueRepository(tmp_path / "q.db").due(_NOW + timedelta(hours=3))

    assert [i.text for i in items] == ["first", "second"]
    assert items[0].keyboards == [{"inline_keyboard": []}] and items[1].photo_url is None


# --- sampler hook ------------------------------------------------------------------


def _run_sampler(**kw):
    from trip_hunter.accommodation_price_history_repository import AccommodationPriceHistoryRepository
    from trip_hunter.caching import FileCache
    from trip_hunter.price_history_repository import PriceHistoryRepository
    from trip_hunter.providers.null_accommodation_provider import NullAccommodationProvider
    from trip_hunter.providers.flight_provider import FlightProvider

    class _NoFlights(FlightProvider):
        def search_flights(self, *a, **k):
            return []

        def get_typical_price(self, *a):
            return None

        def get_price_insight(self, *a):
            return None

    tmp = kw.pop("tmp")
    return run_sampler(
        _NoFlights(), NullAccommodationProvider(), PriceHistoryRepository(tmp / "f.db"),
        AccommodationPriceHistoryRepository(tmp / "h.db"), FileCache(cache_dir=tmp / "c"),
        flight_targets=[], hotel_targets=[], today=date(2026, 9, 25), observed_at=_NOW, **kw,
    )


def test_sampler_flushes_the_queue_at_the_end_of_a_run(tmp_path):
    calls = []
    _run_sampler(tmp=tmp_path, flush_fn=lambda: calls.append("flushed"))
    assert calls == ["flushed"]


def test_sampler_does_not_flush_in_dry_run_or_without_alerts(tmp_path):
    calls = []
    _run_sampler(tmp=tmp_path, flush_fn=lambda: calls.append(1), dry_run=True)
    _run_sampler(tmp=tmp_path, flush_fn=lambda: calls.append(2), send_alerts=False)
    assert calls == []
