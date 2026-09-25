"""Fixed observation targets for the daily sampler (daily_sampler.py).

Resolves the "Frequency-Bias-Gefahr" and "Kein Scheduler" gaps documented
in docs/PRODUCT_SPEC.md ("Deduplikation") and the Trip Hunter handover
briefing: automatic collection needs "eine Observation pro Comparison
Group pro geplantem Messzeitpunkt, nicht 'so oft wie zufällig gesucht
wird'" - a fixed, explicit target list is exactly that fixed sampling
rhythm's input. This module owns ONLY the target list - no scheduling, no
API calls, no persistence. See daily_sampler.py for the runner.

Every target is an EXPLICIT, self-validating comparison group (see
"Explicit Comparison Groups" in docs/PRODUCT_SPEC.md) - never a route
pattern or a date range to expand. Adding a destination means adding one
more explicit entry here, not a loop parameter; that keeps the "no route
loops, no date loops" credit-safety rule (see docs/ARCHITECTURE.md "API
Credit Safety") true by construction, not by runtime discipline.

Flight and hotel targets are deliberately two separate lists, not paired
1:1 - a route and a stay are independent comparison groups (see
FlightComparisonGroup / AccommodationComparisonGroup in models.py), even
when today's real targets happen to describe the same trip.

MULTI-ORIGIN ROTATION (origin_of_the_day / build_rotating_flight_targets):
FLIGHT_TARGETS above stays exactly as it's always been - explicit, HAM-
anchored entries that keep building on the real observation history
already accumulated in data/trip_hunter.db (see docs/PRODUCT_SPEC.md's
"Observation Semantics" and this project's real 5-observation HAM->PMI
baseline) - nothing here ever changes THEIR origin.

Multi-origin support ("flexible Abflughäfen", config.load_origins() -
default HAM/BER/FRA/MUC/DUS) is layered ON TOP, not by turning
FLIGHT_TARGETS into a cross product of origins x destinations x dates
(that would be exactly the unbounded "route loop" this module's own
docstring above forbids). Instead, `build_rotating_flight_targets()`
takes the SAME small set of explicit trip templates (destination + dates
+ trip_type + currency, read from FLIGHT_TARGETS itself so the two can
never drift) and re-emits them with exactly ONE origin - today's, chosen
by `origin_of_the_day()`'s deterministic round-robin over
config.load_origins(). One call, one shared origin, a bounded list the
same length as FLIGHT_TARGETS: never more requests per day than adding
one more explicit hand-written origin's worth of targets would cost, and
every one of them still passes through daily_sampler.py's existing
per-target DUE/cache pre-check before any live SerpApi call - the
"CREDIT-SAFETY GUARANTEE, BY CONSTRUCTION" documented there is untouched.

A rotating target for a non-HAM origin (e.g. BER->PMI) starts with zero
observation history of its own - PriceHistoryRepository keys observations
by the full (origin, destination, dates, trip_type, currency) tuple, so
it never inherits FLIGHT_TARGETS' HAM-route history. It will honestly
report BASELINE_UNAVAILABLE until it accumulates >= 5 of its own
observations, exactly like any new route - see "Baseline Problem" in
docs/PRODUCT_SPEC.md. That's expected, not a bug.

HOTEL_TARGETS is NOT part of this rotation: a hotel's price doesn't
depend on where the guest is flying from, so the existing
destination-keyed hotel targets already cover every origin's stay.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from trip_hunter.config import load_origins
from trip_hunter.engine.feed_sensor import GERMAN_ORIGINS, DealSignal
from trip_hunter.models import AccommodationComparisonGroup, FlightComparisonGroup, TripType

# The real HAM->PMI trip this project has been sampling manually since
# MVP 0.4 (record_price_snapshot.py / record_hotel_price_snapshot.py) -
# already has real observations in data/trip_hunter.db.
FLIGHT_TARGETS: list[FlightComparisonGroup] = [
    FlightComparisonGroup(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    ),
    # A genuine Friday->Sunday weekend getaway, matching
    # weekend_radar_demo.py's HAM->BCN scenario.
    FlightComparisonGroup(
        origin="HAM",
        destination="BCN",
        departure_date=date(2026, 10, 9),
        return_date=date(2026, 10, 11),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    ),
    # A genuine Thursday->Sunday LONG weekend (3 nights, not the 2-night
    # Fri->Sun shape above) - a second, distinct BCN comparison group, not
    # a replacement for it. Deliberately a different week so the two BCN
    # targets track genuinely independent trips, not alternate dates for
    # "the same" one (see "Explicit Comparison Groups" in
    # docs/PRODUCT_SPEC.md - result count must never decide which group
    # "wins"; here there simply are two, on purpose).
    FlightComparisonGroup(
        origin="HAM",
        destination="BCN",
        departure_date=date(2026, 11, 5),
        return_date=date(2026, 11, 8),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    ),
    # A genuine Thursday->Sunday long weekend to Rome.
    FlightComparisonGroup(
        origin="HAM",
        destination="FCO",
        departure_date=date(2026, 11, 19),
        return_date=date(2026, 11, 22),
        trip_type=TripType.ROUND_TRIP,
        currency="EUR",
    ),
]

HOTEL_TARGETS: list[AccommodationComparisonGroup] = [
    AccommodationComparisonGroup(
        destination="PMI",
        check_in=date(2026, 10, 2),
        check_out=date(2026, 10, 7),
        currency="EUR",
    ),
    AccommodationComparisonGroup(
        destination="BCN",
        check_in=date(2026, 10, 9),
        check_out=date(2026, 10, 11),
        currency="EUR",
    ),
    AccommodationComparisonGroup(
        destination="BCN",
        check_in=date(2026, 11, 5),
        check_out=date(2026, 11, 8),
        currency="EUR",
    ),
    AccommodationComparisonGroup(
        destination="FCO",
        check_in=date(2026, 11, 19),
        check_out=date(2026, 11, 22),
        currency="EUR",
    ),
]

# --- ROTATION POOL (Phase 2) -----------------------------------------------------
#
# A wider catalogue of destinations for the ONE daily featured trip - a pure
# repertoire extension: FLIGHT_TARGETS/HOTEL_TARGETS above stay exactly as
# they are (the always-on HAM baseline and the "static flights per run"
# credit math depend on that), and a run still makes the same number of
# live calls. Only WHICH featured trip/origin gets today's single rotating
# slot changes. BCN and FCO/PMI are already in the pool via FLIGHT_TARGETS.
#
# Every new template is HAM-anchored like the originals (the rotation swaps
# in the day's origin, see build_rotating_flight_targets) and has a paired
# hotel entry with identical dates (ROTATION_HOTEL_TARGETS), since the
# featured trip's hotel is sampled with it.
_NEW_ROTATION_TEMPLATES: list[FlightComparisonGroup] = [
    # Metropolen
    FlightComparisonGroup("HAM", "VCE", date(2026, 10, 16), date(2026, 10, 18), TripType.ROUND_TRIP, "EUR"),  # Fri->Sun
    FlightComparisonGroup("HAM", "BGY", date(2026, 10, 23), date(2026, 10, 25), TripType.ROUND_TRIP, "EUR"),  # Fri->Sun
    FlightComparisonGroup("HAM", "STN", date(2026, 11, 13), date(2026, 11, 15), TripType.ROUND_TRIP, "EUR"),  # Fri->Sun
    FlightComparisonGroup("HAM", "VIE", date(2026, 12, 4), date(2026, 12, 6), TripType.ROUND_TRIP, "EUR"),  # Fri->Sun
    # Sonnenziele
    FlightComparisonGroup("HAM", "OPO", date(2026, 10, 30), date(2026, 11, 1), TripType.ROUND_TRIP, "EUR"),  # Fri->Sun
    FlightComparisonGroup("HAM", "FAO", date(2026, 11, 26), date(2026, 11, 29), TripType.ROUND_TRIP, "EUR"),  # Thu->Sun
]

ROTATION_FLIGHT_TEMPLATES: list[FlightComparisonGroup] = FLIGHT_TARGETS + _NEW_ROTATION_TEMPLATES

ROTATION_HOTEL_TARGETS: list[AccommodationComparisonGroup] = HOTEL_TARGETS + [
    AccommodationComparisonGroup(
        destination=t.destination, check_in=t.departure_date, check_out=t.return_date, currency=t.currency
    )
    for t in _NEW_ROTATION_TEMPLATES
]


def featured_rotation_of_the_day(
    templates: list[FlightComparisonGroup] | None = None,
    origins: list[str] | None = None,
    *,
    today: date | None = None,
    exclude: list[FlightComparisonGroup] | None = None,
) -> tuple[FlightComparisonGroup, str]:
    """Today's featured (trip template, origin) pair: exactly ONE, from the
    full templates x origins grid (ROTATION_FLIGHT_TEMPLATES x
    config.load_origins() by default), so over time every destination is
    visited from every configured origin - not just HAM.

    Deterministic per calendar date. Template = day % len(templates) (the
    same rule as featured_trip_of_the_day, so the trip cycles daily) and
    origin = (day // len(templates)) % len(origins) - deliberately NOT
    day % len(origins), which would lock each destination to one origin
    whenever the two list lengths share a factor.

    A combination that would rebuild an existing static target (`exclude`,
    FLIGHT_TARGETS by default - e.g. HAM->PMI on the same dates) is skipped
    in favour of the next grid cell: the slot would otherwise be spent
    re-sampling a group that is already sampled on every run. If EVERY cell
    is excluded (a degenerate grid) the day's raw cell is returned.
    """
    resolved_templates = templates if templates is not None else ROTATION_FLIGHT_TEMPLATES
    resolved_origins = origins if origins is not None else load_origins()
    if not resolved_templates:
        raise ValueError("templates must be a non-empty list")
    if not resolved_origins:
        raise ValueError("origins must be a non-empty list")
    skip = set(exclude if exclude is not None else FLIGHT_TARGETS)

    n, m = len(resolved_templates), len(resolved_origins)
    start = (today or date.today()).toordinal()

    def cell(index: int) -> tuple[FlightComparisonGroup, str]:
        return resolved_templates[index % n], resolved_origins[(index // n) % m]

    for step in range(n * m):
        template, origin = cell(start + step)
        rebuilt = FlightComparisonGroup(
            origin=origin, destination=template.destination, departure_date=template.departure_date,
            return_date=template.return_date, trip_type=template.trip_type, currency=template.currency,
        )
        if rebuilt not in skip:
            return template, origin
    return cell(start)


def origin_of_the_day(origins: list[str] | None = None, *, today: date | None = None) -> str:
    """Deterministic round-robin over the configured origin rotation
    (config.load_origins() by default): picks exactly ONE origin per
    calendar day, cycling through `origins` in order. The same date
    always resolves to the same origin (no per-call/per-process
    randomness) - `build_rotating_flight_targets()` relies on that to
    call this once per run and share the single result across every
    target it builds, which is what keeps the rotation credit-safe (never
    more than one origin's worth of extra targets per day).

    Raises ValueError for an empty origin list - there is no sane origin
    to fall back to, and TRIP_HUNTER_ORIGINS="" already falls back to the
    default cluster in config.load_origins(), so an empty list here can
    only mean an explicit, deliberately-empty `origins` argument.
    """
    resolved_origins = origins if origins is not None else load_origins()
    if not resolved_origins:
        raise ValueError("origins must be a non-empty list")
    resolved_today = today or date.today()
    index = resolved_today.toordinal() % len(resolved_origins)
    return resolved_origins[index]


def build_rotating_flight_targets(
    *,
    today: date | None = None,
    origins: list[str] | None = None,
    base_targets: list[FlightComparisonGroup] | None = None,
) -> list[FlightComparisonGroup]:
    """One additional FlightComparisonGroup per trip template in
    `base_targets` (defaults to FLIGHT_TARGETS), all using TODAY's single
    rotating origin (origin_of_the_day()) in place of each template's own
    origin - see this module's "MULTI-ORIGIN ROTATION" docstring section
    for why this can never explode into a cross product: exactly one
    origin, applied to an already-bounded, already-explicit template list.
    """
    resolved_today = today or date.today()
    origin = origin_of_the_day(origins, today=resolved_today)
    templates = base_targets if base_targets is not None else FLIGHT_TARGETS
    return [
        FlightComparisonGroup(
            origin=origin,
            destination=group.destination,
            departure_date=group.departure_date,
            return_date=group.return_date,
            trip_type=group.trip_type,
            currency=group.currency,
        )
        for group in templates
    ]


def featured_trip_of_the_day(
    templates: list[FlightComparisonGroup] | None = None, *, today: date | None = None
) -> FlightComparisonGroup:
    """Deterministic round-robin over the explicit trip templates in
    FLIGHT_TARGETS (or `templates`): picks exactly ONE per calendar day.
    Same rotation pattern as origin_of_the_day(), applied to a different
    list.

    Used by daily_sampler.py to keep the free-tier SerpApi monthly credit
    budget (250/month) safe: sampling every hotel target AND every
    rotating-origin flight target on every scheduled run would already
    exceed that budget even before counting the always-on FLIGHT_TARGETS
    entries (see daily_sampler.py's "CREDIT BUDGET" docstring section for
    the exact math). So only the ONE hotel target and ONE rotating-origin
    flight target matching today's featured trip are sampled per run -
    every FLIGHT_TARGETS entry itself is still sampled on every run
    regardless of which trip is featured; that continuity is never
    throttled.
    """
    resolved_templates = templates if templates is not None else FLIGHT_TARGETS
    if not resolved_templates:
        raise ValueError("templates must be a non-empty list")
    resolved_today = today or date.today()
    index = resolved_today.toordinal() % len(resolved_templates)
    return resolved_templates[index]


# Safety cap: signal-triggered verification scans per run. daily_sampler.py's
# "CREDIT BUDGET" assumes at most 6 live calls per run; a signal scan REPLACES
# the rotating-origin featured flight target (never adds to it), and this cap
# makes sure that can never grow beyond one.
MAX_SIGNAL_TARGETS_PER_RUN = 1
_SIGNAL_MIN_LEAD_DAYS = 14
_FRIDAY = 4


def _default_signal_dates(today: date) -> tuple[date, date]:
    """Friday->Sunday weekend at least _SIGNAL_MIN_LEAD_DAYS out - the
    project's standard getaway shape (see FLIGHT_TARGETS)."""
    earliest = today + timedelta(days=_SIGNAL_MIN_LEAD_DAYS)
    friday = earliest + timedelta(days=(_FRIDAY - earliest.weekday()) % 7)
    return friday, friday + timedelta(days=2)


def build_signal_flight_targets(
    signals: Iterable[DealSignal],
    *,
    today: date | None = None,
    existing_targets: list[FlightComparisonGroup] | None = None,
    templates: list[FlightComparisonGroup] | None = None,
    max_targets: int = MAX_SIGNAL_TARGETS_PER_RUN,
) -> list[FlightComparisonGroup]:
    """Turn feed-sensor signals (engine/feed_sensor.py) into verification
    scan targets - at most `max_targets` (never more than
    MAX_SIGNAL_TARGETS_PER_RUN, whatever the caller passes), in the
    order the signals arrive (scan_feeds() sorts newest first).

    A signal qualifies only if it is Tier 1 (`is_tier_1`), names a German
    origin and a destination IATA that isn't that origin. Signals whose
    route+dates are already covered by `existing_targets` are skipped (a
    second scan of the same group would only be a wasted DUE check).

    Feeds give no reliable travel dates, so the dates come from the
    explicit `templates` trip (FLIGHT_TARGETS) for that destination if it
    is still upcoming, else the default weekend from _default_signal_dates.
    """
    resolved_today = today or date.today()
    resolved_templates = templates if templates is not None else ROTATION_FLIGHT_TEMPLATES
    covered = set(existing_targets or [])
    cap = max(0, min(max_targets, MAX_SIGNAL_TARGETS_PER_RUN))

    targets: list[FlightComparisonGroup] = []
    for signal in signals:
        if len(targets) >= cap:
            break
        origin = next((o for o in signal.origins if o in GERMAN_ORIGINS), None)
        destination = signal.destination_iata
        if not signal.is_tier_1 or origin is None or not destination or destination == origin:
            continue

        template = next(
            (t for t in resolved_templates if t.destination == destination and t.departure_date > resolved_today),
            None,
        )
        departure, return_ = (
            (template.departure_date, template.return_date)
            if template is not None
            else _default_signal_dates(resolved_today)
        )
        target = FlightComparisonGroup(
            origin=origin, destination=destination, departure_date=departure,
            return_date=return_, trip_type=TripType.ROUND_TRIP, currency="EUR",
        )
        if target in covered:
            continue
        covered.add(target)
        targets.append(target)
    return targets
