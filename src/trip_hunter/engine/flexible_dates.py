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

THREE-TIER NIGHTS RANGE (explicit per-destination allowlist below, never
inferred from geography - same pattern as error_fare_floor.py's
MID_HAUL_DESTINATIONS, though that set answers a DIFFERENT question - the
Tier-1 error-fare price bar - and is deliberately not reused here, since a
destination's typical STAY length and its error-fare price ceiling don't
have to agree): a realistic trip length is not the same everywhere -
nobody books a 3-night trip to Phuket, and nobody needs 2 weeks for a
Palma city break.
  - short-haul (Europe/city-trip, the default for anything unlisted):
    3-5 nights.
  - mid-haul (Gulf/Orient, Canary Islands, North Africa): 7-10 nights.
  - long-haul (Southeast Asia, Indian Ocean, Americas, Australia/Africa):
    10-14 nights.

HERO NIGHTS PREFERENCE: which of the WINDOW_COUNT generated nights-counts
the "lead" example (the one in the Telegram hero line / deal-sheet header)
should be. For short- and mid-haul this is still the shortest/cheapest of
the range (a quick getaway IS the attractive "ab" price). For long-haul it
is deliberately the LONGEST of the range instead (14, or as close to it as
the generated set gets) - a 10-night Bali trip is a real option and stays
in the example matrix, but leading with it as "the" Bali trip undersells
the destination and reads as an oddly short intercontinental stay; the
realistic, expected length is what should headline. Because the hero
example is then not necessarily the cheapest one on display,
alerts/instant_alert_formatter.py checks explicitly whether it happens to
also be the cheapest before ever printing "ab" ("from" - implying nothing
cheaper is shown) versus a plain "für" ("for") in the hero line - it may
not always be the case anymore, so it is worth checking there, not assumed.

Departures: 5, 8, 11 and 14 weeks from `today`, each snapped forward to the
next Saturday (the standard leisure-trip start day already used by
instant_alert_formatter's weekend-trip logic) - so every window falls
within the requested "1 to 4 months out" range while staying deterministic
and typical.
"""

from __future__ import annotations

from datetime import date, timedelta

WINDOW_COUNT = 4
_WEEK_OFFSETS = (5, 8, 11, 14)
_SATURDAY = 5

SHORT_HAUL_NIGHTS_RANGE = (3, 5)
MID_HAUL_NIGHTS_RANGE = (7, 10)
LONG_HAUL_NIGHTS_RANGE = (10, 14)

# Gulf/Orient, Canary Islands, North Africa - typically a week-to-ten-days
# trip, longer than a European city break but short of a genuine
# intercontinental one.
MID_HAUL_DESTINATIONS: frozenset[str] = frozenset(
    {
        # Gulf / Orient
        "DXB", "DOH", "AUH",
        # Canary Islands
        "TFS", "TFN", "LPA", "ACE", "FUE",
        # North Africa
        "RAK", "CAI", "HRG", "SSH",
    }
)

# Southeast Asia, Indian Ocean, the Americas, Australia/Southern Africa -
# a real intercontinental flight, where a realistic trip runs 10-14
# nights, not a long-haul-for-a-long-weekend outlier.
LONG_HAUL_DESTINATIONS: frozenset[str] = frozenset(
    {
        # Southeast Asia
        "HKT", "DPS", "BKK", "KBV", "CNX", "SIN", "HKG", "TPE",
        # Indian Ocean
        "MLE", "SEZ",
        # Americas
        "LAX", "SFO", "MIA", "JFK", "EWR", "BOS", "CHI", "YYZ", "YYC",
        "CUN", "PUJ", "MEX", "HAV",
        # East Asia / Australia / Southern Africa
        "TYO", "SEL", "SYD", "CPT",
        # South Asia
        "DEL", "BOM",
    }
)


def _next_saturday(day: date) -> date:
    return day + timedelta(days=(_SATURDAY - day.weekday()) % 7)


def nights_range_for(destination_iata: str | None) -> tuple[int, int]:
    """(min, max) nights for example windows to this destination - the
    long- or mid-haul range for the explicit allowlists above, else the
    short-haul range. An unknown destination is never assumed long- or
    mid-haul (matches error_fare_floor's own "unlisted = short-haul"
    convention)."""
    if destination_iata in LONG_HAUL_DESTINATIONS:
        return LONG_HAUL_NIGHTS_RANGE
    if destination_iata in MID_HAUL_DESTINATIONS:
        return MID_HAUL_NIGHTS_RANGE
    return SHORT_HAUL_NIGHTS_RANGE


def _hero_nights_preference(destination_iata: str | None) -> int:
    """Which nights-count (always somewhere in nights_range_for's range,
    since it's literally that range's own lo/hi) the hero example should
    prefer - see the module docstring's "HERO NIGHTS PREFERENCE" section."""
    lo, hi = nights_range_for(destination_iata)
    return hi if destination_iata in LONG_HAUL_DESTINATIONS else lo


def _spread_indices(count: int, size: int) -> list[int]:
    """`count` indices into a `size`-long sequence, evenly spread from the
    first to the last element inclusive (so both endpoints of a nights
    range always show up among the generated windows, however short or
    long that range is, however few or many windows are generated)."""
    if size <= 1:
        return [0] * count
    return [round(i * (size - 1) / (count - 1)) for i in range(count)]


def generate_example_windows(
    destination_iata: str | None, *, today: date | None = None
) -> tuple[tuple[date, date], ...]:
    """WINDOW_COUNT (departure, return) example windows, earliest first.
    Deterministic in both `today` and `destination_iata` (only via the
    nights range for that destination's tier) - never randomised, never a
    fabricated real-looking one-off date."""
    resolved_today = today or date.today()
    lo, hi = nights_range_for(destination_iata)
    night_options = list(range(lo, hi + 1))
    nights_sequence = [night_options[i] for i in _spread_indices(WINDOW_COUNT, len(night_options))]
    windows = []
    for weeks, nights in zip(_WEEK_OFFSETS[:WINDOW_COUNT], nights_sequence):
        departure = _next_saturday(resolved_today + timedelta(weeks=weeks))
        windows.append((departure, departure + timedelta(days=nights)))
    return tuple(windows)


def hero_window(
    windows: tuple[tuple[date, date], ...], destination_iata: str | None
) -> tuple[date, date]:
    """The window to lead with in the hero line/deal-sheet header - the
    one whose nights-count is closest to `_hero_nights_preference` for
    this destination (exactly that value, for windows built by
    `generate_example_windows`, since _spread_indices guarantees both
    range endpoints appear); earliest departure breaks a tie. Never empty
    - `windows` always has WINDOW_COUNT entries from
    `generate_example_windows`."""
    preference = _hero_nights_preference(destination_iata)
    return min(windows, key=lambda window: (abs((window[1] - window[0]).days - preference), window[0]))
