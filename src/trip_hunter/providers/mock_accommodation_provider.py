"""Fixed, realistic-looking accommodation data for development and tests.

Dates line up with `mock_flight_provider` so the same trip can be searched
end to end. The PMI offer (205 EUR vs. a 340 EUR baseline for 5 nights)
matches the product spec's example scenario exactly.
"""

from __future__ import annotations

from datetime import date

from trip_hunter.models import AccommodationOffer
from trip_hunter.providers.accommodation_provider import AccommodationProvider

# Baseline price per night (EUR) for a "normal" stay at this destination.
_BASELINE_PRICE_PER_NIGHT: dict[str, float] = {
    "PMI": 68.0,
    "AGP": 55.0,
    "BCN": 70.0,
    "LIS": 60.0,
}

_OFFERS: list[AccommodationOffer] = [
    AccommodationOffer(
        destination="PMI",
        check_in=date(2026, 10, 2),
        check_out=date(2026, 10, 7),
        total_price=205.0,
        currency="EUR",
        name="Hotel Playa Sol",
        rating=4.2,
        provider="mock",
    ),
    AccommodationOffer(
        destination="AGP",
        check_in=date(2026, 10, 5),
        check_out=date(2026, 10, 12),
        total_price=380.0,
        currency="EUR",
        name="Costa del Sol Apartments",
        rating=3.9,
        provider="mock",
    ),
    AccommodationOffer(
        destination="BCN",
        check_in=date(2026, 10, 3),
        check_out=date(2026, 10, 10),
        total_price=410.0,
        currency="EUR",
        name="Hotel Barceloneta Beach",
        rating=4.5,
        provider="mock",
    ),
    AccommodationOffer(
        destination="LIS",
        check_in=date(2026, 10, 4),
        check_out=date(2026, 10, 9),
        total_price=300.0,
        currency="EUR",
        name="Lisbon Central Suites",
        rating=4.0,
        provider="mock",
    ),
]


class MockAccommodationProvider(AccommodationProvider):
    def search_accommodations(
        self, destination: str, check_in: date, check_out: date
    ) -> list[AccommodationOffer]:
        return [
            offer
            for offer in _OFFERS
            if offer.destination == destination
            and offer.check_in == check_in
            and offer.check_out == check_out
        ]

    def get_typical_total_price(self, destination: str, nights: int, month: int) -> float:
        try:
            per_night = _BASELINE_PRICE_PER_NIGHT[destination]
        except KeyError:
            raise ValueError(f"No baseline price known for destination {destination}") from None
        return round(per_night * nights, 2)
