"""Which (origin, destination, departure, return) combinations
engine/daily_scanner.py has recently alerted on.

A dedicated, narrow history - deliberately NOT feed_seen_repository.py's
(that one suppresses an already-sent THIRD-PARTY FEED ITEM essentially
forever, by URL or by a loose, DATELESS "origin|destination|rounded price"
key - see its own docstring). This project's active daily scanner needs a
different rule: suppress the SAME route + exact date window only for
SCANNER_SUPPRESSION_DAYS (the task's own "nicht mehrmals pro Woche"
wording), after which a persisting or returning deal is honestly allowed
to be reported again. Reusing feed_seen_repository's permanent, dateless
key here would either never re-alert a genuinely persisting weekly deal
at all, or (via its price-based key) wrongly conflate two different dates
that happen to share a rounded price - exactly the kind of silent
misbehaviour this project avoids.

Shares the same SQLite file (data/trip_hunter.db) as every other
repository in this project - restored by the same GitHub Actions cache
alongside price_history_repository.py/feed_seen_repository.py's own
tables.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from trip_hunter.price_history_repository import DEFAULT_DB_PATH

# "nicht mehrmals pro Woche" - a week, read literally.
SCANNER_SUPPRESSION_DAYS = 7

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS scanner_alerts (
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    return_date TEXT NOT NULL,
    alerted_at TEXT NOT NULL,
    PRIMARY KEY (origin, destination, departure_date, return_date)
)
"""


class ScannerHistoryRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_CREATE_SQL)

    def was_recently_alerted(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date,
        *,
        within_days: int = SCANNER_SUPPRESSION_DAYS,
        now: datetime | None = None,
    ) -> bool:
        """True if this exact route + date window was already alerted on
        within the last `within_days` days - never checked by price (a
        deal that got cheaper, or pricier but still a deal, is still the
        same already-reported window)."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT alerted_at FROM scanner_alerts "
                "WHERE origin = ? AND destination = ? AND departure_date = ? AND return_date = ?",
                (origin, destination, departure_date.isoformat(), return_date.isoformat()),
            ).fetchone()
        if row is None:
            return False
        alerted_at = datetime.fromisoformat(row[0])
        moment = now or datetime.now(timezone.utc)
        return (moment - alerted_at) < timedelta(days=within_days)

    def mark_alerted(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date,
        *,
        now: datetime | None = None,
    ) -> None:
        """Record (or refresh) the moment this route + date window was
        last alerted on. Idempotent - a repeat mark just moves the
        suppression window forward from the latest alert."""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scanner_alerts (origin, destination, departure_date, return_date, alerted_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (origin, destination, departure_date, return_date) "
                "DO UPDATE SET alerted_at = excluded.alerted_at",
                (
                    origin, destination, departure_date.isoformat(), return_date.isoformat(),
                    (now or datetime.now(timezone.utc)).isoformat(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)
