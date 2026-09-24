"""Controlled scheduler-runner: goes through the fixed targets in
sampling_targets.py once and, for each one, decides whether a snapshot is
actually due today - never more than that. Resolves Blocker #3
(Frequency-Bias) and Blocker #5 (Kein Scheduler) from the Trip Hunter
handover briefing.

CREDIT BUDGET (free-tier SerpApi: ~100 credits/month): the GitHub Actions
schedule (.github/workflows/daily_sample.yml) runs this 3x/week (Sun/Tue/
Thu), ~13-14 times/month. Sampling every FLIGHT_TARGETS entry every run
(4) already costs ~4 x 14 = 56/month. Adding a rotating-origin flight
target AND a hotel target for EVERY trip template on EVERY run, as an
earlier version of this module did, would add 4 (rotating flights) + 4
(hotels) more per run = 8 x 14 = 112 more/month - 168/month total, well
over the free tier even before buying more.

So `run()` (the real entry point) only ever adds, per run:
  - EVERY FLIGHT_TARGETS entry, unthrottled - this is the project's real,
    long-running HAM baseline continuity and is never sacrificed to the
    budget (see "MULTI-ORIGIN ROTATION" in sampling_targets.py).
  - exactly ONE rotating-origin flight target for exactly ONE trip - today's
    "featured trip" (sampling_targets.featured_rotation_of_the_day(): one
    cell of the ROTATION_FLIGHT_TEMPLATES x configured-origins grid, so
    every destination in the wider Phase-2 pool is eventually visited from
    every hub, not just HAM).
  - exactly ONE hotel target - the SAME featured trip's hotel
    (_hotel_target_for_flight_template() over ROTATION_HOTEL_TARGETS).
Widening the rotation pool never changes these counts.
That's 4 (static) + 1 (rotating) + 1 (hotel) = 6 potential live calls per
run, worst case 6 x 14 = 84/month - inside the requested 85-90 budget
with a small margin, and well under the hard 100 limit. Every one of
those 6 still goes through the exact same per-target DUE/cache pre-check
documented below before it can trigger a live call - the "CREDIT-SAFETY
GUARANTEE" is unaffected, this budgeting only shrinks WHICH targets are
even offered to it each run.

Trade-off, stated plainly: only one of the 4 destinations gets a fresh
hotel observation (and a fresh rotating-origin flight observation) per
run, cycling through all 4 over time rather than every run - hotel/
rotating-origin baselines now build up roughly 4x slower than before this
budget was introduced. FLIGHT_TARGETS' own (HAM) observation cadence is
completely unaffected.

FEED SIGNALS (engine/feed_sensor.py): before building the target list,
`run()` scans the free RSS deal feeds (no SerpApi credits). If a Tier-1
signal names a German origin and a destination IATA, that route becomes
today's dynamic scan candidate and TAKES THE PLACE of the rotating-origin
featured flight target - the budget above (6 live calls/run) therefore
does not grow, and sampling_targets.MAX_SIGNAL_TARGETS_PER_RUN caps it at
one signal scan per run. It goes through the same DUE/cache pre-check as
every other target. Skip with --no-signals; a broken/blocked feed never
fails the run.

Run with:
    python -m trip_hunter.daily_sampler            # live run
    python -m trip_hunter.daily_sampler --dry-run   # show what's due, no requests

This is a RUNNER, not a daemon: it makes no attempt to schedule itself
(no cron/launchd install, no internal timer/sleep loop). Running it once a
day (e.g. via an external cron entry) is what turns it into the "fixed
sampling rhythm" docs/PRODUCT_SPEC.md's "Frequency-Bias-Gefahr" note calls
for - that scheduling decision stays outside this codebase on purpose,
matching the project's established "credit safety first" caution: this
module only guarantees that ANY single invocation, however triggered,
never spends an uncontrolled number of credits.

THE CREDIT-SAFETY GUARANTEE, BY CONSTRUCTION:
For every target, this module only ever reaches the shared
_record_snapshot(...) helper (record_price_snapshot.py /
record_hotel_price_snapshot.py - the same, already-hardened functions the
manual commands use) when its own pre-check has PROVEN, via two
non-destructive reads only (a DB query, a FileCache.get() - neither
triggers a request), that:
  1. no observation for this exact comparison group exists for TODAY's
     calendar date yet, AND
  2. the provider's own response cache for this exact search is empty
     (not merely stale - literally absent or expired).
Only then is `_record_snapshot(..., source_label="LIVE RESPONSE", ...)`
called - and it is called with that hardcoded label truthfully, not
guessed, because reaching that line already proved there's nothing to
read from cache. There is no code path that calls it more than once per
target, and --dry-run skips the call entirely even for a due target. See
test_daily_sampler.py for the direct, call-counting proof of this.

Deliberately reuses the exact same _record_snapshot(...) functions the
manual commands (record_price_snapshot.py, record_hotel_price_snapshot.py)
already use and this project has already tested extensively (Cache Hit
Rule, dedup, baseline reporting) - this module adds a pre-check in front
of them, it does not reimplement any of their logic.

AUTOMATIC ALERT CHECK (after a successful live snapshot): once a target
was actually DUE and its snapshot stored, the affected route's data is
re-evaluated for an instant-alert-worthy Deal and, if one qualifies,
dispatched via dispatch/telegram.py. This check is a SEPARATE, ALWAYS-FREE
step - it never triggers another provider search. It rebuilds the Deal
from whatever is now the most recently stored flight/hotel observation in
data/trip_hunter.db, using replay_providers.py's zero-network stand-ins
(the same technique dispatch/test_dispatch.py's --real mode already
uses), not a fresh live/cached search - so it can never spend a second
credit for the same target, and running it after either a flight or a
hotel snapshot (or skipping it entirely with --no-alerts) never changes
how many live searches this run makes. A flight target and its paired
hotel target (same destination/dates) are checked at most once each per
run even if both were due, via a set() keyed on the route.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from enum import Enum
from typing import Callable

from trip_hunter.accommodation_price_history_repository import (
    DEFAULT_DB_PATH as ACCOMMODATION_DB_PATH,
)
from trip_hunter.accommodation_price_history_repository import AccommodationPriceHistoryRepository
from trip_hunter.build_newsletter import DEFAULT_INSTANT_ALERT_CRITERIA, DEFAULT_TIER_3_CRITERIA
from trip_hunter.caching import FileCache, flight_search_cache_key, hotel_search_cache_key
from trip_hunter.config import MissingConfigError, load_serpapi_config
from trip_hunter.dispatch.telegram import dispatch_deal_alert
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.engine.deal_filters import DealFilterCriteria, filter_deals
from trip_hunter.engine.feed_sensor import DealSignal, scan_feeds
from trip_hunter.models import AccommodationComparisonGroup, Deal, FlightComparisonGroup
from trip_hunter.price_history_repository import DEFAULT_DB_PATH as FLIGHT_DB_PATH
from trip_hunter.price_history_repository import PriceHistoryRepository
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.flight_provider import FlightProvider
from trip_hunter.providers.serpapi_accommodation_provider import SerpApiAccommodationProvider
from trip_hunter.providers.serpapi_client import SerpApiClient
from trip_hunter.providers.serpapi_flight_provider import SerpApiGoogleFlightsProvider
from trip_hunter.providers.serpapi_hotels_client import SerpApiHotelsClient
from trip_hunter.record_hotel_price_snapshot import _record_snapshot as _record_hotel_snapshot
from trip_hunter.record_price_snapshot import _record_snapshot as _record_flight_snapshot
from trip_hunter.replay_providers import ReplayAccommodationProvider, ReplayFlightProvider
from trip_hunter.sampling_targets import (
    FLIGHT_TARGETS,
    ROTATION_HOTEL_TARGETS,
    build_rotating_flight_targets,
    build_signal_flight_targets,
    featured_rotation_of_the_day,
)


class SamplingStatus(str, Enum):
    DUE = "DUE"
    ALREADY_OBSERVED_TODAY = "ALREADY_OBSERVED_TODAY"
    CACHE_ACTIVE = "CACHE_ACTIVE"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trip_hunter.daily_sampler",
        description=(
            "Go through the fixed targets in sampling_targets.py once; run a "
            "controlled snapshot for whichever ones are actually due today."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show which targets are due today. Never calls a provider.",
    )
    parser.add_argument(
        "--no-alerts",
        action="store_true",
        help="Skip the automatic post-snapshot alert check/Telegram dispatch (data collection only).",
    )
    parser.add_argument(
        "--no-signals",
        action="store_true",
        help="Don't scan the RSS deal feeds; use the regular featured rotating-origin target only.",
    )
    return parser


def _fetch_signals() -> list[DealSignal]:
    """Tier-1 feed signals, or [] on any failure - free HTTP only, and a
    broken feed must never fail the sampler run."""
    try:
        return scan_feeds(tier_1_only=True)
    except Exception as exc:  # noqa: BLE001 - defensive: signals are optional
        print(f"Feed-Sensor übersprungen ({type(exc).__name__}).")
        return []


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _flight_already_observed_today(
    repository: PriceHistoryRepository, group: FlightComparisonGroup, today: date
) -> bool:
    observations = repository.get_observations(
        group.origin, group.destination, group.departure_date, group.return_date,
        group.trip_type, group.currency,
    )
    return any(observation.observed_at.date() == today for observation in observations)


def _hotel_already_observed_today(
    repository: AccommodationPriceHistoryRepository, group: AccommodationComparisonGroup, today: date
) -> bool:
    observations = repository.get_observations(
        group.destination, group.check_in, group.check_out, group.currency
    )
    return any(observation.observed_at.date() == today for observation in observations)


def _flight_is_cached(cache: FileCache, group: FlightComparisonGroup) -> bool:
    key = flight_search_cache_key(
        group.origin, group.destination,
        group.departure_date.isoformat(), group.return_date.isoformat(), group.currency,
    )
    return cache.get(key) is not None


def _hotel_is_cached(cache: FileCache, group: AccommodationComparisonGroup) -> bool:
    key = hotel_search_cache_key(
        group.destination, group.check_in.isoformat(), group.check_out.isoformat(), group.currency
    )
    return cache.get(key) is not None


def _decide_flight(
    repository: PriceHistoryRepository, cache: FileCache, group: FlightComparisonGroup, today: date
) -> SamplingStatus:
    if _flight_already_observed_today(repository, group, today):
        return SamplingStatus.ALREADY_OBSERVED_TODAY
    if _flight_is_cached(cache, group):
        return SamplingStatus.CACHE_ACTIVE
    return SamplingStatus.DUE


def _decide_hotel(
    repository: AccommodationPriceHistoryRepository,
    cache: FileCache,
    group: AccommodationComparisonGroup,
    today: date,
) -> SamplingStatus:
    if _hotel_already_observed_today(repository, group, today):
        return SamplingStatus.ALREADY_OBSERVED_TODAY
    if _hotel_is_cached(cache, group):
        return SamplingStatus.CACHE_ACTIVE
    return SamplingStatus.DUE


_SKIP_MESSAGE = {
    SamplingStatus.ALREADY_OBSERVED_TODAY: "Already observed today -> skipped",
    SamplingStatus.CACHE_ACTIVE: "Cache active -> skipped",
}


def _process_flight_target(
    group: FlightComparisonGroup,
    provider: FlightProvider,
    repository: PriceHistoryRepository,
    cache: FileCache,
    *,
    dry_run: bool,
    today: date,
    observed_at: datetime,
) -> SamplingStatus:
    label = f"{group.origin} → {group.destination} ({group.departure_date} – {group.return_date})"
    status = _decide_flight(repository, cache, group, today)

    if status is not SamplingStatus.DUE:
        print(f"  {label}: {_SKIP_MESSAGE[status]}")
        return status

    if dry_run:
        print(f"  {label}: DUE (dry-run, kein Request)")
        return status

    print(f"  {label}: DUE -> running snapshot")
    # Reaching here already proved the cache was empty for this exact
    # search, so "LIVE RESPONSE" is a true label, not an assumption - see
    # module docstring "THE CREDIT-SAFETY GUARANTEE".
    _record_flight_snapshot(
        provider, repository, group, "LIVE RESPONSE", group.currency, observed_at=observed_at
    )
    return status


def _process_hotel_target(
    group: AccommodationComparisonGroup,
    provider: AccommodationProvider,
    repository: AccommodationPriceHistoryRepository,
    cache: FileCache,
    *,
    dry_run: bool,
    today: date,
    observed_at: datetime,
) -> SamplingStatus:
    label = f"{group.destination} ({group.check_in} – {group.check_out})"
    status = _decide_hotel(repository, cache, group, today)

    if status is not SamplingStatus.DUE:
        print(f"  {label}: {_SKIP_MESSAGE[status]}")
        return status

    if dry_run:
        print(f"  {label}: DUE (dry-run, kein Request)")
        return status

    print(f"  {label}: DUE -> running snapshot")
    _record_hotel_snapshot(provider, repository, group, "LIVE RESPONSE", observed_at=observed_at)
    return status


def _matching_flight_targets(
    hotel_group: AccommodationComparisonGroup, flight_targets: list[FlightComparisonGroup]
) -> list[FlightComparisonGroup]:
    """A hotel target has no deal of its own (DealEngine always needs a
    flight to anchor a Deal) - find every flight target for the SAME trip
    (same destination, same dates) so a hotel-triggered alert check has
    something to pair it with. Returns a LIST, not a single match: since
    sampling_targets.py's multi-origin rotation can put several origins'
    flight targets on the same destination+dates (e.g. HAM->PMI and
    today's rotating BER->PMI), a hotel-triggered check re-evaluates every
    one of them, not just the first - each origin has its own, separate
    observation history/baseline. Empty list if no flight target covers
    this stay at all (nothing to check against; not an error).
    """
    return [
        flight_group
        for flight_group in flight_targets
        if (
            flight_group.destination == hotel_group.destination
            and flight_group.departure_date == hotel_group.check_in
            and flight_group.return_date == hotel_group.check_out
            and flight_group.currency == hotel_group.currency
        )
    ]


def _hotel_target_for_flight_template(
    flight_template: FlightComparisonGroup, hotel_targets: list[AccommodationComparisonGroup]
) -> AccommodationComparisonGroup | None:
    """The hotel target for the SAME trip (same destination, same dates,
    same currency) as `flight_template` - the inverse of
    _matching_flight_targets. Used to find today's featured trip's paired
    hotel target (see run()'s "CREDIT BUDGET" wiring). None if no hotel
    target covers this route (nothing to check against; not an error).
    """
    for hotel_group in hotel_targets:
        if (
            hotel_group.destination == flight_template.destination
            and hotel_group.check_in == flight_template.departure_date
            and hotel_group.check_out == flight_template.return_date
            and hotel_group.currency == flight_template.currency
        ):
            return hotel_group
    return None


def _check_and_dispatch_alert(
    flight_group: FlightComparisonGroup,
    flight_repository: PriceHistoryRepository,
    accommodation_repository: AccommodationPriceHistoryRepository,
    *,
    alert_criteria: DealFilterCriteria,
    tier3_criteria: DealFilterCriteria | None,
    dispatch_fn: Callable[[Deal], bool],
) -> bool:
    """Re-evaluates `flight_group`'s route from whatever is now the most
    recently stored flight/hotel observation and dispatches an alert if it
    clears `alert_criteria` - or, only if nothing did, `tier3_criteria`
    (the VIP-exclusive "Good Deal" tier, see engine/alert_tier.py; pass
    None to disable this fallback entirely). The two criteria's
    allowed_deal_types are disjoint by construction (see
    DEFAULT_INSTANT_ALERT_CRITERIA / DEFAULT_TIER_3_CRITERIA in
    build_newsletter.py), so there's no ambiguity about which tier a
    qualifying deal belongs to - dispatch_deal_alert itself decides the
    actual channel routing from the deal alone (classify_alert_tier),
    this only decides WHETHER to dispatch at all. Zero network calls:
    both providers here replay stored data (replay_providers.py), never
    search live or read the SerpApi response cache. Returns True iff an
    alert was actually dispatched successfully.
    """
    label = f"{flight_group.origin} → {flight_group.destination}"

    flight_observations = flight_repository.get_observations(
        flight_group.origin, flight_group.destination,
        flight_group.departure_date, flight_group.return_date,
        flight_group.trip_type, flight_group.currency,
    )
    if not flight_observations:
        print(f"  {label}: keine Flug-Beobachtung vorhanden - kein Alert-Check möglich.")
        return False
    latest_flight_observation = max(flight_observations, key=lambda o: o.observed_at)

    hotel_observations = accommodation_repository.get_observations(
        flight_group.destination, flight_group.departure_date, flight_group.return_date, flight_group.currency
    )
    latest_hotel_observation = (
        max(hotel_observations, key=lambda o: o.observed_at) if hotel_observations else None
    )

    engine = DealEngine(
        flight_provider=ReplayFlightProvider(latest_flight_observation),
        accommodation_provider=ReplayAccommodationProvider(latest_hotel_observation),
        price_history_repository=flight_repository,
        accommodation_price_history_repository=accommodation_repository,
    )
    deals = engine.find_trip_deals(
        origin=flight_group.origin, destination=flight_group.destination,
        earliest_departure=flight_group.departure_date, latest_departure=flight_group.departure_date,
        return_date=flight_group.return_date,
    )
    qualifying_deals = filter_deals(deals, alert_criteria)
    if not qualifying_deals and tier3_criteria is not None:
        qualifying_deals = filter_deals(deals, tier3_criteria)

    if not qualifying_deals:
        print(f"  {label}: kein alert-würdiger Deal.")
        return False

    print(f"  {label}: alert-würdiger Deal ({qualifying_deals[0].deal_type.value}) -> Telegram-Versand.")
    return dispatch_fn(qualifying_deals[0])


def run_sampler(
    flight_provider: FlightProvider,
    accommodation_provider: AccommodationProvider,
    flight_repository: PriceHistoryRepository,
    accommodation_repository: AccommodationPriceHistoryRepository,
    cache: FileCache,
    *,
    flight_targets: list[FlightComparisonGroup],
    hotel_targets: list[AccommodationComparisonGroup],
    dry_run: bool = False,
    today: date | None = None,
    observed_at: datetime | None = None,
    send_alerts: bool = True,
    alert_criteria: DealFilterCriteria = DEFAULT_INSTANT_ALERT_CRITERIA,
    tier3_criteria: DealFilterCriteria | None = DEFAULT_TIER_3_CRITERIA,
    dispatch_fn: Callable[[Deal], bool] = dispatch_deal_alert,
) -> list[SamplingStatus]:
    """The testable core: takes already-constructed providers/repositories/
    cache so tests can inject fakes and a tmp_path DB, never a real HTTP
    call. `run()` below is the only place that wires up the real SerpApi
    clients and the real runtime databases.

    Returns one SamplingStatus per target processed (flight targets first,
    then hotel targets, in list order) - purely for tests/callers that want
    to assert on the outcome without re-parsing printed output.

    `send_alerts=False` (--no-alerts) skips the automatic alert check
    entirely - a pure data-collection run. `dispatch_fn` defaults to the
    real dispatch_deal_alert (Free/VIP dual-channel routing, see
    dispatch/telegram.py) but is injectable for tests.
    """
    today = today or datetime.now(timezone.utc).date()
    observed_at = observed_at or datetime.now(timezone.utc)

    statuses: list[SamplingStatus] = []
    routes_to_check: set[FlightComparisonGroup] = set()

    print("Flug-Ziele:")
    for group in flight_targets:
        status = _process_flight_target(
            group, flight_provider, flight_repository, cache,
            dry_run=dry_run, today=today, observed_at=observed_at,
        )
        statuses.append(status)
        if status is SamplingStatus.DUE and not dry_run:
            routes_to_check.add(group)
    print()

    print("Hotel-Ziele:")
    for group in hotel_targets:
        status = _process_hotel_target(
            group, accommodation_provider, accommodation_repository, cache,
            dry_run=dry_run, today=today, observed_at=observed_at,
        )
        statuses.append(status)
        if status is SamplingStatus.DUE and not dry_run:
            for matching_flight_target in _matching_flight_targets(group, flight_targets):
                routes_to_check.add(matching_flight_target)
    print()

    if send_alerts and routes_to_check:
        print("Alert-Check:")
        for flight_group in routes_to_check:
            _check_and_dispatch_alert(
                flight_group, flight_repository, accommodation_repository,
                alert_criteria=alert_criteria, tier3_criteria=tier3_criteria, dispatch_fn=dispatch_fn,
            )
        print()

    due_count = sum(1 for status in statuses if status is SamplingStatus.DUE)
    skipped_count = len(statuses) - due_count
    if dry_run:
        print(f"Zusammenfassung: {due_count} fällig (dry-run, kein Request gesendet), {skipped_count} übersprungen.")
    else:
        print(f"Zusammenfassung: {due_count} Snapshot(s) ausgeführt, {skipped_count} übersprungen.")

    return statuses


def run(argv: list[str] | None = None) -> None:
    """CLI entry point: parses arguments, loads config, wires up the real
    SerpApi providers and the REAL runtime databases, then delegates to
    `run_sampler`. Kept thin, deliberately hard to unit test in isolation -
    see run_sampler's docstring for the provider-agnostic core tests exercise.
    """
    args = _parse_args(argv)
    today = datetime.now(timezone.utc).date()
    observed_at = datetime.now(timezone.utc)

    # See module docstring "CREDIT BUDGET": only today's ONE featured trip
    # gets a rotating-origin flight target and a hotel target - every
    # FLIGHT_TARGETS entry itself is still unthrottled, added below.
    featured_trip, rotation_origin = featured_rotation_of_the_day(today=today)
    rotating_flight_targets = build_rotating_flight_targets(
        today=today, origins=[rotation_origin], base_targets=[featured_trip]
    )
    signal_targets = (
        []
        if args.no_signals
        else build_signal_flight_targets(
            _fetch_signals(), today=today, existing_targets=FLIGHT_TARGETS + rotating_flight_targets
        )
    )
    if signal_targets:
        # Replaces (never adds to) the rotating featured flight target -
        # see module docstring "FEED SIGNALS".
        rotating_flight_targets = signal_targets
    featured_hotel_target = _hotel_target_for_flight_template(featured_trip, ROTATION_HOTEL_TARGETS)
    featured_hotel_targets = [featured_hotel_target] if featured_hotel_target is not None else []

    print("TRIP HUNTER — DAILY SAMPLER")
    print(f"Datum: {today.isoformat()}")
    print(f"Rotierender Origin heute: {rotation_origin}")
    print(
        f"Featured Trip heute (Hotel + Rotations-Flug): {featured_trip.destination} "
        f"({featured_trip.departure_date} – {featured_trip.return_date})"
    )
    for target in signal_targets:
        print(
            f"Feed-Signal (Tier 1) -> Verifikations-Scan: {target.origin} → {target.destination} "
            f"({target.departure_date} – {target.return_date}) statt Rotations-Ziel"
        )
    if args.dry_run:
        print("Modus: DRY RUN (keine Requests)")
    if args.no_alerts:
        print("Alert-Check: deaktiviert (--no-alerts)")
    print()

    try:
        config = load_serpapi_config()
    except MissingConfigError as exc:
        print(f"Configuration missing: {exc}")
        return

    cache = FileCache(ttl_seconds=config.cache_ttl_seconds)
    flight_repository = PriceHistoryRepository(db_path=FLIGHT_DB_PATH)
    accommodation_repository = AccommodationPriceHistoryRepository(db_path=ACCOMMODATION_DB_PATH)

    flight_client = SerpApiClient(api_key=config.api_key, timeout_seconds=config.request_timeout_seconds)
    flight_provider = SerpApiGoogleFlightsProvider(client=flight_client, cache=cache, currency=config.currency)

    hotels_client = SerpApiHotelsClient(api_key=config.api_key, timeout_seconds=config.request_timeout_seconds)
    accommodation_provider = SerpApiAccommodationProvider(
        client=hotels_client, cache=cache, currency=config.currency
    )

    run_sampler(
        flight_provider,
        accommodation_provider,
        flight_repository,
        accommodation_repository,
        cache,
        flight_targets=FLIGHT_TARGETS + rotating_flight_targets,
        hotel_targets=featured_hotel_targets,
        dry_run=args.dry_run,
        today=today,
        observed_at=observed_at,
        send_alerts=not args.no_alerts,
    )


if __name__ == "__main__":
    run()
