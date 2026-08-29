"""Runnable demo: Vacation Hunter's own historical price intelligence,
using ONLY local fixture data - no live API, no SerpApi credits.

Simulates several days of search snapshots (each with multiple competing
FlightOffers, like a real search would return), reduces each snapshot to
its one cheapest comparable observation via
observation_from_search_results(...) against an EXPLICITLY defined
FlightComparisonGroup, stores those into the local SQLite database
(data/vacation_hunter.db by default), then evaluates one current candidate
price against the resulting history through the existing DealEngine.
Demonstrates three things at once:
- The caller states which travel dates/route/currency are comparable up
  front (FlightComparisonGroup) - Vacation Hunter never infers that from
  which group of offers happened to be biggest (see "Explicit Comparison
  Groups" in docs/PRODUCT_SPEC.md, MVP 0.3.2).
- One search snapshot = at most one market-price observation for that
  group (see "Observation Semantics", MVP 0.3.1) - NOT one observation
  per offer.
- OWN_HISTORICAL_BASELINE taking priority over any provider price insight.

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
from vacation_hunter.models import Deal, DealType, FlightComparisonGroup, FlightOffer, TripType
from vacation_hunter.price_history_repository import (
    DEFAULT_DB_PATH,
    PriceHistoryRepository,
    observation_from_search_results,
)
from vacation_hunter.providers.flight_provider import FlightProvider
from vacation_hunter.providers.null_accommodation_provider import NullAccommodationProvider

_ORIGIN = "HAM"
_DESTINATION = "PMI"
_DEPARTURE_DATE = date(2026, 10, 2)
_RETURN_DATE = date(2026, 10, 7)
_CURRENCY = "EUR"
_PROVIDER = "demo_fixture"

# The caller states up front which travel dates/route/currency count as
# "comparable" - Vacation Hunter never guesses this from result counts.
# See "Explicit Comparison Groups" in docs/PRODUCT_SPEC.md.
_COMPARISON_GROUP = FlightComparisonGroup(
    origin=_ORIGIN,
    destination=_DESTINATION,
    departure_date=_DEPARTURE_DATE,
    return_date=_RETURN_DATE,
    trip_type=TripType.ROUND_TRIP,
    currency=_CURRENCY,
)

# Six simulated days of search snapshots. Each inner list is what ONE
# search returned that day (several competing offers, like a real
# provider would) - only the cheapest of each snapshot becomes history.
# The cheapest-per-day values (180/175/190/185/178/182) match what earlier
# demo versions stored directly - the resulting baseline is unchanged, only
# how we arrive at it is now shown explicitly.
_DAILY_SEARCH_SNAPSHOTS = [
    [180.0, 205.0, 227.0],
    [175.0, 199.0],
    [190.0, 210.0, 240.0],
    [185.0, 220.0],
    [178.0, 195.0, 208.0],
    [182.0, 200.0],
]

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


def _snapshot_offer(price: float) -> FlightOffer:
    return FlightOffer(
        origin=_ORIGIN,
        destination=_DESTINATION,
        departure_date=_DEPARTURE_DATE,
        return_date=_RETURN_DATE,
        price=price,
        currency=_CURRENCY,
        airline="Eurowings",
        stops=0,
        provider=_PROVIDER,
    )


def _print_comparison_group() -> None:
    print("Comparison Group:")
    print(f"{_ORIGIN} → {_DESTINATION}")
    print(f"Departure: {_DEPARTURE_DATE}")
    print(f"Return: {_RETURN_DATE}")
    print(f"Currency: {_CURRENCY}")
    print("Mode: cheapest_any")
    print()


def _seed_history(repository: PriceHistoryRepository) -> None:
    today = datetime.now(timezone.utc)
    total_days = len(_DAILY_SEARCH_SNAPSHOTS)

    print("Simulated daily search snapshots:")
    print()
    for offset, snapshot_prices in enumerate(_DAILY_SEARCH_SNAPSHOTS):
        observed_at = today - timedelta(days=total_days - offset)
        offers = [_snapshot_offer(price) for price in snapshot_prices]

        print(f"Search Snapshot ({observed_at.date()}): {' / '.join(f'{p:.0f}' for p in snapshot_prices)}")

        # One search snapshot -> at most one observation, for the
        # EXPLICIT comparison group above - never guessed from which
        # group of offers happened to be biggest. See "Explicit
        # Comparison Groups" in docs/PRODUCT_SPEC.md.
        observation = observation_from_search_results(
            offers, _COMPARISON_GROUP, observed_at=observed_at
        )
        assert observation is not None  # snapshot fixtures always match the group
        repository.add_observation(observation)

        print(f"Stored observation: {observation.price:.0f} {_CURRENCY}")
        print()


def run() -> Deal:
    repository = PriceHistoryRepository(db_path=DEFAULT_DB_PATH)
    _print_comparison_group()
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
