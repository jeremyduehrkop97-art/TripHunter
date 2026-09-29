"""engine/flexible_dates.py: deterministic example travel-date windows for
the feed-radar "Urlaubspiraten model" flexible-date combo teaser."""

from __future__ import annotations

from datetime import date

from trip_hunter.engine.flexible_dates import (
    LONG_HAUL_NIGHTS_RANGE,
    SHORT_HAUL_NIGHTS_RANGE,
    WINDOW_COUNT,
    cheapest_window,
    generate_example_windows,
    nights_range_for,
)

_TODAY = date(2026, 9, 26)  # a Saturday, chosen so "next Saturday" edge cases show up


def test_generates_exactly_window_count_windows():
    windows = generate_example_windows("BKK", today=_TODAY)
    assert len(windows) == WINDOW_COUNT == 4


def test_every_window_departs_before_it_returns():
    for departure, return_ in generate_example_windows("BKK", today=_TODAY):
        assert return_ > departure


def test_every_departure_falls_on_a_saturday():
    for departure, _ in generate_example_windows("BKK", today=_TODAY):
        assert departure.weekday() == 5  # Monday=0 ... Saturday=5


def test_every_departure_is_between_one_and_four_months_out():
    for departure, _ in generate_example_windows("BKK", today=_TODAY):
        days_out = (departure - _TODAY).days
        assert 28 <= days_out <= 4 * 31 + 7  # generous month bounds + the Saturday snap


def test_windows_are_sorted_earliest_first():
    windows = generate_example_windows("BKK", today=_TODAY)
    departures = [departure for departure, _ in windows]
    assert departures == sorted(departures)


def test_is_deterministic_for_the_same_today_and_destination():
    first = generate_example_windows("BKK", today=_TODAY)
    second = generate_example_windows("BKK", today=_TODAY)
    assert first == second


def test_long_haul_destination_gets_seven_to_nine_nights():
    for departure, return_ in generate_example_windows("JFK", today=_TODAY):  # JFK is in LONG_HAUL_DESTINATIONS
        nights = (return_ - departure).days
        assert LONG_HAUL_NIGHTS_RANGE[0] <= nights <= LONG_HAUL_NIGHTS_RANGE[1]


def test_short_haul_destination_gets_three_to_four_nights():
    for departure, return_ in generate_example_windows("PMI", today=_TODAY):  # not in LONG_HAUL_DESTINATIONS
        nights = (return_ - departure).days
        assert SHORT_HAUL_NIGHTS_RANGE[0] <= nights <= SHORT_HAUL_NIGHTS_RANGE[1]


def test_unknown_destination_defaults_to_short_haul_never_assumed_long_haul():
    assert nights_range_for("ZZZ") == SHORT_HAUL_NIGHTS_RANGE
    assert nights_range_for(None) == SHORT_HAUL_NIGHTS_RANGE


def test_cheapest_window_is_the_one_with_fewest_nights():
    windows = generate_example_windows("JFK", today=_TODAY)
    cheapest = cheapest_window(windows)
    fewest_nights = min((return_ - departure).days for departure, return_ in windows)
    assert (cheapest[1] - cheapest[0]).days == fewest_nights


def test_cheapest_window_breaks_a_nights_tie_by_earliest_departure():
    windows = (
        (date(2026, 12, 5), date(2026, 12, 8)),   # 3 nights, later
        (date(2026, 11, 7), date(2026, 11, 10)),  # 3 nights, earlier - wins the tie
        (date(2026, 11, 21), date(2026, 11, 26)),  # 5 nights
    )
    assert cheapest_window(windows) == (date(2026, 11, 7), date(2026, 11, 10))


def test_no_today_argument_uses_the_real_current_date():
    from datetime import date as real_date

    windows = generate_example_windows("BKK")
    days_out = (windows[0][0] - real_date.today()).days
    assert 28 <= days_out <= 4 * 31 + 7
