"""Which feed items the radar has already handled (hourly feed runner).

Two keys per item, both persisted in the same SQLite file as the price
history (restored between GitHub Actions runs together with it):
  - the item's URL without tracking parameters - the same article is never
    sent twice;
  - a deal key (origin | destination | price) - the same deal reported by
    two feeds (Fly4free AND Travel-Dealz) is sent once.
An item is recorded only after it was sent (or, on the very first run,
seeded silently - see feed_radar.py), so a failed Telegram send is retried
by the next hourly run.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from trip_hunter.engine.feed_sensor import DealSignal, _canonical_link
from trip_hunter.price_history_repository import DEFAULT_DB_PATH

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS feed_seen (
    url_key TEXT PRIMARY KEY,
    deal_key TEXT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    action TEXT NOT NULL
)
"""


def url_key(signal: DealSignal) -> str:
    return _canonical_link(signal.link) or signal.title


def deal_key(signal: DealSignal) -> str | None:
    """origin|destination|price, or None if the destination or price is
    unknown (then only the URL identifies the item)."""
    destination = signal.destination_iata or (signal.destination or "").strip().lower()
    if not destination or signal.price is None or not signal.origins:
        return None
    return f"{'/'.join(sorted(signal.origins))}|{destination}|{signal.price:.0f}"


class FeedSeenRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_CREATE_SQL)

    def is_empty(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM feed_seen LIMIT 1").fetchone() is None

    def count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM feed_seen").fetchone()[0]

    def has_seen(self, signal: DealSignal) -> bool:
        """True if this URL, or this very deal from another feed, was handled."""
        key = deal_key(signal)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM feed_seen WHERE url_key = ? OR (? IS NOT NULL AND deal_key = ?) LIMIT 1",
                (url_key(signal), key, key),
            ).fetchone()
        return row is not None

    def mark(self, signal: DealSignal, *, action: str = "sent", now: datetime | None = None) -> None:
        """Record the item (`action`: "sent", "seed"). Idempotent."""
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO feed_seen (url_key, deal_key, source, title, first_seen, action) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (url_key(signal), deal_key(signal), signal.source, signal.title,
                 (now or datetime.now(timezone.utc)).isoformat(), action),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)
