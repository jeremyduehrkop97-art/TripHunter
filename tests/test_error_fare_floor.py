from __future__ import annotations

from datetime import date

from trip_hunter.engine.error_fare_floor import (
    MID_HAUL_ERROR_FARE_FLOOR,
    SHORT_HAUL_ERROR_FARE_FLOOR,
    error_fare_floor_for,
    error_fare_floor_triggered,
)
from trip_hunter.models import FlightOffer

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 7)  # 5 nights


def _flight(price: float, *, destination: str = "PMI") -> FlightOffer:
    return FlightOffer(
        origin="HAM", destination=destination, departure_date=_FRI, return_date=_SUN,
        price=price, currency="EUR", airline="Testair", stops=0, provider="test",
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
    assert error_fare_floor_triggered(flight) is True


def test_does_not_trigger_one_cent_above_the_floor():
    flight = _flight(SHORT_HAUL_ERROR_FARE_FLOOR + 0.01, destination="PMI")
    assert error_fare_floor_triggered(flight) is False


def test_triggers_at_exactly_the_mid_haul_floor():
    flight = _flight(MID_HAUL_ERROR_FARE_FLOOR, destination="ATH")
    assert error_fare_floor_triggered(flight) is True


def test_flight_above_floor_never_triggers():
    assert error_fare_floor_triggered(_flight(SHORT_HAUL_ERROR_FARE_FLOOR + 5)) is False
