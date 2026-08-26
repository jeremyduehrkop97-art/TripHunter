from datetime import date

from vacation_hunter.engine.hotel_deal_detector import assess_accommodation
from vacation_hunter.models import AccommodationOffer, DealType


def _accommodation(total_price: float) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI",
        check_in=date(2026, 10, 2),
        check_out=date(2026, 10, 7),
        total_price=total_price,
        currency="EUR",
        name="Test Hotel",
        rating=4.0,
        provider="test",
    )


def test_hotel_drop_detected_above_threshold():
    result = assess_accommodation(_accommodation(205.0), typical_total_price=340.0)
    assert result.deal_type == DealType.HOTEL_DROP


def test_no_hotel_deal_below_threshold():
    result = assess_accommodation(_accommodation(320.0), typical_total_price=340.0)
    assert result.deal_type is None


def test_savings_are_computed_correctly():
    result = assess_accommodation(_accommodation(205.0), typical_total_price=340.0)
    assert result.savings_absolute == 135.0
