"""Runnable demo: the full "Wochenend-Getaway-Radar" pipeline end to end -
DealEngine over several routes, curated with engine/deal_filters.py, then
formatted into a newsletter draft with alerts/deal_formatter.py. Entirely
mock data, no network calls, no API key needed.

Run with: python -m trip_hunter.weekend_radar_demo

Deliberately uses its OWN small, dedicated mock providers below rather than
providers/mock_flight_provider.py / mock_accommodation_provider.py - those
are shared fixtures many existing tests assert exact contents of; keeping
this demo's scenario-specific data (three real Friday->Sunday weekends,
tuned to show one clean pass, one non-deal, and one over-budget deal) fully
local avoids any risk of coupling this demo to those tests' fixtures.
"""

from __future__ import annotations

from datetime import date

from trip_hunter.alerts.deal_formatter import format_newsletter
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.engine.deal_filters import DealFilterCriteria, filter_deals
from trip_hunter.models import AccommodationOffer, Deal, FlightOffer
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.flight_provider import FlightProvider

# Three real Friday->Sunday weekends in autumn 2026, one per route - not the
# same weekend, to make clear these are independent candidate trips, not
# alternate dates for one route.
_ROUTES = [
    # (origin, destination, departure (Fri), return (Sun))
    ("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 4)),
    ("HAM", "BCN", date(2026, 10, 9), date(2026, 10, 11)),
    ("HAM", "ROM", date(2026, 10, 16), date(2026, 10, 18)),
]

# Baseline round-trip prices (EUR) for a "normal" price on this route - same
# convention as mock_flight_provider.py's _BASELINE_PRICES.
_FLIGHT_BASELINES: dict[tuple[str, str], float] = {
    ("HAM", "PMI"): 140.0,
    ("HAM", "BCN"): 140.0,
    ("HAM", "ROM"): 130.0,
}

_FLIGHT_OFFERS: list[FlightOffer] = [
    FlightOffer(
        origin="HAM", destination="PMI", departure_date=date(2026, 10, 2), return_date=date(2026, 10, 4),
        price=65.0, currency="EUR", airline="Eurowings", stops=0, provider="mock_weekend_radar",
        booking_link="https://example.com/book/flight/ham-pmi-1002",
    ),
    # Only ~14% below baseline - below the 15% UNUSUALLY_LOW threshold, so
    # this route never becomes a Deal at all (proves the radar doesn't just
    # rubber-stamp every route it searches).
    FlightOffer(
        origin="HAM", destination="BCN", departure_date=date(2026, 10, 9), return_date=date(2026, 10, 11),
        price=120.0, currency="EUR", airline="Vueling", stops=0, provider="mock_weekend_radar",
        booking_link="https://example.com/book/flight/ham-bcn-1009",
    ),
    # A genuine FLIGHT_DROP, but the hotel isn't cheap enough to reach
    # COMBINED_TRIP_DROP and the combined total lands over a 250 EUR budget
    # - proves the budget filter actually excludes a real deal, not just a
    # non-deal.
    FlightOffer(
        origin="HAM", destination="ROM", departure_date=date(2026, 10, 16), return_date=date(2026, 10, 18),
        price=55.0, currency="EUR", airline="Ryanair", stops=0, provider="mock_weekend_radar",
        booking_link="https://example.com/book/flight/ham-rom-1016",
    ),
]

# Baseline total accommodation price (EUR) for the specific stay length used
# above (2 nights each) - kept as a flat total per destination rather than a
# per-night rate, since every route here happens to be a 2-night stay.
_ACCOMMODATION_BASELINE_TOTAL: dict[str, float] = {
    "PMI": 150.0,
    "BCN": 190.0,
    "ROM": 230.0,
}

_ACCOMMODATION_OFFERS: list[AccommodationOffer] = [
    AccommodationOffer(
        destination="PMI", check_in=date(2026, 10, 2), check_out=date(2026, 10, 4),
        total_price=90.0, currency="EUR", name="Hostal Born Boutique", rating=4.3,
        provider="mock_weekend_radar", booking_link="https://example.com/book/hotel/pmi-1002",
    ),
    AccommodationOffer(
        destination="BCN", check_in=date(2026, 10, 9), check_out=date(2026, 10, 11),
        total_price=180.0, currency="EUR", name="El Born Suites", rating=4.0,
        provider="mock_weekend_radar", booking_link="https://example.com/book/hotel/bcn-1009",
    ),
    AccommodationOffer(
        destination="ROM", check_in=date(2026, 10, 16), check_out=date(2026, 10, 18),
        total_price=220.0, currency="EUR", name="Trastevere Charme B&B", rating=4.5,
        provider="mock_weekend_radar", booking_link="https://example.com/book/hotel/rom-1016",
    ),
]


class _WeekendRadarFlightProvider(FlightProvider):
    def search_flights(
        self, origin: str, destination: str, earliest_departure: date, latest_departure: date,
        return_date: date | None = None,
    ) -> list[FlightOffer]:
        return [
            offer
            for offer in _FLIGHT_OFFERS
            if offer.origin == origin
            and offer.destination == destination
            and earliest_departure <= offer.departure_date <= latest_departure
        ]

    def get_typical_price(self, origin: str, destination: str, month: int) -> float | None:
        return _FLIGHT_BASELINES.get((origin, destination))


class _WeekendRadarAccommodationProvider(AccommodationProvider):
    def search_accommodations(
        self, destination: str, check_in: date, check_out: date
    ) -> list[AccommodationOffer]:
        return [
            offer
            for offer in _ACCOMMODATION_OFFERS
            if offer.destination == destination and offer.check_in == check_in and offer.check_out == check_out
        ]

    def get_typical_total_price(self, destination: str, nights: int, month: int) -> float | None:
        return _ACCOMMODATION_BASELINE_TOTAL.get(destination)


def run_demo(max_total_price: float = 250.0) -> list[Deal]:
    engine = DealEngine(
        flight_provider=_WeekendRadarFlightProvider(),
        accommodation_provider=_WeekendRadarAccommodationProvider(),
    )

    print("TRIP HUNTER — WOCHENEND-RADAR")
    print()
    print(f"Durchsuchte Routen: {len(_ROUTES)}")

    raw_deals: list[Deal] = []
    for origin, destination, departure, return_date in _ROUTES:
        deals = engine.find_trip_deals(
            origin=origin,
            destination=destination,
            earliest_departure=departure,
            latest_departure=departure,
            return_date=return_date,
        )
        print(f"  {origin} → {destination} ({departure} – {return_date}): {len(deals)} Deal(s) gefunden")
        raw_deals.extend(deals)

    print()
    print(f"Rohdeals gesamt: {len(raw_deals)}")

    criteria = DealFilterCriteria.weekend_getaway(max_total_price=max_total_price)
    curated_deals = filter_deals(raw_deals, criteria)

    print(
        f"Nach Kuratierung (Wochenend-Getaway, <= {max_total_price:.0f} EUR, "
        f"Score >= {criteria.min_score}): {len(curated_deals)} Deal(s)"
    )
    print()
    print("=" * 60)
    print()

    print(format_newsletter(curated_deals))

    return curated_deals


if __name__ == "__main__":
    run_demo()
