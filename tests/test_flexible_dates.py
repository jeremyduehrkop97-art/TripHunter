"""engine/flexible_dates.py: deterministic example travel-date windows.
Used today by engine/daily_scanner.py to pick which dates to run a real,
live SerpApi search on - the feed-radar "Urlaubspiraten model" flexible-
date combo teaser that used to consume these for an ALERT's displayed
price was retired after its fabricated dates caused real price mismatches
on click-through (see this module's own "FORMER USE, NOW RETIRED")."""

from __future__ import annotations

from datetime import date

import pytest

from trip_hunter.engine.flexible_dates import (
    LONG_HAUL_DESTINATIONS,
    LONG_HAUL_NIGHTS_RANGE,
    MID_HAUL_DESTINATIONS,
    MID_HAUL_NIGHTS_RANGE,
    SHORT_HAUL_NIGHTS_RANGE,
    WINDOW_COUNT,
    generate_example_windows,
    hero_window,
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


def test_unknown_destination_defaults_to_short_haul_never_assumed_longer():
    assert nights_range_for("ZZZ") == SHORT_HAUL_NIGHTS_RANGE
    assert nights_range_for(None) == SHORT_HAUL_NIGHTS_RANGE


# --- three-tier nights range -----------------------------------------------------


@pytest.mark.parametrize("code", ["PMI", "BCN", "FCO", "LIS", "MAD", "LON", "CDG", "FRU"])
def test_short_haul_destinations_get_three_to_five_nights(code):
    assert nights_range_for(code) == SHORT_HAUL_NIGHTS_RANGE
    for departure, return_ in generate_example_windows(code, today=_TODAY):
        nights = (return_ - departure).days
        assert SHORT_HAUL_NIGHTS_RANGE[0] <= nights <= SHORT_HAUL_NIGHTS_RANGE[1]


@pytest.mark.parametrize("code", ["DXB", "DOH", "TFS", "LPA", "ACE", "RAK"])
def test_mid_haul_destinations_get_seven_to_ten_nights(code):
    assert code in MID_HAUL_DESTINATIONS
    assert nights_range_for(code) == MID_HAUL_NIGHTS_RANGE
    for departure, return_ in generate_example_windows(code, today=_TODAY):
        nights = (return_ - departure).days
        assert MID_HAUL_NIGHTS_RANGE[0] <= nights <= MID_HAUL_NIGHTS_RANGE[1]


@pytest.mark.parametrize("code", ["HKT", "DPS", "BKK", "KBV", "MLE", "SEZ", "LAX", "SFO", "MIA", "JFK", "CUN", "PUJ", "SYD", "CPT"])
def test_long_haul_destinations_never_go_below_ten_nights(code):
    """The exact reported bug: Phuket/Bali-style destinations got example
    windows as short as 3 nights, which nobody actually books for a
    long-haul flight."""
    assert code in LONG_HAUL_DESTINATIONS
    assert nights_range_for(code) == LONG_HAUL_NIGHTS_RANGE
    for departure, return_ in generate_example_windows(code, today=_TODAY):
        nights = (return_ - departure).days
        assert nights >= 10
        assert LONG_HAUL_NIGHTS_RANGE[0] <= nights <= LONG_HAUL_NIGHTS_RANGE[1]


@pytest.mark.parametrize("code", ["HKT", "DPS", "BKK", "MLE"])
def test_long_haul_destinations_include_a_fourteen_night_window(code):
    """Both ends of the range must actually appear among the generated
    windows (see _spread_indices) - in particular the preferred hero
    value, 14 nights, for every long-haul destination."""
    nights = [(return_ - departure).days for departure, return_ in generate_example_windows(code, today=_TODAY)]
    assert 14 in nights
    assert 10 in nights


def test_short_and_mid_haul_windows_span_both_ends_of_their_range():
    short_nights = [(r - d).days for d, r in generate_example_windows("PMI", today=_TODAY)]
    assert 3 in short_nights and 5 in short_nights

    mid_nights = [(r - d).days for d, r in generate_example_windows("DXB", today=_TODAY)]
    assert 7 in mid_nights and 10 in mid_nights


# --- hero window selection ---------------------------------------------------


def test_hero_window_for_short_haul_is_the_cheapest_shortest_one():
    windows = generate_example_windows("PMI", today=_TODAY)
    lead = hero_window(windows, "PMI")
    fewest_nights = min((r - d).days for d, r in windows)
    assert (lead[1] - lead[0]).days == fewest_nights


def test_hero_window_for_long_haul_prefers_fourteen_nights_not_the_minimum():
    """The exact new requirement: a long-haul hero example should show a
    realistic ~2-week trip, not the technically-cheapest 10-night one."""
    windows = generate_example_windows("DPS", today=_TODAY)
    lead = hero_window(windows, "DPS")
    assert (lead[1] - lead[0]).days == 14


def test_hero_window_for_mid_haul_is_still_the_cheapest_shortest_one():
    windows = generate_example_windows("DXB", today=_TODAY)
    lead = hero_window(windows, "DXB")
    fewest_nights = min((r - d).days for d, r in windows)
    assert (lead[1] - lead[0]).days == fewest_nights


def test_hero_window_tie_break_is_earliest_departure():
    windows = (
        (date(2026, 12, 5), date(2026, 12, 8)),   # 3 nights, later
        (date(2026, 11, 7), date(2026, 11, 10)),  # 3 nights, earlier - wins the tie
        (date(2026, 11, 21), date(2026, 11, 26)),  # 5 nights
    )
    assert hero_window(windows, "PMI") == (date(2026, 11, 7), date(2026, 11, 10))


def test_no_today_argument_uses_the_real_current_date():
    windows = generate_example_windows("BKK")
    days_out = (windows[0][0] - date.today()).days
    assert 28 <= days_out <= 4 * 31 + 7


def test_alerts_instant_alert_formatter_no_longer_imports_this_module_at_all():
    """engine/flexible_dates.py's own "FORMER USE, NOW RETIRED": the
    alert formatter used to call generate_example_windows/hero_window to
    fabricate an example date for a signal with no real one - removed,
    since a real search link built from that fabricated date could (and
    did) show a different price than the one actually advertised. This is
    the by-construction guarantee a dated signal's booking link is never
    anything but a real, exact date: there is no code path left in that
    module able to call into this one at all any more."""
    import inspect

    import trip_hunter.alerts.instant_alert_formatter as formatter

    source = inspect.getsource(formatter)
    assert "generate_example_windows" not in source and "hero_window" not in source


def test_a_dealsignal_with_a_real_travel_date_produces_a_booking_link_for_exactly_those_dates():
    """The task's own worked example: a feed signal naming a real, exact
    date range (parse_travel_date_range) must use exactly THOSE dates in
    its booking link - never a fabricated window from this module."""
    from trip_hunter.engine.feed_sensor import DealSignal
    from trip_hunter.alerts.instant_alert_formatter import signal_deal_sheet_url

    signal = DealSignal(
        source="fly4free", title="Flights to Bangkok from Frankfurt for €399", link="https://x/1",
        origins=("FRA",), tier_1_reasons=(), destination="Bangkok", destination_iata="BKK",
        price=399.0, travel_dates="12.10.–19.10.2026",
    )
    url = signal_deal_sheet_url(signal)

    assert "dep=2026-10-12" in url and "ret=2026-10-19" in url
