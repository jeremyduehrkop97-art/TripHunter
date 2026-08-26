from datetime import date

from vacation_hunter.engine.flight_deal_detector import assess_flight
from vacation_hunter.models import DealType, FlightOffer


def _flight(price: float) -> FlightOffer:
    return FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=price,
        currency="EUR",
        airline="Testair",
        stops=0,
        provider="test",
    )


def test_error_fare_detected_above_70_percent_off():
    result = assess_flight(_flight(50.0), typical_price=200.0)
    assert result.deal_type == DealType.ERROR_FARE


def test_flight_drop_detected_between_30_and_70_percent_off():
    result = assess_flight(_flight(79.0), typical_price=180.0)
    assert result.deal_type == DealType.FLIGHT_DROP


def test_unusually_low_detected_between_15_and_30_percent_off():
    result = assess_flight(_flight(150.0), typical_price=180.0)
    assert result.deal_type == DealType.UNUSUALLY_LOW


def test_no_deal_below_threshold():
    result = assess_flight(_flight(175.0), typical_price=180.0)
    assert result.deal_type is None


def test_savings_are_computed_correctly():
    result = assess_flight(_flight(79.0), typical_price=180.0)
    assert result.savings_absolute == 101.0
    assert round(result.savings_percentage, 4) == round(101 / 180, 4)
