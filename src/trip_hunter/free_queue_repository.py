"""Delay queue for FREE_CHANNEL_MODE=delayed_full.

VIP gets an alert immediately; the Free channel gets the complete alert only
after a delay. The message is rendered when the deal is found and stored
READY TO SEND (text, photo URL, keyboards) in the same SQLite file as the
price history (restored between GitHub Actions runs together with it), so
nothing has to be rebuilt later. The sampler flushes due items at the end
of a run (telegram.flush_free_queue) - with a Mon/Wed/Fri/Sun schedule an
item goes out at the first run after it is due.

An item that could not be sent stays pending and is retried on the next
flush; one older than MAX_QUEUE_AGE is expired instead - a week-old error
fare is not news.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trip_hunter.price_history_repository import DEFAULT_DB_PATH

MAX_QUEUE_AGE = timedelta(days=7)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS free_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    text TEXT NOT NULL,
    photo_url TEXT,
    keyboards TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
)
"""


@dataclass(frozen=True)
class QueuedAlert:
    id: int
    created_at: datetime
    due_at: datetime
    text: str
    photo_url: str | None
    keyboards: list[dict]


class FreeQueueRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_CREATE_SQL)

    def enqueue(
        self, *, text: str, photo_url: str | None, keyboards: list[dict], due_at: datetime,
        now: datetime | None = None,
    ) -> int:
        created = now or datetime.now(timezone.utc)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO free_queue (created_at, due_at, text, photo_url, keyboards) VALUES (?, ?, ?, ?, ?)",
                (created.isoformat(), due_at.isoformat(), text, photo_url, json.dumps(keyboards)),
            )
            return int(cursor.lastrowid)

    def due(self, now: datetime | None = None) -> list[QueuedAlert]:
        """Pending items whose time has come, oldest first. Items older
        than MAX_QUEUE_AGE are expired first and never returned."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                "UPDATE free_queue SET status = 'expired' WHERE status = 'pending' AND created_at < ?",
                ((now - MAX_QUEUE_AGE).isoformat(),),
            )
            rows = connection.execute(
                "SELECT id, created_at, due_at, text, photo_url, keyboards FROM free_queue "
                "WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id",
                (now.isoformat(),),
            ).fetchall()
        return [
            QueuedAlert(
                id=row[0], created_at=datetime.fromisoformat(row[1]), due_at=datetime.fromisoformat(row[2]),
                text=row[3], photo_url=row[4], keyboards=json.loads(row[5]),
            )
            for row in rows
        ]

    def mark_sent(self, item_id: int) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE free_queue SET status = 'sent' WHERE id = ?", (item_id,))

    def pending_count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM free_queue WHERE status = 'pending'").fetchone()[0]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)
