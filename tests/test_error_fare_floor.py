from __future__ import annotations

from datetime import date

from trip_hunter.engine.error_fare_floor import (
    MAX_HOTEL_PRICE_PER_NIGHT,
    MID_HAUL_ERROR_FARE_FLOOR,
    SHORT_HAUL_ERROR_FARE_FLOOR,
    error_fare_floor_for,
    error_fare_floor_triggered,
)
from trip_hunter.models import AccommodationOffer, FlightOffer

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 7)  # 5 nights


def _flight(price: float, *, destination: str = "PMI") -> FlightOffer:
    return FlightOffer(
        origin="HAM", destination=destination, departure_date=_FRI, return_date=_SUN,
        price=price, currency="EUR", airline="Testair", stops=0, provider="test",
    )


def _accommodation(total_price: float) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI", check_in=_FRI, check_out=_SUN, total_price=total_price,
        currency="EUR", name="Test Hotel", rating=4.0, provider="test",
    )


# --- error_fare_floor_for -----------------------------------------------------


def test_error_fare_floor_for_unknown_destination_is_short_haul():
    assert error_fare_floor_for("PMI") == SHORT_HAUL_ERROR_FARE_FLOOR


def test_error_fare_floor_for_mid_haul_destination():
    assert error_fare_floor_for("LPA") == MID_HAUL_ERROR_FARE_FLOOR  # Gran Canaria
    assert error_fare_floor_for("ATH") == MID_HAUL_ERROR_FARE_FLOOR  # Athens


def test_short_and_mid_haul_floors_are_different():
    assert SHORT_HAUL_ERROR_FARE_FLOOR != MID_HAUL_ERROR_FARE_FLOOR
    assert SHORT_HAUL_ERROR_FARE_FLOOR < MID_HAUL_ERROR_FARE_FLOOR


# --- error_fare_floor_triggered -----------------------------------------------


def test_triggers_at_exactly_the_short_haul_floor():
    flight = _flight(SHORT_HAUL_ERROR_FARE_FLOOR, destination="PMI")
    assert error_fare_floor_triggered(flight, None) is True


def test_does_not_trigger_one_cent_above_the_floor():
    flight = _flight(SHORT_HAUL_ERROR_FARE_FLOOR + 0.01, destination="PMI")
    assert error_fare_floor_triggered(flight, None) is False


def test_triggers_at_exactly_the_mid_haul_floor():
    flight = _flight(MID_HAUL_ERROR_FARE_FLOOR, destination="ATH")
    assert error_fare_floor_triggered(flight, None) is True


def test_missing_accommodation_does_not_block_the_trigger():
    flight = _flight(20.0, destination="PMI")
    assert error_fare_floor_triggered(flight, None) is True


def test_fairly_priced_accommodation_allows_the_trigger():
    flight = _flight(20.0, destination="PMI")
    fair_hotel = _accommodation(MAX_HOTEL_PRICE_PER_NIGHT * 5)  # exactly at the ceiling, 5 nights

    assert error_fare_floor_triggered(flight, fair_hotel) is True


def test_overpriced_accommodation_blocks_the_trigger():
    flight = _flight(20.0, destination="PMI")
    expensive_hotel = _accommodation((MAX_HOTEL_PRICE_PER_NIGHT + 1) * 5)

    assert error_fare_floor_triggered(flight, expensive_hotel) is False


def test_flight_above_floor_never_triggers_regardless_of_hotel():
    flight = _flight(SHORT_HAUL_ERROR_FARE_FLOOR + 5, destination="PMI")
    cheap_hotel = _accommodation(1.0)

    assert error_fare_floor_triggered(flight, cheap_hotel) is False
