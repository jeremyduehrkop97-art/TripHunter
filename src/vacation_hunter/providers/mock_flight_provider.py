"""Fixed, realistic-looking flight data for development and tests.

No network calls, no API keys. `_OFFERS` includes the HAM -> PMI scenario from
the product spec (79 EUR vs. a 180 EUR baseline) alongside a few other routes
covering "no deal", "modest deal" and "normal price" cases.
"""

from __future__ import annotations

from datetime import date

from vacation_hunter.models import FlightOffer
from vacation_hunter.providers.flight_provider import FlightProvider

# Baseline round-trip prices (EUR) for a "normal" price on this route.
# In a real provider this would come from historical price statistics.
_BASELINE_PRICES: dict[tuple[str, str], float] = {
    ("HAM", "PMI"): 180.0,
    ("HAM", "AGP"): 210.0,
    ("BER", "BCN"): 160.0,
    ("MUC", "LIS"): 190.0,
}

_OFFERS: list[FlightOffer] = [
    FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=79.0,
        currency="EUR",
        airline="Eurowings",
        stops=0,
        provider="mock",
    ),
    FlightOffer(
        origin="HAM",
        destination="AGP",
        departure_date=date(2026, 10, 5),
        return_date=date(2026, 10, 12),
        price=205.0,
        currency="EUR",
        airline="Ryanair",
        stops=1,
        provider="mock",
    ),
    FlightOffer(
        origin="BER",
        destination="BCN",
        departure_date=date(2026, 10, 3),
        return_date=date(2026, 10, 10),
        price=135.0,
        currency="EUR",
        airline="Vueling",
        stops=0,
        provider="mock",
    ),
    FlightOffer(
        origin="MUC",
        destination="LIS",
        departure_date=date(2026, 10, 4),
        return_date=date(2026, 10, 9),
        price=185.0,
        currency="EUR",
        airline="TAP",
        stops=0,
        provider="mock",
    ),
]


class MockFlightProvider(FlightProvider):
    def search_flights(
        self,
        origin: str,
        destination: str,
        earliest_departure: date,
        latest_departure: date,
        return_date: date | None = None,
    ) -> list[FlightOffer]:
        # Mock offers already carry fixed dates, so `return_date` isn't
        # needed for filtering here - it only matters for providers that
        # have to ask a real API for a specific date.
        return [
            offer
            for offer in _OFFERS
            if offer.origin == origin
            and offer.destination == destination
            and earliest_departure <= offer.departure_date <= latest_departure
        ]

    def get_typical_price(self, origin: str, destination: str, month: int) -> float | None:
        return _BASELINE_PRICES.get((origin, destination))
