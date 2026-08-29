"""Runnable demo: Vacation Hunter's own historical price intelligence,
using ONLY local fixture data - no live API, no SerpApi credits.

Seeds a small, realistic price history for one route into the local
SQLite database (data/vacation_hunter.db by default), then evaluates one
current candidate price against it through the existing DealEngine.
Demonstrates OWN_HISTORICAL_BASELINE taking priority over any provider
price insight - see "Historical Price Intelligence" in
docs/PRODUCT_SPEC.md.

Run with:
    python -m vacation_hunter.historical_price_demo

Re-running this is safe and idempotent: seeding uses the same fixed
observation dates each time, so the deduplication in
PriceHistoryRepository.add_observation (see docs/PRODUCT_SPEC.md) prevents
duplicate rows from accumulating - the second run just reads back the
same history instead of doubling it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from vacation_hunter.engine.deal_engine import DealEngine
from vacation_hunter.models import Deal, DealType, FlightOffer, PriceObservation, TripType
from vacation_hunter.price_history_repository import DEFAULT_DB_PATH, PriceHistoryRepository
from vacation_hunter.providers.flight_provider import FlightProvider
from vacation_hunter.providers.null_accommodation_provider import NullAccommodationProvider

_ORIGIN = "HAM"
_DESTINATION = "PMI"
_DEPARTURE_DATE = date(2026, 10, 2)
_RETURN_DATE = date(2026, 10, 7)
_CURRENCY = "EUR"
_PROVIDER = "demo_fixture"

# A small, plausible price history for this route/date pair - the kind of
# data Vacation Hunter would accumulate over time from repeated real
# searches (not automatically wired up yet - see docs/PRODUCT_SPEC.md).
_HISTORICAL_PRICES = [180.0, 175.0, 190.0, 185.0, 178.0, 182.0]

# The offer we're evaluating right now - deliberately far below the
# history above, to demonstrate a clear FLIGHT_DROP.
_CURRENT_PRICE = 119.0


class _SingleOfferFlightProvider(FlightProvider):
    """Returns exactly one given FlightOffer and no baseline/insight of its
    own - used so the demo can show OWN_HISTORICAL_BASELINE (from the
    seeded history) winning over anything a "real" provider might add."""

    def __init__(self, offer: FlightOffer) -> None:
        self._offer = offer

    def search_flights(
        self, origin, destination, earliest_departure, latest_departure, return_date=None
    ) -> list[FlightOffer]:
        return [self._offer]

    def get_typical_price(self, origin, destination, month) -> float | None:
        return None


def _seed_history(repository: PriceHistoryRepository) -> None:
    today = datetime.now(timezone.utc)
    for offset, price in enumerate(_HISTORICAL_PRICES):
        observation = PriceObservation(
            origin=_ORIGIN,
            destination=_DESTINATION,
            departure_date=_DEPARTURE_DATE,
            return_date=_RETURN_DATE,
            trip_type=TripType.ROUND_TRIP,
            price=price,
            currency=_CURRENCY,
            provider=_PROVIDER,
            stops=0,
            airline="Eurowings",
            observed_at=today - timedelta(days=len(_HISTORICAL_PRICES) - offset),
        )
        repository.add_observation(observation)


def run() -> Deal:
    repository = PriceHistoryRepository(db_path=DEFAULT_DB_PATH)
    _seed_history(repository)

    current_offer = FlightOffer(
        origin=_ORIGIN,
        destination=_DESTINATION,
        departure_date=_DEPARTURE_DATE,
        return_date=_RETURN_DATE,
        price=_CURRENT_PRICE,
        currency=_CURRENCY,
        airline="Eurowings",
        stops=0,
        provider=_PROVIDER,
    )

    engine = DealEngine(
        flight_provider=_SingleOfferFlightProvider(current_offer),
        accommodation_provider=NullAccommodationProvider(),
        price_history_repository=repository,
    )

    deals = engine.find_trip_deals(
        origin=_ORIGIN,
        destination=_DESTINATION,
        earliest_departure=_DEPARTURE_DATE,
        latest_departure=_DEPARTURE_DATE,
        return_date=_RETURN_DATE,
    )
    deal = deals[0]

    print("HISTORICAL PRICE INTELLIGENCE")
    print()
    print(f"{_ORIGIN} → {_DESTINATION}")

    baseline = deal.historical_baseline
    if baseline is not None:
        stats = baseline.statistics
        print(f"Observations: {stats.observation_count}")
        print(f"Median: {stats.median:.0f} {_CURRENCY}")
        print(f"Typical range: {stats.p25:.0f}–{stats.p75:.0f} {_CURRENCY}")
        print(f"Current price: {_CURRENT_PRICE:.0f} {_CURRENCY}")
        print(f"Difference from median: {baseline.percent_diff_from_median:.0f} %")
        print(f"Historical position: {baseline.position.value}")
    else:
        print("Not enough history yet for a baseline.")

    print()
    print("Baseline source:")
    print(deal.baseline_source.value)
    print()
    print("Vacation Hunter assessment:")
    print(deal.deal_type.value)

    return deal


if __name__ == "__main__":
    run()
