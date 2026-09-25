"""Which flights have already been alerted - one alert per flight
connection, with a price-drop exception.

A flight connection is (origin, destination, departure_date,
return_date). With daily sampling the same cheap flight would otherwise
be re-detected and re-posted every day, and, as hotel offers change, even
with a different hotel each time. Once an alert for a connection was
delivered, no further alert is sent for it, whatever the hotel - EXCEPT a
price-drop update: if the same connection is scanned again with a flight
price at least REALERT_PRICE_DROP (20%) below the price at the LAST alert
for it, one new alert is allowed (and that new price becomes the
reference for the next update, so a slow slide can't re-alert every day
but a further 20% drop can).

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

REALERT_PRICE_DROP = 0.20

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


def is_price_drop_update(last_price: float | None, price: float) -> bool:
    """True if `price` is at least REALERT_PRICE_DROP below `last_price`."""
    return last_price is not None and last_price > 0 and price <= last_price * (1 - REALERT_PRICE_DROP)


class InMemoryAlertHistory:
    def __init__(self) -> None:
        self._prices: dict[FlightKey, float] = {}

    def has_alerted(self, key: FlightKey) -> bool:
        return key in self._prices

    def last_alert_price(self, key: FlightKey) -> float | None:
        return self._prices.get(key)

    def should_alert(self, key: FlightKey, price: float) -> bool:
        """New connection, or a >= 20% price drop since the last alert."""
        return key not in self._prices or is_price_drop_update(self._prices[key], price)

    def record(self, deal: Deal, *, sent_at: datetime | None = None) -> None:
        self._prices[flight_key(deal)] = deal.flight.price


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

    def last_alert_price(self, key: FlightKey) -> float | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT flight_price FROM sent_alerts WHERE origin = ? AND destination = ? "
                "AND departure_date = ? AND return_date = ?",
                (key[0], key[1], key[2].isoformat(), key[3].isoformat()),
            ).fetchone()
        return None if row is None else row[0]

    def should_alert(self, key: FlightKey, price: float) -> bool:
        """New connection, or a >= 20% price drop since the last alert."""
        if not self.has_alerted(key):
            return True
        return is_price_drop_update(self.last_alert_price(key), price)

    def record(self, deal: Deal, *, sent_at: datetime | None = None) -> None:
        """Remember that `deal`'s flight connection was alerted at this
        price. Recording again (a price-drop update) replaces the stored
        price, which is what the next update is measured against."""
        origin, destination, departure, return_ = flight_key(deal)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO sent_alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    origin, destination, departure.isoformat(), return_.isoformat(),
                    (sent_at or datetime.now(timezone.utc)).isoformat(),
                    deal.deal_type.value, deal.flight.price,
                    deal.accommodation.name if deal.accommodation else None,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)
