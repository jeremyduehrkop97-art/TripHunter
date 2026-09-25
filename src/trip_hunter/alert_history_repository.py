"""Which flights have already been alerted - strict "one alert per flight
connection".

A flight connection is (origin, destination, departure_date,
return_date). With daily sampling the same cheap flight would otherwise
be re-detected and re-posted every day, and, as hotel offers change, even
with a different hotel each time. Once an alert for a connection was
delivered, no further alert is sent for it, whatever the hotel or price
(a stricter rule than "unchanged since last time" - by design).

Persistence: a table in the same SQLite file as the price history
(data/trip_hunter.db, restored between GitHub Actions runs together with
it). An alert is only recorded after the dispatch succeeded, so a failed
Telegram send stays eligible for the next run.
`InMemoryAlertHistory` gives the same behaviour for one run when no
database is wanted (and in tests).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from trip_hunter.models import Deal, FlightComparisonGroup
from trip_hunter.price_history_repository import DEFAULT_DB_PATH

FlightKey = tuple[str, str, date, date]

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sent_alerts (
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    return_date TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    deal_type TEXT,
    flight_price REAL,
    hotel_name TEXT,
    PRIMARY KEY (origin, destination, departure_date, return_date)
)
"""


def flight_key(source: Deal | FlightComparisonGroup) -> FlightKey:
    """The unique flight connection of a deal or comparison group."""
    if isinstance(source, Deal):
        f = source.flight
        return (f.origin, f.destination, f.departure_date, f.return_date)
    return (source.origin, source.destination, source.departure_date, source.return_date)


class InMemoryAlertHistory:
    def __init__(self) -> None:
        self._keys: set[FlightKey] = set()

    def has_alerted(self, key: FlightKey) -> bool:
        return key in self._keys

    def record(self, deal: Deal, *, sent_at: datetime | None = None) -> None:
        self._keys.add(flight_key(deal))


class AlertHistoryRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_CREATE_TABLE_SQL)

    def has_alerted(self, key: FlightKey) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sent_alerts WHERE origin = ? AND destination = ? "
                "AND departure_date = ? AND return_date = ?",
                (key[0], key[1], key[2].isoformat(), key[3].isoformat()),
            ).fetchone()
        return row is not None

    def record(self, deal: Deal, *, sent_at: datetime | None = None) -> None:
        """Remember that `deal`'s flight connection was alerted. Recording
        the same connection again is a no-op (the first alert stands)."""
        origin, destination, departure, return_ = flight_key(deal)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO sent_alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    origin, destination, departure.isoformat(), return_.isoformat(),
                    (sent_at or datetime.now(timezone.utc)).isoformat(),
                    deal.deal_type.value, deal.flight.price,
                    deal.accommodation.name if deal.accommodation else None,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)
