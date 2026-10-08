"""Daily Route Scanner: actively scans a curated list of top DACH vacation
routes via SerpApi/Google Flights once a day, instead of only waiting on
third-party deal feeds (engine/feed_sensor.py / feed_radar.py). Resolves
the "we only react to what a blog happens to post" gap the project's own
handover briefing named.

"QUALITÄT VOR QUANTITÄT" - no alert duty, no upload compulsion. A found
flight is only ever pushed once it clears this project's EXISTING
benchmark discount gate (engine/route_benchmark.get_route_benchmark +
is_deal_price, exactly as feed_radar.py already applies them - this
module calls feed_radar.flight_deal_discount/is_pushworthy directly
rather than re-deriving a second copy of the same rule, so the active
scanner and the passive feed radar can never silently drift apart on what
counts as a deal): >= MIN_FLIGHT_DISCOUNT_PERCENT (30%) for Economy, >=
MIN_BUSINESS_DISCOUNT_PERCENT (40%) for Business/First (this module only
ever searches Economy - cabin_class stays "economy" throughout - but
reusing is_pushworthy rather than hardcoding 30% here means a future
cabin_class-aware search path needs zero changes to this file). No deal
found this run is a perfectly normal, silent outcome, never a problem to
work around.

SERPAPI BUDGET, BY CONSTRUCTION: MAX_REQUESTS_PER_DAY (4) live searches at
most per run, once a day (.github/workflows/daily_scanner.yml) - ~122
credits/month. The EXISTING daily_sampler.py already spends up to ~114
credits/month (4x/week, 6 calls/run - see its own "CREDIT BUDGET" note).
Together that is ~236/month, comfortably inside the SerpApi Free plan's
250 searches/month, with a small deliberate buffer. The task that
commissioned this module originally asked for 10-15 requests/day; at a
DAILY cadence that alone would cost 300-450/month - RIGHT AWAY over
budget even before daily_sampler.py's own usage, and combined with it
could silence BOTH modules' access to SerpApi for the rest of whatever
month hit the cap. Per the user's own explicit choice (see the session
this module was built in), the request cap was reduced to 4/day instead -
the literal "once a day" cadence was kept, the literal "10-15" figure was
not, exactly this project's established "implement the request, but
never silently comply with a number that would break a hard constraint"
convention (the same it already applies to the SerpApi 6-calls/run rule
itself - "no trimming" there means this module adds an INDEPENDENT,
separately-budgeted request allowance, never shares or steals from
daily_sampler's own 6).

ROUTES (ORIGINS x DESTINATIONS, see both constants below): a curated 7x13
= 91-cell grid (7 DACH origins, 13 top vacation destinations across
short/mid/long-haul). At 4 cells/day, routes_for_today() rotates through
the FULL grid roughly once every 23 days (91 / 4), deterministically (same
calendar date -> same cells, never randomised) and without skipping or
repeating a cell within one full pass - the same day-indexed rotation
convention sampling_targets.featured_rotation_of_the_day() already
established for the daily sampler, generalised here from 1 cell/day to
MAX_REQUESTS_PER_DAY cells/day.

DATES: engine/flexible_dates.generate_example_windows() + hero_window()
(already built for the "Urlaubspiraten model" feed-signal teaser) pick
ONE representative (departure, return) window per destination - "typische
Urlaubsfenster" 5-14 weeks out, with a nights-count matching that
destination's own tier (short/mid/long-haul) - rather than this module
inventing a second, independent date-window policy.

HISTORY: every genuinely LIVE (never a cache hit - see _scan_route's own
docstring for why, the same "Cache Hit Rule"
record_price_snapshot.py/daily_sampler.py already follow) search result is
stored via PriceHistoryRepository.add_observation, so
engine/route_benchmark.py's own median-of-real-history branch organically
gets more data to work with over time, exactly as the task asked.

DEDUPLICATION: scanner_history_repository.ScannerHistoryRepository, a
dedicated history distinct from feed_seen_repository.py (see that module's
own docstring for why) - the same route + exact date window is suppressed
for SCANNER_SUPPRESSION_DAYS (7, "nicht mehrmals pro Woche"), never
forever, so a persisting or returning deal can be reported again later.

DISPATCH: every qualifying deal is wrapped as a DealSignal (deal_lead=
"flight", source="daily_scanner") and sent through the EXISTING
dispatch/telegram.dispatch_signal_alert - VIP always; the Free channel too
only for a "seltener Mega-Drop" (MEGA_DROP_DISCOUNT_PERCENT, 50%, or an
absolute Tier-1 price - feed_sensor._tier_1_reasons' own, already-
established bar) via that function's own existing is_tier_1 handling -
no new dispatch logic was needed for this, Tier-1 signals already go to
Free per dispatch_signal_alert's existing rules (and, since MVP of the
Business/First gate, NEVER for a Business/First signal - moot here since
this module never scans anything but Economy).

DRY-RUN IS DIFFERENT HERE THAN IN feed_radar.py - READ THIS: feed_radar's
--dry-run still scans for real because that scan is free (RSS feeds, 0
SerpApi credits) - only the SEND is skipped. This module's scan itself is
what costs the budget, so --dry-run here skips the SerpApi search
ENTIRELY and only prints which routes/windows WOULD be scanned today
(routes_for_today's own output) - a safe, free preview of the day's
rotation plan, never a priced one. Confirming a real price still needs a
live run.

Run with:
    python -m trip_hunter.engine.daily_scanner --dry-run   # free preview, no SerpApi calls
    python -m trip_hunter.engine.daily_scanner --live      # (the default) real scan + possible alerts
    python -m trip_hunter.engine.daily_scanner              # same as --live
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Callable

from trip_hunter.alerts.airport_names import city_name
from trip_hunter.caching import FileCache, flight_search_cache_key
from trip_hunter.config import MissingConfigError, load_serpapi_config
from trip_hunter.dispatch.telegram import dispatch_signal_alert
from trip_hunter.engine.feed_sensor import DealSignal, _tier_1_reasons
from trip_hunter.engine.flexible_dates import generate_example_windows, hero_window
from trip_hunter.feed_radar import flight_deal_discount, is_pushworthy
from trip_hunter.models import FlightComparisonGroup, PriceObservation, TripType
from trip_hunter.price_history_repository import (
    DEFAULT_DB_PATH,
    PriceHistoryRepository,
    observation_from_search_results,
)
from trip_hunter.providers.errors import FlightProviderError
from trip_hunter.providers.flight_provider import FlightProvider
from trip_hunter.providers.serpapi_client import SerpApiClient
from trip_hunter.providers.serpapi_flight_provider import SerpApiGoogleFlightsProvider
from trip_hunter.scanner_history_repository import ScannerHistoryRepository

# 7 DACH departure airports, exactly the task's own list - deliberately
# this module's OWN explicit tuple, not config.load_origins() (that one's
# env-configurable default is DACH-GERMANY-only, HAM/BER/FRA/MUC/DUS - no
# VIE/ZRH - a different, narrower list for a different purpose).
ORIGINS: tuple[str, ...] = ("FRA", "MUC", "BER", "DUS", "HAM", "VIE", "ZRH")

# 13 curated top vacation destinations, grouped by haul length exactly as
# the task named them (every code already exists in
# engine/flexible_dates.py's own haul-length allowlists, reused there for
# nights-range purposes - never re-classified independently here).
SHORT_HAUL_ROUTES: tuple[str, ...] = ("PMI", "BCN", "LIS", "FCO")
MID_HAUL_ROUTES: tuple[str, ...] = ("DXB", "TFS", "RAK")
LONG_HAUL_ROUTES: tuple[str, ...] = ("JFK", "MIA", "BKK", "DPS", "NBO", "CUN")
DESTINATIONS: tuple[str, ...] = SHORT_HAUL_ROUTES + MID_HAUL_ROUTES + LONG_HAUL_ROUTES

# See module docstring's "SERPAPI BUDGET, BY CONSTRUCTION" - the user's own
# explicit choice, reduced from the task's original "10-15/day" ask to
# keep this module's own monthly usage, ADDED to daily_sampler.py's
# existing ~114/month, safely inside the SerpApi Free plan's 250/month cap.
MAX_REQUESTS_PER_DAY = 4

# "ein seltener Mega-Drop" - the task's own named figure. A signal this
# steep (or an absolute Tier-1 price, feed_sensor._tier_1_reasons) is
# marked Tier-1 so dispatch_signal_alert's EXISTING rules also reach the
# Free channel for it, same as any other Tier-1 signal.
MEGA_DROP_DISCOUNT_PERCENT = 50.0

_CURRENCY = "EUR"


def routes_for_today(
    today: date | None = None, *, max_requests: int = MAX_REQUESTS_PER_DAY
) -> list[tuple[str, str]]:
    """`max_requests` (origin, destination) cells from the ORIGINS x
    DESTINATIONS grid for `today` - deterministic (same date -> same
    cells, never randomised). Advances a single global index by
    `max_requests` per calendar day (today.toordinal() * max_requests),
    so the full 7x13=91-cell grid is covered roughly once every 91 /
    max_requests days with no cell skipped and no cell repeated within
    one pass - the same day-indexed convention
    sampling_targets.featured_rotation_of_the_day() already established
    for the daily sampler (there: one cell/day; here: generalised to
    several cells/day), destination-major (destination = cell % n,
    origin = cell // n % m - deliberately not `day % m` for the origin,
    which would lock each destination to one fixed origin whenever the
    two list lengths share a factor)."""
    resolved_today = today or date.today()
    n, m = len(DESTINATIONS), len(ORIGINS)
    start = resolved_today.toordinal() * max_requests
    cells = []
    for step in range(max_requests):
        index = start + step
        destination = DESTINATIONS[index % n]
        origin = ORIGINS[(index // n) % m]
        cells.append((origin, destination))
    return cells


def _scan_route(
    provider: FlightProvider,
    repository: PriceHistoryRepository,
    comparison_group: FlightComparisonGroup,
    *,
    cache: FileCache | None,
    observed_at: datetime | None = None,
) -> PriceObservation | None:
    """Exactly ONE search for `comparison_group` - provider.search_flights,
    one call; the provider's own response cache transparently avoids a
    second LIVE request for an already-cached exact search - reduced to at
    most one PriceObservation (observation_from_search_results, never
    looped over every offer - see price_history_repository.py's own
    warning on why that would bias history). Stored in `repository` ONLY
    when the search was genuinely live, never for a cache hit (the same
    "Cache Hit Rule" record_price_snapshot.py's own _record_snapshot
    already follows: a cache hit re-reads an old response, not a new
    market measurement) - detected the same non-destructive way, checking
    `cache` BEFORE calling search_flights, never after.

    Deliberately a thin, independent sibling of record_price_snapshot.
    _record_snapshot rather than a call to it: that function only PRINTS
    its result for a human to read and returns nothing - this one needs
    the resulting price back immediately, in the same run, to evaluate it
    against engine/route_benchmark.get_route_benchmark."""
    origin, destination = comparison_group.origin, comparison_group.destination
    departure_date, return_date = comparison_group.departure_date, comparison_group.return_date

    was_cached_before_search = False
    if cache is not None:
        cache_key = flight_search_cache_key(
            origin, destination, departure_date.isoformat(), return_date.isoformat(), comparison_group.currency,
        )
        was_cached_before_search = cache.get(cache_key) is not None

    try:
        offers = provider.search_flights(origin, destination, departure_date, departure_date, return_date=return_date)
    except FlightProviderError as exc:
        print(f"Daily-Scanner: Suche fehlgeschlagen ({origin} → {destination}, {type(exc).__name__}).")
        return None

    observation = observation_from_search_results(
        offers, comparison_group, observed_at=observed_at or datetime.now(timezone.utc)
    )
    if observation is not None and not was_cached_before_search:
        repository.add_observation(observation)
    return observation


def _travel_dates_text(departure: date, return_date: date) -> str:
    """"12.10.–19.10.2026" - the exact shape
    feed_sensor.parse_travel_date_range expects, so a scanner signal gets
    the plain fixed alert layout for a real, confirmed date (never the
    flexible-combo teaser, which is only for a signal with NO confirmed
    date at all)."""
    return f"{departure.strftime('%d.%m.')}–{return_date.strftime('%d.%m.%Y')}"


def _build_signal(
    origin: str, destination: str, departure: date, return_date: date, price: float, *, now: datetime
) -> DealSignal:
    """The scan result as a DealSignal - the SAME shape engine/feed_sensor.py
    produces from a third-party feed title, so every existing downstream
    path (feed_radar.is_pushworthy, alerts/instant_alert_formatter.py,
    dispatch/telegram.dispatch_signal_alert) treats a scanner find exactly
    like a feed find, no special-casing anywhere else in the codebase.
    cabin_class stays "economy" - this module never searches a premium
    cabin (see module docstring)."""
    destination_city = city_name(destination)
    origin_city = city_name(origin)
    title = f"{origin_city} nach {destination_city}: {price:.0f} {_CURRENCY} (Daily Route Scanner)"
    link = f"https://trip-hunter.de/daily-scanner/{origin}-{destination}/{departure.isoformat()}_{return_date.isoformat()}"
    return DealSignal(
        source="daily_scanner",
        title=title,
        link=link,
        origins=(origin,),
        tier_1_reasons=_tier_1_reasons(title, price, destination, ()),
        destination=destination_city,
        destination_iata=destination,
        price=price,
        travel_dates=_travel_dates_text(departure, return_date),
        published=now,
        deal_lead="flight",
        cabin_class="economy",
    )


@dataclass(frozen=True)
class DailyScanResult:
    planned: int = 0  # dry-run only: routes that WOULD be scanned
    scanned: int = 0
    candidates: int = 0  # cleared the benchmark discount gate (is_pushworthy)
    suppressed: int = 0  # a candidate, but already alerted within SCANNER_SUPPRESSION_DAYS
    sent: int = 0
    failed: int = 0


def scan_and_dispatch(
    *,
    provider: FlightProvider | None = None,
    repository: PriceHistoryRepository | None = None,
    scanner_history: ScannerHistoryRepository | None = None,
    dispatch_fn: Callable[[DealSignal], bool] | None = None,
    routes: list[tuple[str, str]] | None = None,
    max_requests: int = MAX_REQUESTS_PER_DAY,
    cache: FileCache | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> DailyScanResult:
    """One scanner pass: up to `max_requests` (origin, destination) cells
    (routes_for_today() by default), each scanned via ONE live search at
    most (_scan_route), evaluated against the EXISTING benchmark discount
    gate (feed_radar.is_pushworthy - never a second, independently
    re-derived copy of that rule), deduplicated against
    `scanner_history` (ScannerHistoryRepository, SCANNER_SUPPRESSION_DAYS),
    and dispatched via `dispatch_fn` (dispatch/telegram.dispatch_signal_alert
    by default) when it's a genuinely new, qualifying deal. Never raises -
    a single route's search failure (_scan_route) is logged and skipped,
    the rest of the run continues.

    `dry_run=True` makes NO SerpApi request at all (see module docstring's
    "DRY-RUN IS DIFFERENT HERE") - it only prints and counts the routes
    that WOULD be scanned today (`planned`), everything else stays 0.
    """
    moment = now or datetime.now(timezone.utc)
    today = moment.date()
    resolved_routes = routes if routes is not None else routes_for_today(today, max_requests=max_requests)

    if dry_run:
        for origin, destination in resolved_routes:
            windows = generate_example_windows(destination, today=today)
            departure, return_date = hero_window(windows, destination)
            print(
                f"Daily-Scanner (dry-run, 0 SerpApi-Credits): würde scannen - "
                f"{origin} → {destination} ({departure.isoformat()}–{return_date.isoformat()})."
            )
        print(f"Daily-Scanner (dry-run): {len(resolved_routes)} Routen geplant, nichts gesendet.")
        return DailyScanResult(planned=len(resolved_routes))

    dispatch_fn = dispatch_fn or dispatch_signal_alert
    resolved_repository = repository if repository is not None else PriceHistoryRepository(db_path=DEFAULT_DB_PATH)
    resolved_history = scanner_history if scanner_history is not None else ScannerHistoryRepository(db_path=DEFAULT_DB_PATH)
    if provider is None:
        raise ValueError("provider is required for a live run (dry_run=False)")

    scanned = candidates = suppressed = sent = failed = 0
    for origin, destination in resolved_routes:
        windows = generate_example_windows(destination, today=today)
        departure, return_date = hero_window(windows, destination)
        comparison_group = FlightComparisonGroup(
            origin=origin, destination=destination, departure_date=departure, return_date=return_date,
            trip_type=TripType.ROUND_TRIP, currency=_CURRENCY,
        )
        observation = _scan_route(provider, resolved_repository, comparison_group, cache=cache, observed_at=moment)
        scanned += 1
        label = f"{origin} → {destination} ({departure.isoformat()}–{return_date.isoformat()})"
        if observation is None:
            print(f"Daily-Scanner: kein vergleichbares Angebot gefunden - {label}.")
            continue

        signal = _build_signal(origin, destination, departure, return_date, observation.price, now=moment)
        _is_deal, discount_percent = flight_deal_discount(signal)
        if discount_percent >= MEGA_DROP_DISCOUNT_PERCENT and not signal.is_tier_1:
            signal = replace(signal, tier_1_reasons=(f"discount>={MEGA_DROP_DISCOUNT_PERCENT:.0f}%",))
        price_label = f"{observation.price:.0f} {_CURRENCY} ({discount_percent:+.0f}% vs. Marktbenchmark)"

        if not is_pushworthy(signal):
            print(f"Daily-Scanner: kein Deal - {label}: {price_label}.")
            continue
        candidates += 1

        if resolved_history.was_recently_alerted(origin, destination, departure, return_date, now=moment):
            suppressed += 1
            print(f"Daily-Scanner: bereits kürzlich gemeldet, übersprungen - {label}: {price_label}.")
            continue

        if dispatch_fn(signal):
            resolved_history.mark_alerted(origin, destination, departure, return_date, now=moment)
            sent += 1
            print(f"Daily-Scanner: gesendet - {label}: {price_label}.")
        else:
            failed += 1
            print(f"Daily-Scanner: Versand fehlgeschlagen - {label}: {price_label}.")

    print(
        f"Daily-Scanner: {scanned} Routen gescannt, {candidates} Deal-Kandidaten, {sent} gesendet, "
        f"{suppressed} unterdrückt (kürzlich gemeldet), {failed} fehlgeschlagen."
    )
    return DailyScanResult(scanned=scanned, candidates=candidates, suppressed=suppressed, sent=sent, failed=failed)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trip_hunter.engine.daily_scanner",
        description=(
            "Actively scan a curated list of top DACH vacation routes via SerpApi/Google Flights once a day."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Free preview only: print today's planned routes, make NO SerpApi request.",
    )
    mode.add_argument(
        "--live", action="store_true",
        help="Real scan (the default) - up to --max-requests live SerpApi searches, possible Telegram alerts.",
    )
    parser.add_argument(
        "--max-requests", type=int, default=MAX_REQUESTS_PER_DAY,
        help=f"Cap of live SerpApi searches this run (default: {MAX_REQUESTS_PER_DAY}).",
    )
    return parser


def run(argv: list[str] | None = None) -> DailyScanResult:
    """CLI entry point (the daily workflow). `--live` is only the explicit,
    documented opposite of `--dry-run` - it changes nothing by itself,
    since a real scan is already the default."""
    args = _build_parser().parse_args(argv)
    print("TRIP HUNTER — DAILY ROUTE SCANNER")
    if args.dry_run:
        print("Modus: DRY RUN (0 SerpApi-Credits)")

    if args.dry_run:
        return scan_and_dispatch(dry_run=True, max_requests=max(0, args.max_requests), now=datetime.now(timezone.utc))

    try:
        config = load_serpapi_config()
    except MissingConfigError as exc:
        print(f"Configuration missing: {exc}")
        return DailyScanResult()

    client = SerpApiClient(api_key=config.api_key, timeout_seconds=config.request_timeout_seconds)
    cache = FileCache(ttl_seconds=config.cache_ttl_seconds)
    provider = SerpApiGoogleFlightsProvider(client=client, cache=cache, currency=config.currency)
    repository = PriceHistoryRepository(db_path=DEFAULT_DB_PATH)
    scanner_history = ScannerHistoryRepository(db_path=DEFAULT_DB_PATH)

    return scan_and_dispatch(
        provider=provider, repository=repository, scanner_history=scanner_history,
        cache=cache, max_requests=max(0, args.max_requests), now=datetime.now(timezone.utc),
    )


if __name__ == "__main__":
    run()
