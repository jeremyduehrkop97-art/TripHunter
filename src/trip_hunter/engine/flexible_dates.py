"""Example travel-date windows for a feed-radar signal with no exact date
("Urlaubspiraten model": the feed only ever names a destination and a
headline price, sometimes a month - never a concrete departure day, so we
can't quote a real specific flight for a real specific date the way the
sampler does for its own SerpApi-verified Deals).

WHAT THIS IS NOT: a prediction, a live search result, or a promise that a
seat exists on these exact dates. It is 3-4 DETERMINISTIC, clearly-labelled
example windows spread across the next 1-4 months, purely so the alert and
deal.html can show "for instance, these dates" with real, live, dated
search links behind them (build_flight_link / build_hotel_link - see
instant_alert_formatter.signal_combo_lines) - never a specific fabricated
flight. Deterministic on purpose: the same signal, formatted twice on the
same day, must produce the same windows (repeatable, testable, and it
never looks like the bot is guessing differently each time).

Nights per window: 7-9 for long-haul destinations (engine/error_fare_floor.
LONG_HAUL_DESTINATIONS), 3-4 for everything else (short/mid-haul, matching
this project's own nonstop-short-haul weekend-trip convention elsewhere).
Departures: 5, 8, 11 and 14 weeks from `today`, each snapped forward to the
next Saturday (the standard leisure-trip start day already used by
instant_alert_formatter's weekend-trip logic) - so every window falls
within the requested "1 to 4 months out" range while staying deterministic
and typical.
"""

from __future__ import annotations

from datetime import date, timedelta

from trip_hunter.engine.error_fare_floor import LONG_HAUL_DESTINATIONS

WINDOW_COUNT = 4
_WEEK_OFFSETS = (5, 8, 11, 14)
_SATURDAY = 5

LONG_HAUL_NIGHTS_RANGE = (7, 9)
SHORT_HAUL_NIGHTS_RANGE = (3, 4)


def _next_saturday(day: date) -> date:
    return day + timedelta(days=(_SATURDAY - day.weekday()) % 7)


def nights_range_for(destination_iata: str | None) -> tuple[int, int]:
    """(min, max) nights for example windows to this destination - the
    long-haul range if `destination_iata` is in the explicit LONG_HAUL_
    DESTINATIONS allowlist, else the short/mid-haul range. An unknown
    destination is never assumed long-haul (matches error_fare_floor's own
    "unlisted = short-haul" convention)."""
    if destination_iata in LONG_HAUL_DESTINATIONS:
        return LONG_HAUL_NIGHTS_RANGE
    return SHORT_HAUL_NIGHTS_RANGE


def generate_example_windows(
    destination_iata: str | None, *, today: date | None = None
) -> tuple[tuple[date, date], ...]:
    """WINDOW_COUNT (departure, return) example windows, earliest first.
    Deterministic in both `today` and `destination_iata` (only via the
    long- vs. short-haul nights range) - never randomised, never a
    fabricated real-looking one-off date."""
    resolved_today = today or date.today()
    lo, hi = nights_range_for(destination_iata)
    night_options = list(range(lo, hi + 1))
    windows = []
    for index, weeks in enumerate(_WEEK_OFFSETS[:WINDOW_COUNT]):
        departure = _next_saturday(resolved_today + timedelta(weeks=weeks))
        nights = night_options[index % len(night_options)]
        windows.append((departure, departure + timedelta(days=nights)))
    return tuple(windows)


def cheapest_window(windows: tuple[tuple[date, date], ...]) -> tuple[date, date]:
    """The window to lead with in the hero line - fewest nights (so, at a
    constant nightly hotel rate, the lowest combo total); earliest
    departure breaks a tie. Never empty - `windows` always has
    WINDOW_COUNT entries from `generate_example_windows`."""
    return min(windows, key=lambda window: ((window[1] - window[0]).days, window[0]))
