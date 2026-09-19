"""Simple helper to test the Telegram dispatch channel: sends either a
built-in mock Deal (default) or a Deal built from already-stored real
data (--real), useful to verify TRIP_HUNTER_TELEGRAM_BOT_TOKEN/_CHAT_ID
are configured correctly without needing a live SerpApi search.

Run with:
    python -m trip_hunter.dispatch.test_dispatch              # sends the mock deal
    python -m trip_hunter.dispatch.test_dispatch --dry-run     # prints the payload, sends nothing
    python -m trip_hunter.dispatch.test_dispatch --real         # uses real stored data instead of the mock
    python -m trip_hunter.dispatch.test_dispatch --real --dry-run

--real makes ZERO API calls, regardless of the SerpApi cache's state: it
replays the last stored real flight PriceObservation (HAM->PMI, the same
route sampling_targets.py already tracks) against the real flight
baseline, using NullAccommodationProvider - never the real hotel provider
- so this never depends on (or risks) the hotel cache. It may honestly
find no deal (the same finding this project's end-to-end verification run
already documented: one of the observations that makes up a baseline
rarely reads as an outlier against that very baseline) - this prints a
clear message and sends nothing rather than fabricating one.

Named test_dispatch.py (not e.g. dispatch_check.py) because
"python -m trip_hunter.dispatch.test_dispatch" reads clearly as what it
is; pytest never collects it by accident since pyproject.toml's
testpaths is scoped to tests/ only, not src/.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from trip_hunter.alerts.instant_alert_formatter import format_instant_alert
from trip_hunter.dispatch import telegram
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.models import (
    AccommodationOffer,
    Deal,
    DealScore,
    DealType,
    FlightOffer,
    TripType,
)
from trip_hunter.price_history_repository import DEFAULT_DB_PATH as FLIGHT_DB_PATH
from trip_hunter.price_history_repository import PriceHistoryRepository
from trip_hunter.providers.flight_provider import FlightProvider
from trip_hunter.providers.null_accommodation_provider import NullAccommodationProvider

# The one real, currently-tracked route with real flight history - see
# sampling_targets.py.
_REAL_ORIGIN, _REAL_DESTINATION = "HAM", "PMI"
_REAL_DEPARTURE, _REAL_RETURN = date(2026, 10, 2), date(2026, 10, 7)
_REAL_CURRENCY = "EUR"


def _mock_deal() -> Deal:
    """The canonical docs/PRODUCT_SPEC.md example scenario - always
    available, no dependency on any stored or live data."""
    flight = FlightOffer(
        origin="HAM", destination="PMI", departure_date=date(2026, 10, 2), return_date=date(2026, 10, 7),
        price=79.0, currency="EUR", airline="Eurowings", stops=0, provider="mock_test_dispatch",
        booking_link="https://example.com/book/flight-mock",
    )
    accommodation = AccommodationOffer(
        destination="PMI", check_in=date(2026, 10, 2), check_out=date(2026, 10, 7),
        total_price=205.0, currency="EUR", name="Hotel Playa Sol", rating=4.2,
        provider="mock_test_dispatch", booking_link="https://example.com/book/hotel-mock",
    )
    return Deal(
        deal_type=DealType.COMBINED_TRIP_DROP,
        flight=flight,
        accommodation=accommodation,
        expected_flight_price=180.0,
        expected_accommodation_price=340.0,
        score=DealScore(total=88, breakdown={}),
        savings_absolute=236.0,
        savings_percentage=0.4538,
    )


class _ReplayFlightProvider(FlightProvider):
    """Zero-network stand-in: replays an already-stored PriceObservation as
    today's FlightOffer. get_typical_price mirrors the real
    SerpApiGoogleFlightsProvider (always None), so DealEngine falls back
    to the real repository-based baseline exactly like production does.
    """

    def __init__(self, observation):
        self._observation = observation

    def search_flights(self, origin, destination, earliest_departure, latest_departure, return_date=None):
        o = self._observation
        return [
            FlightOffer(
                origin=o.origin, destination=o.destination,
                departure_date=o.departure_date, return_date=o.return_date,
                price=o.price, currency=o.currency,
                airline=o.airline or "Unknown", stops=o.stops,
                provider=o.provider, price_confirmed_complete=True,
            )
        ]

    def get_typical_price(self, origin, destination, month):
        return None

    def get_price_insight(self, origin, destination, departure_date, return_date):
        return None


def _real_deal(repository: PriceHistoryRepository | None = None) -> Deal | None:
    """Builds a Deal from already-stored real flight data only - 0 network
    calls, regardless of any cache's state (uses NullAccommodationProvider,
    never the real hotel provider). Returns None if there's no stored
    observation to replay, or if the replayed price doesn't clear the
    flight baseline (an honest "no deal", not a fabricated one).
    """
    repo = repository or PriceHistoryRepository(db_path=FLIGHT_DB_PATH)
    observations = repo.get_observations(
        _REAL_ORIGIN, _REAL_DESTINATION, _REAL_DEPARTURE, _REAL_RETURN, TripType.ROUND_TRIP, _REAL_CURRENCY
    )
    if not observations:
        return None

    last_observation = max(observations, key=lambda o: o.observed_at)
    engine = DealEngine(
        flight_provider=_ReplayFlightProvider(last_observation),
        accommodation_provider=NullAccommodationProvider(),
        price_history_repository=repo,
    )
    deals = engine.find_trip_deals(
        origin=_REAL_ORIGIN, destination=_REAL_DESTINATION,
        earliest_departure=_REAL_DEPARTURE, latest_departure=_REAL_DEPARTURE, return_date=_REAL_RETURN,
    )
    return deals[0] if deals else None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trip_hunter.dispatch.test_dispatch",
        description="Send a test alert (mock or real-data-based) to the configured Telegram channel.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the Telegram payload instead of sending it."
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use a Deal built from real stored data (0 API calls) instead of the built-in mock Deal.",
    )
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def run(argv: list[str] | None = None) -> bool | None:
    """Returns the underlying send_telegram_alert(...) result (True/False),
    or None if there was nothing to send (--real found no deal) or nothing
    was sent (--dry-run).
    """
    args = _parse_args(argv)

    if args.real:
        deal = _real_deal()
        if deal is None:
            print(
                "Kein Deal aus echten, gespeicherten Daten verfügbar "
                "(keine Beobachtung vorhanden, oder der letzte Preis liegt "
                "nicht unter der eigenen Baseline) - nichts zu senden."
            )
            return None
    else:
        deal = _mock_deal()

    if args.dry_run:
        token_configured = telegram.get_bot_token() is not None
        chat_id = telegram.get_chat_id()
        message = format_instant_alert(deal)
        print("DRY RUN - Telegram-Payload (wird NICHT gesendet):")
        print(f"  chat_id: {chat_id if chat_id else '<nicht konfiguriert>'}")
        print(f"  bot_token konfiguriert: {'ja' if token_configured else 'nein'}")
        print("  text:")
        print(message)
        return None

    return telegram.send_telegram_alert(deal)


if __name__ == "__main__":
    run()
