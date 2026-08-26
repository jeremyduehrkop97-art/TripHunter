from datetime import date

from vacation_hunter.engine.deal_engine import DealEngine
from vacation_hunter.models import DealType, FlightOffer
from vacation_hunter.providers.flight_provider import FlightProvider
from vacation_hunter.providers.mock_accommodation_provider import MockAccommodationProvider
from vacation_hunter.providers.mock_flight_provider import MockFlightProvider
from vacation_hunter.providers.null_accommodation_provider import NullAccommodationProvider


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


class _NoBaselineFlightProvider(FlightProvider):
    """Stand-in for a real search API: returns offers but has no historical
    baseline price for any route."""

    def __init__(self, offers: list[FlightOffer]):
        self._offers = offers

    def search_flights(
        self, origin, destination, earliest_departure, latest_departure, return_date=None
    ) -> list[FlightOffer]:
        return self._offers

    def get_typical_price(self, origin, destination, month):
        return None


def test_flight_without_baseline_is_marked_unavailable_not_fabricated():
    """Central product requirement: a real current price with no historical
    comparison must never be classified as a price drop or error fare."""
    flight = FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=89.0,
        currency="EUR",
        airline="Testair",
        stops=0,
        provider="test",
    )
    engine = DealEngine(
        flight_provider=_NoBaselineFlightProvider([flight]),
        accommodation_provider=NullAccommodationProvider(),
    )

    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 2),
        latest_departure=date(2026, 10, 2),
    )

    assert len(deals) == 1
    deal = deals[0]
    assert deal.deal_type == DealType.BASELINE_UNAVAILABLE
    assert deal.deal_type not in (
        DealType.ERROR_FARE,
        DealType.FLIGHT_DROP,
        DealType.UNUSUALLY_LOW,
        DealType.COMBINED_TRIP_DROP,
    )
    assert deal.score is None
    assert deal.savings_absolute is None
    assert deal.expected_flight_price is None
    assert deal.actual_total_price == 89.0
