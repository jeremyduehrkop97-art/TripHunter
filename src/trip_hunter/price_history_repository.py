"""Local SQLite storage for our own observed flight prices.

Why SQLite for MVP 0.3: local, a single file, no server/infrastructure to
run, robust, and trivially migratable to a real database server later if
volume ever demands it. This is the ONLY place in the codebase that writes
SQL - the rest of the system talks to PriceHistoryRepository's methods, not
to the database directly. See "Historical Price Intelligence" in
docs/PRODUCT_SPEC.md.

Deduplication: adding the exact same observation (same route, dates,
currency, provider, price) again on the same calendar day is a no-op -
see `add_observation`. This keeps repeated reads of an already-cached
search from inflating history with redundant rows, without needing an
event-sourcing setup.

The database file itself (default: data/trip_hunter.db) is local
runtime state, not project data, and is git-ignored - same reasoning as the
existing data/cache/ (see caching.py).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from trip_hunter.models import (
    FlightComparisonGroup,
    FlightOffer,
    PriceObservation,
    PriceStatistics,
    TripType,
)

DEFAULT_DB_PATH = Path("data/trip_hunter.db")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    return_date TEXT NOT NULL,
    trip_type TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    provider TEXT NOT NULL,
    stops INTEGER NOT NULL,
    airline TEXT,
    cabin_class TEXT,
    observed_at TEXT NOT NULL,
    observed_date TEXT NOT NULL,
    UNIQUE (
        origin, destination, departure_date, return_date, trip_type,
        currency, provider, price, observed_date
    )
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_price_observations_route
    ON price_observations (origin, destination, departure_date, return_date, trip_type, currency)
"""

_INSERT_SQL = """
INSERT OR IGNORE INTO price_observations
    (origin, destination, departure_date, return_date, trip_type,
     price, currency, provider, stops, airline, cabin_class,
     observed_at, observed_date)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_SQL = """
SELECT origin, destination, departure_date, return_date, trip_type,
       price, currency, provider, stops, airline, cabin_class, observed_at
FROM price_observations
WHERE origin = ? AND destination = ? AND departure_date = ? AND return_date = ?
      AND trip_type = ? AND currency = ?
ORDER BY observed_at
"""


class PriceHistoryRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_CREATE_TABLE_SQL)
            connection.execute(_CREATE_INDEX_SQL)

    def add_observation(self, observation: PriceObservation) -> bool:
        """Store one observation. Returns True if it was newly inserted,
        False if an equivalent observation (same route/dates/trip type/
        currency/provider/price, same calendar day) already existed.
        """
        observed_date = observation.observed_at.date().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                _INSERT_SQL,
                (
                    observation.origin,
                    observation.destination,
                    observation.departure_date.isoformat(),
                    observation.return_date.isoformat(),
                    observation.trip_type.value,
                    observation.price,
                    observation.currency,
                    observation.provider,
                    observation.stops,
                    observation.airline,
                    observation.cabin_class,
                    observation.observed_at.isoformat(),
                    observed_date,
                ),
            )
            return cursor.rowcount > 0

    def get_observations(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date,
        trip_type: TripType,
        currency: str,
    ) -> list[PriceObservation]:
        """Return all observations for one specific, strict comparison
        group: same route, same exact departure/return dates, same trip
        type, same currency. See "Route Baseline" in docs/PRODUCT_SPEC.md
        for why this MVP grouping is deliberately strict.
        """
        with self._connect() as connection:
            rows = connection.execute(
                _SELECT_SQL,
                (
                    origin,
                    destination,
                    departure_date.isoformat(),
                    return_date.isoformat(),
                    trip_type.value,
                    currency,
                ),
            ).fetchall()
        return [_row_to_observation(row) for row in rows]

    def get_route_statistics(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date,
        trip_type: TripType,
        currency: str,
    ) -> PriceStatistics | None:
        """Convenience: observations for this group, summarized. Returns
        None if there are no observations at all - callers that need a
        minimum-data policy (see MIN_HISTORY_OBSERVATIONS in
        engine/price_statistics.py) must check observation_count themselves;
        this method has no opinion on what counts as "enough".
        """
        # Imported here, not at module level, to keep this storage module
        # from depending on engine/ - engine depends on storage, not the
        # other way around.
        from trip_hunter.engine.price_statistics import compute_statistics

        observations = self.get_observations(
            origin, destination, departure_date, return_date, trip_type, currency
        )
        if not observations:
            return None
        return compute_statistics([observation.price for observation in observations])

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)


def _row_to_observation(row: tuple) -> PriceObservation:
    (
        origin,
        destination,
        departure_date_str,
        return_date_str,
        trip_type_str,
        price,
        currency,
        provider,
        stops,
        airline,
        cabin_class,
        observed_at_str,
    ) = row
    return PriceObservation(
        origin=origin,
        destination=destination,
        departure_date=date.fromisoformat(departure_date_str),
        return_date=date.fromisoformat(return_date_str),
        trip_type=TripType(trip_type_str),
        price=price,
        currency=currency,
        provider=provider,
        stops=stops,
        airline=airline,
        cabin_class=cabin_class,
        observed_at=datetime.fromisoformat(observed_at_str),
    )


def observation_from_flight_offer(
    flight: FlightOffer,
    trip_type: TripType,
    observed_at: datetime | None = None,
) -> PriceObservation:
    """Build a PriceObservation from exactly ONE already-chosen FlightOffer.

    This is a low-level conversion primitive, not a selection strategy. Do
    NOT call this in a loop over every offer a search returned - that would
    treat each offer as an independent point in time and bias the
    historical baseline toward the size of each search's result set
    instead of genuine market movement over time (see "Observation
    Semantics" in docs/PRODUCT_SPEC.md, and the audit note in MVP 0.3.1's
    commit message for the concrete failure mode). When persisting a real
    search result, use `observation_from_search_results(...)` below
    instead, which reduces a whole search snapshot to the one observation
    that actually belongs in history.

    Still a hook for later, deliberately NOT wired into any live search
    flow yet (see "no automatic data collection" in docs/PRODUCT_SPEC.md) -
    MVP 0.3 only proves the storage/statistics layer works. `trip_type` is
    the caller's own knowledge of what kind of search produced this offer
    (FlightOffer itself doesn't record that) - never guessed from the dates.
    """
    return PriceObservation(
        origin=flight.origin,
        destination=flight.destination,
        departure_date=flight.departure_date,
        return_date=flight.return_date,
        trip_type=trip_type,
        price=flight.price,
        currency=flight.currency,
        provider=flight.provider,
        stops=flight.stops,
        airline=flight.airline,
        cabin_class=None,
        observed_at=observed_at or datetime.now(timezone.utc),
    )


def observation_from_search_results(
    offers: list[FlightOffer],
    comparison_group: FlightComparisonGroup,
    observed_at: datetime | None = None,
) -> PriceObservation | None:
    """Reduce one search snapshot (possibly many FlightOffers, possibly
    spanning several travel-date groups) to AT MOST ONE PriceObservation
    FOR THE EXPLICITLY GIVEN `comparison_group`: the cheapest valid,
    complete offer that actually belongs to that group. This is the
    recommended, safe entry point for turning a real search result into
    history - see "Explicit Comparison Groups" in docs/PRODUCT_SPEC.md.

    MVP 0.3.1 used a "largest group wins" heuristic here, inferring the
    comparison group from whichever set of offers was most numerous in the
    list. MVP 0.3.2 removed that: result-count says nothing about which
    travel dates were actually intended, and a future multi-date request
    (e.g. several departure/return combinations in one response) could
    easily make the wrong group "win" by simply returning more offers.
    Trip Hunter must never guess the group from result sizes - the
    caller states it explicitly instead.

    Steps:
    1. Drop any offer whose price isn't confirmed complete
       (price_confirmed_complete=False) - see "Price Completeness" in
       docs/PRODUCT_SPEC.md. An unconfirmed price must never win "cheapest".
    2. Keep only offers that exactly match `comparison_group`
       (FlightComparisonGroup.matches) - route, exact departure/return
       date, currency. Everything else is ignored, not guessed at.
    3. Pick the cheapest offer among what's left. Stops and airline are
       NOT part of the grouping - see "Stops" and "Airline" in
       docs/PRODUCT_SPEC.md: MVP 0.3.1/0.3.2 deliberately track a single
       "cheapest_any" market price, not separate nonstop/max-1-stop or
       per-airline baselines. cabin_class is preserved as metadata only;
       no current provider populates it, so it isn't part of the grouping
       either yet - see docs/PRODUCT_SPEC.md if that changes.

    Returns None if nothing in `offers` is both complete and an exact
    match for `comparison_group` - never guessed.
    """
    matching_offers = [
        offer
        for offer in offers
        if offer.price_confirmed_complete and comparison_group.matches(offer)
    ]
    if not matching_offers:
        return None

    cheapest = min(matching_offers, key=lambda offer: offer.price)
    return observation_from_flight_offer(cheapest, comparison_group.trip_type, observed_at=observed_at)
