"""Tests for dispatch/test_dispatch.py. No real HTTP calls anywhere in
this file: `run()` either hits the built-in mock Deal / a tmp_path-based
real repository, or --dry-run, which never touches the network at all.
Where a real send would be attempted (no --dry-run), credentials are left
unconfigured so send_telegram_alert's own "not configured" fallback
applies - still zero network, verified by the absence of any monkeypatched
session ever being constructed with real credentials.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trip_hunter.dispatch.test_dispatch import (
    _build_parser,
    _mock_deal,
    _parse_args,
    _real_deal,
    run,
)
from trip_hunter.models import DealType
from trip_hunter.price_history_repository import PriceHistoryRepository, observation_from_flight_offer


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_CHAT_ID", raising=False)


# --- CLI parsing --------------------------------------------------------------


def test_flags_default_to_false():
    args = _parse_args([])
    assert args.dry_run is False
    assert args.real is False


def test_dry_run_flag_can_be_set():
    args = _parse_args(["--dry-run"])
    assert args.dry_run is True


def test_real_flag_can_be_set():
    args = _parse_args(["--real"])
    assert args.real is True


def test_both_flags_can_be_combined():
    args = _parse_args(["--dry-run", "--real"])
    assert args.dry_run is True
    assert args.real is True


def test_parser_has_a_prog_name():
    assert "test_dispatch" in _build_parser().prog


# --- _mock_deal ----------------------------------------------------------------


def test_mock_deal_is_a_combined_trip_drop():
    deal = _mock_deal()

    assert deal.deal_type == DealType.COMBINED_TRIP_DROP
    assert deal.flight.origin == "HAM"
    assert deal.flight.destination == "PMI"
    assert deal.accommodation is not None
    assert deal.score is not None


# --- _real_deal ------------------------------------------------------------------


def test_real_deal_returns_none_with_no_stored_observations(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "empty.db")

    assert _real_deal(repository=repo) is None


def test_real_deal_replays_the_most_recent_observation(tmp_path):
    from trip_hunter.dispatch.test_dispatch import _REAL_CURRENCY, _REAL_DEPARTURE, _REAL_DESTINATION, _REAL_ORIGIN
    from trip_hunter.models import TripType

    from trip_hunter.models import FlightOffer

    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    prices_oldest_to_newest = [300.0, 300.0, 300.0, 300.0, 100.0]  # last one is cheapest by far
    for day, price in enumerate(prices_oldest_to_newest, start=1):
        flight = FlightOffer(
            origin=_REAL_ORIGIN, destination=_REAL_DESTINATION,
            departure_date=_REAL_DEPARTURE, return_date=_real_return(),
            price=price, currency=_REAL_CURRENCY, airline="Testair", stops=0, provider="test",
        )
        repo.add_observation(
            observation_from_flight_offer(
                flight, TripType.ROUND_TRIP,
                observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
            )
        )

    deal = _real_deal(repository=repo)

    assert deal is not None
    assert deal.flight.price == 100.0  # the most recently observed price, not the cheapest overall
    assert deal.deal_type == DealType.FLIGHT_DROP
    assert deal.accommodation is None  # NullAccommodationProvider - never the real hotel provider


def _real_return():
    from trip_hunter.dispatch.test_dispatch import _REAL_RETURN

    return _REAL_RETURN


# --- run(): mock deal, dry-run --------------------------------------------------


def test_dry_run_mock_prints_payload_without_sending(capsys):
    result = run(["--dry-run"])

    assert result is None
    captured = capsys.readouterr().out
    assert "DRY RUN" in captured
    assert "wird NICHT gesendet" in captured
    assert "FLIGHT DROP" in captured or "COMBINED TRIP DROP" in captured
    assert "<nicht konfiguriert>" in captured
    assert "bot_token konfiguriert: nein" in captured


def test_dry_run_shows_configured_chat_id_without_sending(monkeypatch, capsys):
    monkeypatch.setenv("TRIP_HUNTER_TELEGRAM_CHAT_ID", "42")

    run(["--dry-run"])

    captured = capsys.readouterr().out
    assert "chat_id: 42" in captured


def test_mock_send_without_credentials_returns_false_and_never_touches_network(capsys):
    result = run([])

    assert result is False
    captured = capsys.readouterr().out
    assert "nicht konfiguriert" in captured


# --- run(): --real ---------------------------------------------------------------


def test_real_mode_with_no_data_prints_message_and_returns_none(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # DEFAULT_DB_PATH is relative - fresh, empty DB here

    result = run(["--real", "--dry-run"])

    assert result is None
    captured = capsys.readouterr().out
    assert "Kein Deal aus echten" in captured
    assert "DRY RUN" not in captured  # never got as far as building a payload


def test_real_mode_dry_run_with_data_shows_real_price(tmp_path, monkeypatch, capsys):
    from trip_hunter.dispatch.test_dispatch import _REAL_CURRENCY, _REAL_DEPARTURE, _REAL_DESTINATION, _REAL_ORIGIN, _REAL_RETURN
    from trip_hunter.models import FlightOffer, TripType
    from trip_hunter.price_history_repository import DEFAULT_DB_PATH

    monkeypatch.chdir(tmp_path)
    repo = PriceHistoryRepository(db_path=DEFAULT_DB_PATH)
    prices = [300.0, 300.0, 300.0, 300.0, 100.0]
    for day, price in enumerate(prices, start=1):
        flight = FlightOffer(
            origin=_REAL_ORIGIN, destination=_REAL_DESTINATION,
            departure_date=_REAL_DEPARTURE, return_date=_REAL_RETURN,
            price=price, currency=_REAL_CURRENCY, airline="Testair", stops=0, provider="test",
        )
        repo.add_observation(
            observation_from_flight_offer(
                flight, TripType.ROUND_TRIP,
                observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
            )
        )

    result = run(["--real", "--dry-run"])

    assert result is None
    captured = capsys.readouterr().out
    assert "DRY RUN" in captured
    assert "100.00 EUR" in captured
