"""Rotation state for the weekly "Travel Hack" tip
(dispatch/weekly_tips.py): which tip went out last, and when - a single
row in the same SQLite file as the price history (restored between
GitHub Actions runs together with it, like feed_seen_repository.py and
free_queue_repository.py), so the rotation survives across weeks without
repeating a tip back-to-back.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trip_hunter.price_history_repository import DEFAULT_DB_PATH

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS weekly_tip_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_index INTEGER NOT NULL,
    last_sent_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class WeeklyTipState:
    last_index: int
    last_sent_at: datetime


class WeeklyTipRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_CREATE_SQL)

    def last_sent(self) -> WeeklyTipState | None:
        """The last tip index sent and when, or None if a tip has never
        been sent yet (the very first run)."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_index, last_sent_at FROM weekly_tip_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return WeeklyTipState(last_index=row[0], last_sent_at=datetime.fromisoformat(row[1]))

    def record_sent(self, index: int, *, now: datetime | None = None) -> None:
        moment = now or datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO weekly_tip_state (id, last_index, last_sent_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET last_index = excluded.last_index, "
                "last_sent_at = excluded.last_sent_at",
                (index, moment.isoformat()),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)
