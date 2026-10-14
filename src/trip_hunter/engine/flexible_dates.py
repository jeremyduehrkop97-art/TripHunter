"""Example travel-date windows for picking WHICH dates to search next.

FORMER USE, NOW RETIRED: this module used to also back alerts/
instant_alert_formatter.py's "Urlaubspiraten model" - a feed signal with
no exact date got a fabricated example window, and a REAL flight/hotel
search LINK for that fabricated date. That silently caused exactly the
price mismatch it claimed to avoid: a user clicking "Flug buchen" landed
on a live Kiwi/Aviasales search for a date the feed never actually priced
(sometimes landing on Christmas/New Year by sheer bad luck of the fixed
weekly offsets below), never the deal's real price. Removed - a feed
signal with no exact date now gets either a dateless search or the feed's
own real article link (DealSignal.link via alerts/
instant_alert_formatter._original_deal_link), never an invented day. See
that module's and feed_radar's git history for the full incident.

CURRENT USE: engine/daily_scanner.py's own active SerpApi search needs
SOME date to search on - there the "fabrication" risk doesn't exist at
all, because the result IS a live, verified price for exactly that date;
nothing about it is ever shown as if it were the feed's own number. These
functions stay exactly as they were for that one purpose.

Also used there, same way, for its interactive "3-5 Termine" date matrix
(matrix_candidate_windows below): each candidate window gets its OWN live
SerpApi search before ever reaching a matrix entry, so every price shown
there is just as genuinely verified as the single-window case above -
never the feed-signal mistake this module's own "FORMER USE" section
describes.

REPRESENTATIVE MONTH WINDOW (representative_month_window /
default_representative_month, 2026-10, explicit product decision): a
feed signal naming only a month ("November 2026") or no date at all used
to fall all the way back to a 100% pure flight deal, with no hotel at
all - "Trip Hunter soll IMMER wie ein geniales All-in-Urlaubspaket
wirken". This picks ONE deterministic example window within the named
(or, failing that, the next calendar) month and pairs it with a real
hotel/flight search for exactly that window - the same mechanism the
"FORMER USE" section above retired, but safe here for a reason that
section's teaser never had: the message and deal sheet BOTH say,
explicitly, "Reisebeispiel" ("example window") - never presented as the
feed's own confirmed date the way the retired teaser silently was. A
user who clicks through already knows the exact date is illustrative;
the combined price itself is exactly as honest as this project's
real-date All-in-Paket case already is (a genuinely real flight price
plus an openly-labelled hotel ESTIMATE) - only WHICH week within the
month is itself an example, and that is said out loud.

WHAT THIS IS NOT: a prediction, a live search result, or a promise that a
seat exists on these exact dates - whichever caller uses them, "deterministic,
clearly-labelled example dates" is as far as this module's own claim ever
goes; what a caller then DOES with the result (verify it live, as
daily_scanner.py does, or merely display it, which no longer happens) is
entirely that caller's own responsibility. Deterministic on purpose: the
same destination, computed twice on the same day, must produce the same
windows (repeatable and testable).

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

# "3 bis maximal 5" interactive date-matrix slots (engine/daily_scanner.py
# only - see matrix_candidate_windows below). One week apart on purpose -
# close enough together that all candidates stay plausible alternatives
# for the same trip, far enough apart that they're genuinely different
# booking options, not the same week twice.
MAX_MATRIX_WINDOWS = 5
MATRIX_WEEK_STEP = 1

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
        "HKT", "DPS", "BKK", "KBV", "CNX", "SIN", "HKG", "TPE", "SAI", "KTI",
        # Indian Ocean
        "MLE", "SEZ",
        # Americas
        "LAX", "SFO", "MIA", "JFK", "EWR", "BOS", "CHI", "YYZ", "YYC",
        "CUN", "PUJ", "MEX", "HAV",
        # East Asia / Australia / Southern Africa
        "TYO", "SEL", "SYD", "CPT",
        # South Asia
        "DEL", "BOM", "KTM", "CMB",
        # East Africa
        "NBO", "ZNZ",
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


def matrix_candidate_windows(
    hero_departure: date, hero_return: date, *, count: int = MAX_MATRIX_WINDOWS, today: date | None = None
) -> tuple[tuple[date, date], ...]:
    """`count` candidate (departure, return) windows for
    engine/daily_scanner.py's own interactive date matrix - the hero
    window itself first, then `count - 1` more departures spread
    MATRIX_WEEK_STEP week(s) apart, ALL sharing the hero's exact night
    count. Deliberately a DIFFERENT question from generate_example_windows
    (which spreads several NIGHT COUNTS over fixed week offsets, "how
    long should the trip be") - this is "which week works for the SAME
    trip length", so every entry here is a genuinely comparable
    alternative, never a shorter or longer stay mislabeled as one.

    Every departure is on a Saturday (hero_departure already is, since it
    always comes from hero_window/generate_example_windows) and `>= today`
    - in practice always true for a hero window 5-14 weeks out, this guard
    just means a caller can never get a past date back even if it changes
    that assumption later. SAFE to pair with a REAL price per window
    (unlike this module's own "FORMER USE, NOW RETIRED" feed-signal
    teaser): daily_scanner.py runs one more live SerpApi search for each
    of these, so every window this produces a matrix entry for is
    eventually backed by its own genuinely verified price, never a
    fabricated date."""
    nights = (hero_return - hero_departure).days
    resolved_today = today or date.today()
    windows: list[tuple[date, date]] = []
    week = 0
    while len(windows) < count:
        departure = _next_saturday(hero_departure + timedelta(weeks=week * MATRIX_WEEK_STEP))
        if departure >= resolved_today:
            windows.append((departure, departure + timedelta(days=nights)))
        week += 1
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


# --- representative month window (see module docstring's own section) -----------

EUROPE_WEEKEND_NIGHTS = 4  # "Donnerstag bis Montag" - Europe / city-trip example window
LONG_HAUL_WINDOW_NIGHTS = 7  # "zweiter Dienstag bis dritter Dienstag" - intercontinental example window


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th (1-indexed) occurrence of `weekday` (Monday=0 ... Sunday=6)
    in `year`-`month`."""
    first = date(year, month, 1)
    first_occurrence = first + timedelta(days=(weekday - first.weekday()) % 7)
    return first_occurrence + timedelta(weeks=n - 1)


def representative_month_window(
    month: int, year: int, *, destination_iata: str | None = None
) -> tuple[date, date]:
    """ONE deterministic, explicitly-an-example (departure, return) window
    within `year`-`month` - see this module's own "REPRESENTATIVE MONTH
    WINDOW" docstring section for why this is safe to pair with a real
    deep link, unlike the retired per-signal combo teaser above.

    Long-haul/mid-haul destinations (this module's own existing
    LONG_HAUL_DESTINATIONS / MID_HAUL_DESTINATIONS sets, never a separate
    allowlist): the 2nd Tuesday through the following week's Tuesday -
    LONG_HAUL_WINDOW_NIGHTS (7) nights, a realistic intercontinental stay.
    Everything else (the short-haul/Europe default): the 2nd Thursday
    through the following Monday - EUROPE_WEEKEND_NIGHTS (4) nights, a
    realistic city-trip weekend."""
    is_long_haul = destination_iata in LONG_HAUL_DESTINATIONS or destination_iata in MID_HAUL_DESTINATIONS
    if is_long_haul:
        departure = _nth_weekday_of_month(year, month, weekday=1, n=2)  # 2nd Tuesday
        return departure, departure + timedelta(days=LONG_HAUL_WINDOW_NIGHTS)
    departure = _nth_weekday_of_month(year, month, weekday=3, n=2)  # 2nd Thursday
    return departure, departure + timedelta(days=EUROPE_WEEKEND_NIGHTS)


def default_representative_month(*, today: date | None = None) -> tuple[int, int]:
    """(month, year) to use when a signal names no date at all, not even
    a bare month ("Flexible Reisetermine") - the next calendar month from
    `today`, deterministic and always still ahead of `today`."""
    resolved_today = today or date.today()
    if resolved_today.month == 12:
        return 1, resolved_today.year + 1
    return resolved_today.month + 1, resolved_today.year
