from __future__ import annotations

from datetime import date

import pytest

from trip_hunter.models import AccommodationComparisonGroup, FlightComparisonGroup
from trip_hunter.sampling_targets import (
    FLIGHT_TARGETS,
    HOTEL_TARGETS,
    build_rotating_flight_targets,
    origin_of_the_day,
)


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


# --- origin_of_the_day() (multi-origin round-robin) ------------------------


def test_origin_of_the_day_picks_from_the_given_origins():
    assert origin_of_the_day(["HAM", "BER", "FRA"], today=date(2026, 1, 1)) in {"HAM", "BER", "FRA"}


def test_origin_of_the_day_is_deterministic_for_the_same_date():
    first = origin_of_the_day(["HAM", "BER", "FRA", "MUC", "DUS"], today=date(2026, 3, 15))
    second = origin_of_the_day(["HAM", "BER", "FRA", "MUC", "DUS"], today=date(2026, 3, 15))

    assert first == second


def test_origin_of_the_day_cycles_through_every_origin_over_consecutive_days():
    origins = ["HAM", "BER", "FRA", "MUC", "DUS"]
    start = date(2026, 1, 1)

    seen = {origin_of_the_day(origins, today=date.fromordinal(start.toordinal() + offset)) for offset in range(5)}

    assert seen == set(origins)


def test_origin_of_the_day_defaults_to_config_load_origins(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_ORIGINS", raising=False)
    monkeypatch.delenv("VACATION_HUNTER_ORIGINS", raising=False)
    monkeypatch.setenv("TRIP_HUNTER_ORIGINS", "VIE")

    assert origin_of_the_day(today=date(2026, 1, 1)) == "VIE"


def test_origin_of_the_day_raises_on_empty_origins():
    with pytest.raises(ValueError):
        origin_of_the_day([], today=date(2026, 1, 1))


# --- build_rotating_flight_targets() ----------------------------------------


def test_build_rotating_flight_targets_returns_one_per_template():
    result = build_rotating_flight_targets(today=date(2026, 1, 1), origins=["BER"])

    assert len(result) == len(FLIGHT_TARGETS)


def test_build_rotating_flight_targets_uses_a_single_shared_origin():
    result = build_rotating_flight_targets(today=date(2026, 1, 1), origins=["BER", "FRA"])

    origins_used = {target.origin for target in result}
    assert origins_used == {origin_of_the_day(["BER", "FRA"], today=date(2026, 1, 1))}


def test_build_rotating_flight_targets_preserves_destination_and_dates_from_templates():
    base = [
        FlightComparisonGroup(
            origin="HAM", destination="PMI",
            departure_date=date(2026, 10, 2), return_date=date(2026, 10, 7),
            trip_type=FLIGHT_TARGETS[0].trip_type, currency="EUR",
        )
    ]

    result = build_rotating_flight_targets(today=date(2026, 1, 1), origins=["BER"], base_targets=base)

    assert len(result) == 1
    assert result[0].origin == "BER"
    assert result[0].destination == "PMI"
    assert result[0].departure_date == date(2026, 10, 2)
    assert result[0].return_date == date(2026, 10, 7)
    assert result[0].currency == "EUR"


def test_build_rotating_flight_targets_never_produces_a_cross_product():
    """The whole point of the rotation: never more targets than there are
    explicit trip templates, regardless of how many origins are
    configured."""
    result = build_rotating_flight_targets(
        today=date(2026, 1, 1), origins=["HAM", "BER", "FRA", "MUC", "DUS"]
    )

    assert len(result) == len(FLIGHT_TARGETS)


def test_build_rotating_flight_targets_rotates_across_days():
    day_one = build_rotating_flight_targets(today=date(2026, 1, 1), origins=["BER", "FRA"])
    day_two = build_rotating_flight_targets(today=date(2026, 1, 2), origins=["BER", "FRA"])

    assert {t.origin for t in day_one} != {t.origin for t in day_two}
