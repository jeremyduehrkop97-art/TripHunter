from __future__ import annotations

from datetime import date

from trip_hunter.engine.quality_gate import MIN_HOTEL_RATING, passes_quality_gate
from trip_hunter.models import AccommodationOffer, Deal, DealScore, DealType, FlightOffer

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 4)


def _deal(
    *,
    deal_type: DealType = DealType.FLIGHT_DROP,
    savings_percentage: float = 0.40,
    rating: float | None = 4.2,
    with_hotel: bool = True,
    departure_time: str | None = None,
) -> Deal:
    hotel = (
        AccommodationOffer(
            destination="PMI", check_in=_FRI, check_out=_SUN, total_price=90.0,
            currency="EUR", name="Hotel", rating=rating, provider="test",
        )
        if with_hotel
        else None
    )
    flight = FlightOffer(
        origin="HAM", destination="PMI", departure_date=_FRI, return_date=_SUN,
        price=79.0, currency="EUR", airline="Testair", stops=0, provider="test",
        departure_time=departure_time,
    )
    return Deal(
        deal_type=deal_type, flight=flight, accommodation=hotel,
        expected_flight_price=140.0, expected_accommodation_price=None,
        score=DealScore(total=70, breakdown={}), savings_absolute=61.0,
        savings_percentage=savings_percentage,
    )


def test_rating_at_minimum_passes():
    assert passes_quality_gate(_deal(rating=MIN_HOTEL_RATING)) is True


def test_rating_below_minimum_fails_for_tier_2():
    assert passes_quality_gate(_deal(rating=3.7)) is False


def test_rating_below_minimum_fails_for_tier_3():
    assert passes_quality_gate(_deal(deal_type=DealType.HOTEL_DROP, rating=3.0)) is False


def test_missing_rating_or_hotel_never_blocks():
    assert passes_quality_gate(_deal(rating=None)) is True
    assert passes_quality_gate(_deal(with_hotel=False)) is True


def test_tier_1_ignores_bad_rating_and_early_departure():
    deal = _deal(deal_type=DealType.ERROR_FARE, rating=1.0, departure_time="03:00")
    assert passes_quality_gate(deal) is True


def test_high_savings_promoted_tier_1_ignores_filters():
    assert passes_quality_gate(_deal(savings_percentage=0.65, rating=2.0)) is True


def test_early_outbound_departure_fails():
    assert passes_quality_gate(_deal(departure_time="05:59")) is False


def test_outbound_departure_at_six_passes():
    assert passes_quality_gate(_deal(departure_time="06:00")) is True


def test_missing_or_unparsable_time_never_blocks():
    assert passes_quality_gate(_deal(departure_time=None)) is True
    assert passes_quality_gate(_deal(departure_time="früh")) is True
