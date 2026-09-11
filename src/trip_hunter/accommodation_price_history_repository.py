"""Local SQLite storage for our own observed accommodation prices.

Mirrors price_history_repository.py exactly, for accommodations instead of
flights - see that module's docstring for the full rationale (why SQLite,
why deduplication by calendar day). This is a SEPARATE table
(accommodation_price_observations) in the SAME database file
(DEFAULT_DB_PATH == data/trip_hunter.db), not a generalization of
price_observations - see "Historical Price Intelligence: Accommodations" in
docs/PRODUCT_SPEC.md for why a shared/generalized schema was rejected
(check_in/check_out and no trip_type/stops/airline don't map cleanly onto
the flight-shaped columns, and this project has already rejected forcing
different domain concepts into one shape - see MVP 0.3.1's rejection of a
separate MarketPriceObservation in the other direction).

This module and price_history_repository.py are together the ONLY two
places in the codebase that write SQL - one per domain, sharing one SQLite
file. The rest of the business logic (DealEngine, engine/price_statistics.py,
demos) only ever talks to these repositories' methods.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from trip_hunter.models import (
    AccommodationComparisonGroup,
    AccommodationObservation,
    AccommodationOffer,
    PriceStatistics,
)

DEFAULT_DB_PATH = Path("data/trip_hunter.db")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS accommodation_price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    destination TEXT NOT NULL,
    check_in TEXT NOT NULL,
    check_out TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    provider TEXT NOT NULL,
    name TEXT,
    observed_at TEXT NOT NULL,
    observed_date TEXT NOT NULL,
    UNIQUE (
        destination, check_in, check_out, currency, provider, price, observed_date
    )
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_accommodation_price_observations_route
    ON accommodation_price_observations (destination, check_in, check_out, currency)
"""

_INSERT_SQL = """
INSERT OR IGNORE INTO accommodation_price_observations
    (destination, check_in, check_out, price, currency, provider, name,
     observed_at, observed_date)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_SQL = """
SELECT destination, check_in, check_out, price, currency, provider, name, observed_at
FROM accommodation_price_observations
WHERE destination = ? AND check_in = ? AND check_out = ? AND currency = ?
ORDER BY observed_at
"""


class AccommodationPriceHistoryRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_CREATE_TABLE_SQL)
            connection.execute(_CREATE_INDEX_SQL)

    def add_observation(self, observation: AccommodationObservation) -> bool:
        """Store one observation. Returns True if it was newly inserted,
        False if an equivalent observation (same destination/dates/
        currency/provider/price, same calendar day) already existed.
        """
        observed_date = observation.observed_at.date().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                _INSERT_SQL,
                (
                    observation.destination,
                    observation.check_in.isoformat(),
                    observation.check_out.isoformat(),
                    observation.price,
                    observation.currency,
                    observation.provider,
                    observation.name,
                    observation.observed_at.isoformat(),
                    observed_date,
                ),
            )
            return cursor.rowcount > 0

    def get_observations(
        self,
        destination: str,
        check_in: date,
        check_out: date,
        currency: str,
    ) -> list[AccommodationObservation]:
        """Return all observations for one specific, strict comparison
        group: same destination, same exact check-in/check-out dates, same
        currency. See AccommodationComparisonGroup in models.py for why
        this grouping is deliberately strict.
        """
        with self._connect() as connection:
            rows = connection.execute(
                _SELECT_SQL,
                (destination, check_in.isoformat(), check_out.isoformat(), currency),
            ).fetchall()
        return [_row_to_observation(row) for row in rows]

    def get_route_statistics(
        self,
        destination: str,
        check_in: date,
        check_out: date,
        currency: str,
    ) -> PriceStatistics | None:
        """Convenience: observations for this group, summarized. Returns
        None if there are no observations at all - callers that need a
        minimum-data policy (see MIN_HISTORY_OBSERVATIONS in
        engine/price_statistics.py) must check observation_count themselves.
        """
        # Imported here, not at module level, to keep this storage module
        # from depending on engine/ - engine depends on storage, not the
        # other way around (same convention as price_history_repository.py).
        from trip_hunter.engine.price_statistics import compute_statistics

        observations = self.get_observations(destination, check_in, check_out, currency)
        if not observations:
            return None
        return compute_statistics([observation.price for observation in observations])

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)


def _row_to_observation(row: tuple) -> AccommodationObservation:
    destination, check_in_str, check_out_str, price, currency, provider, name, observed_at_str = row
    return AccommodationObservation(
        destination=destination,
        check_in=date.fromisoformat(check_in_str),
        check_out=date.fromisoformat(check_out_str),
        price=price,
        currency=currency,
        provider=provider,
        name=name,
        observed_at=datetime.fromisoformat(observed_at_str),
    )


def observation_from_accommodation_offer(
    offer: AccommodationOffer,
    observed_at: datetime,
) -> AccommodationObservation:
    """Build an AccommodationObservation from exactly ONE already-chosen
    AccommodationOffer.

    Low-level conversion primitive, not a selection strategy - mirrors
    observation_from_flight_offer(...) in price_history_repository.py,
    including its warning: do NOT call this in a loop over every offer a
    search returned. Use
    observation_from_accommodation_search_results(...) below instead,
    which reduces a whole search snapshot to the one observation that
    actually belongs in history. Unlike the flight-side helper, `observed_at`
    is required here rather than defaulting to "now" - keeps this primitive
    honest about being a pure conversion step with no hidden clock read.
    """
    return AccommodationObservation(
        destination=offer.destination,
        check_in=offer.check_in,
        check_out=offer.check_out,
        price=offer.total_price,
        currency=offer.currency,
        provider=offer.provider,
        name=offer.name,
        observed_at=observed_at,
    )


def observation_from_accommodation_search_results(
    offers: list[AccommodationOffer],
    comparison_group: AccommodationComparisonGroup,
    observed_at: datetime,
) -> AccommodationObservation | None:
    """Reduce one search snapshot (possibly many AccommodationOffers) to AT
    MOST ONE AccommodationObservation FOR THE EXPLICITLY GIVEN
    `comparison_group`: the cheapest offer that actually belongs to that
    group. Mirrors observation_from_search_results(...) in
    price_history_repository.py - see "Observation Semantics" and
    "Explicit Comparison Groups" in docs/PRODUCT_SPEC.md for the full
    rationale (never guess the group from result sizes; a snapshot with
    many offers must not outweigh a snapshot with few).

    Unlike the flight-side helper, there is no price_confirmed_complete-
    style filter here: no active AccommodationProvider has an equivalent
    ambiguity (SerpApiAccommodationProvider's total_rate/rate_per_night
    normalization is already a complete total for the stay, verified
    against a real response - see serpapi_accommodation_provider.py). If a
    future provider needs one, add the filter step here, not by guessing.

    Returns None if nothing in `offers` matches `comparison_group` exactly.
    """
    matching_offers = [offer for offer in offers if comparison_group.matches(offer)]
    if not matching_offers:
        return None

    cheapest = min(matching_offers, key=lambda offer: offer.total_price)
    return observation_from_accommodation_offer(cheapest, observed_at=observed_at)
