from datetime import date

from vacation_hunter.engine.deal_engine import DealEngine
from vacation_hunter.models import DealType
from vacation_hunter.providers.mock_accommodation_provider import MockAccommodationProvider
from vacation_hunter.providers.mock_flight_provider import MockFlightProvider


def _engine() -> DealEngine:
    return DealEngine(
        flight_provider=MockFlightProvider(),
        accommodation_provider=MockAccommodationProvider(),
    )


def test_ham_pmi_trip_is_detected_as_combined_trip_drop():
    """Reproduces the example scenario from docs/PRODUCT_SPEC.md."""
    deals = _engine().find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 1),
        latest_departure=date(2026, 10, 10),
    )

    assert len(deals) == 1
    deal = deals[0]

    assert deal.deal_type == DealType.COMBINED_TRIP_DROP
    assert deal.flight.price == 79.0
    assert deal.accommodation is not None
    assert deal.accommodation.total_price == 205.0
    assert deal.actual_total_price == 284.0
    assert deal.expected_total_price == 520.0
    assert deal.savings_absolute == 236.0
    assert round(deal.savings_percentage * 100) == 45
    assert 0 <= deal.score.total <= 100


def test_route_with_normal_prices_yields_no_deal():
    deals = _engine().find_trip_deals(
        origin="MUC",
        destination="LIS",
        earliest_departure=date(2026, 10, 1),
        latest_departure=date(2026, 10, 10),
    )
    assert deals == []


def test_partial_deal_route_is_flagged_but_not_combined():
    deals = _engine().find_trip_deals(
        origin="BER",
        destination="BCN",
        earliest_departure=date(2026, 10, 1),
        latest_departure=date(2026, 10, 10),
    )
    assert len(deals) == 1
    assert deals[0].deal_type != DealType.COMBINED_TRIP_DROP


def test_unknown_route_yields_no_deals_and_does_not_raise():
    deals = _engine().find_trip_deals(
        origin="XXX",
        destination="YYY",
        earliest_departure=date(2026, 10, 1),
        latest_departure=date(2026, 10, 10),
    )
    assert deals == []
