from datetime import date

import pytest

from vacation_hunter.models import AccommodationOffer, DealScore, FlightComparisonGroup, TripType


def test_accommodation_offer_nights_property():
    offer = AccommodationOffer(
        destination="PMI",
        check_in=date(2026, 10, 2),
        check_out=date(2026, 10, 7),
        total_price=205.0,
        currency="EUR",
        name="Test Hotel",
        rating=4.0,
        provider="test",
    )
    assert offer.nights == 5


def test_deal_score_rejects_out_of_range_total():
    with pytest.raises(ValueError):
        DealScore(total=150, breakdown={})


def test_flight_comparison_group_accepts_consistent_round_trip():
    group = FlightComparisonGroup(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    )
    assert group.trip_type == TripType.ROUND_TRIP


def test_flight_comparison_group_accepts_consistent_one_way():
    group = FlightComparisonGroup(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 2),
        trip_type=TripType.ONE_WAY,
        currency="EUR",
    )
    assert group.trip_type == TripType.ONE_WAY


def test_flight_comparison_group_rejects_round_trip_with_same_day_return():
    with pytest.raises(ValueError):
        FlightComparisonGroup(
            origin="HAM",
            destination="PMI",
            departure_date=date(2026, 10, 2),
            return_date=date(2026, 10, 2),  # looks like a one-way, but claims ROUND_TRIP
            trip_type=TripType.ROUND_TRIP,
            currency="EUR",
        )


def test_flight_comparison_group_rejects_one_way_with_different_return_date():
    with pytest.raises(ValueError):
        FlightComparisonGroup(
            origin="HAM",
            destination="PMI",
            departure_date=date(2026, 10, 2),
            return_date=date(2026, 10, 7),  # a real return date, but claims ONE_WAY
            trip_type=TripType.ONE_WAY,
            currency="EUR",
        )


def test_flight_comparison_group_matches_checks_every_offer_field():
    from vacation_hunter.models import FlightOffer

    group = FlightComparisonGroup(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    )
    matching_offer = FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=184.0,
        currency="EUR",
        airline="Vueling",
        stops=0,
        provider="test",
    )
    wrong_currency = FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=50.0,
        currency="USD",
        airline="Vueling",
        stops=0,
        provider="test",
    )

    assert group.matches(matching_offer) is True
    assert group.matches(wrong_currency) is False
