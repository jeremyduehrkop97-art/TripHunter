"""Runnable demo: shows the full Vacation Hunter pipeline end to end.

Run with: python -m vacation_hunter.demo
"""

from __future__ import annotations

from datetime import date

from vacation_hunter.engine.deal_engine import DealEngine
from vacation_hunter.models import Deal, DealType
from vacation_hunter.providers.mock_accommodation_provider import MockAccommodationProvider
from vacation_hunter.providers.mock_flight_provider import MockFlightProvider

_DEAL_TYPE_LABELS = {
    DealType.COMBINED_TRIP_DROP: "\U0001f525 COMBINED TRIP DROP",
    DealType.ERROR_FARE: "⚠️ ERROR FARE",
    DealType.FLIGHT_DROP: "✈️ FLIGHT DROP",
    DealType.HOTEL_DROP: "\U0001f3e8 HOTEL DROP",
    DealType.UNUSUALLY_LOW: "\U0001f4a1 UNUSUALLY LOW PRICE",
}

_CITY_NAMES = {
    "HAM": "Hamburg",
    "PMI": "Mallorca",
    "AGP": "Malaga",
    "BER": "Berlin",
    "BCN": "Barcelona",
    "MUC": "Munich",
    "LIS": "Lisbon",
}


def _city(iata_code: str) -> str:
    return _CITY_NAMES.get(iata_code, iata_code)


def print_deal(deal: Deal) -> None:
    print(_DEAL_TYPE_LABELS.get(deal.deal_type, deal.deal_type.value))
    print()
    print(f"{_city(deal.flight.origin)} → {_city(deal.flight.destination)}")
    print(f"{deal.flight.departure_date:%d.%m.}–{deal.flight.return_date:%d.%m.%Y}")
    print()
    print(f"Flight: {deal.flight.price:.0f} €")
    if deal.accommodation is not None:
        print(f"Accommodation: {deal.accommodation.total_price:.0f} €")
    print(f"Trip total: {deal.actual_total_price:.0f} €")
    print()
    print(f"Expected: {deal.expected_total_price:.0f} €")
    print(f"Saving: {deal.savings_absolute:.0f} € / {deal.savings_percentage * 100:.0f} %")
    print()
    print(f"Trip Score: {deal.score.total}/100")


def run_demo() -> list[Deal]:
    engine = DealEngine(
        flight_provider=MockFlightProvider(),
        accommodation_provider=MockAccommodationProvider(),
    )

    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 1),
        latest_departure=date(2026, 10, 10),
    )

    for deal in deals:
        print_deal(deal)
        print()
        print("-" * 40)
        print()

    return deals


if __name__ == "__main__":
    run_demo()
