from __future__ import annotations

from trip_hunter.models import AccommodationComparisonGroup, FlightComparisonGroup
from trip_hunter.sampling_targets import FLIGHT_TARGETS, HOTEL_TARGETS


def test_flight_targets_are_non_empty():
    assert len(FLIGHT_TARGETS) > 0


def test_hotel_targets_are_non_empty():
    assert len(HOTEL_TARGETS) > 0


def test_flight_targets_are_all_flight_comparison_groups():
    assert all(isinstance(target, FlightComparisonGroup) for target in FLIGHT_TARGETS)


def test_hotel_targets_are_all_accommodation_comparison_groups():
    assert all(isinstance(target, AccommodationComparisonGroup) for target in HOTEL_TARGETS)


def test_flight_targets_have_no_exact_duplicates():
    assert len(FLIGHT_TARGETS) == len(set(FLIGHT_TARGETS))


def test_hotel_targets_have_no_exact_duplicates():
    assert len(HOTEL_TARGETS) == len(set(HOTEL_TARGETS))


def test_flight_and_hotel_targets_are_separate_lists():
    """Klare Trennung: flight and hotel targets are independent lists, not
    a single merged/paired structure."""
    assert FLIGHT_TARGETS is not HOTEL_TARGETS
    assert type(FLIGHT_TARGETS[0]) is not type(HOTEL_TARGETS[0])
