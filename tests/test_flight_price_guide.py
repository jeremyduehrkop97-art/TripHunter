"""monetization/flight_price_guide.py: the per-tier flight-price guide
for the hotel-first reverse combo."""

from __future__ import annotations

from trip_hunter.monetization.flight_price_guide import (
    LONG_HAUL_FLIGHT_GUIDE_EUR,
    MID_HAUL_FLIGHT_GUIDE_EUR,
    SHORT_HAUL_FLIGHT_GUIDE_EUR,
    flight_price_guide_for,
)


def test_short_haul_destination_gets_the_short_haul_guide_price():
    assert flight_price_guide_for("PMI") == SHORT_HAUL_FLIGHT_GUIDE_EUR == 105


def test_mid_haul_destination_gets_the_mid_haul_guide_price():
    assert flight_price_guide_for("DXB") == MID_HAUL_FLIGHT_GUIDE_EUR == 250


def test_long_haul_destination_gets_the_long_haul_guide_price():
    assert flight_price_guide_for("DPS") == LONG_HAUL_FLIGHT_GUIDE_EUR == 550


def test_unresolved_destination_gets_the_short_haul_guide_price():
    """No IATA code at all is never assumed to be farther away than
    Europe - the cheapest, least presumptuous figure applies."""
    assert flight_price_guide_for(None) == SHORT_HAUL_FLIGHT_GUIDE_EUR


def test_all_guide_prices_are_within_the_ranges_the_task_itself_named():
    assert 90 <= SHORT_HAUL_FLIGHT_GUIDE_EUR <= 120
    assert 220 <= MID_HAUL_FLIGHT_GUIDE_EUR <= 280
    assert 500 <= LONG_HAUL_FLIGHT_GUIDE_EUR <= 600
