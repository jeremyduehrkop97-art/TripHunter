"""Export CLI: runs the real deal pipeline once, curates it for two
different audiences, and writes all three channel exports to disk.

Run with:
    python -m trip_hunter.build_newsletter                  # writes to output/
    python -m trip_hunter.build_newsletter --output-dir out  # custom directory

Writes:
    <output_dir>/newsletter.html    - email-ready HTML (html_formatter.py)
    <output_dir>/newsletter.md      - Markdown preview (deal_formatter.py)
    <output_dir>/instant_alerts.txt - one messenger push per line block
                                       (instant_alert_formatter.py)

CREDIT COST OF A REAL RUN: `run()` calls DealEngine.find_trip_deals(...)
once per entry in sampling_targets.FLIGHT_TARGETS - each one is a real,
potentially credit-costing SerpApi search (free if the provider's own
cache for that exact search is still valid, same TTL/mechanism as every
other real command in this project). This is NOT covered by the "0
API-Credits" rule that applied while building/testing this module - a real
newsletter needs a real, current price to compare against a baseline,
there is no way around that. Treat a real `run()` invocation exactly like
record_price_snapshot.py / daily_sampler.py: needs explicit, prior
human approval for the exact number of credits it may spend
(len(FLIGHT_TARGETS) at most).

The two audiences get DIFFERENT curation (engine/deal_filters.py):
newsletter is the broader weekly digest, instant alerts are the narrower,
urgency-only set (ERROR_FARE/FLIGHT_DROP/COMBINED_TRIP_DROP, higher score
bar) - a HOTEL_DROP is newsletter-worthy but not push-notification-worthy.

Testable core: `build_and_export(...)` takes an already-computed
list[Deal] and has no provider/network dependency at all - every test in
test_build_newsletter.py exercises it (or `_collect_real_deals` with a
DealEngine wired to fakes) directly, never through `run()`'s real wiring.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from trip_hunter.accommodation_price_history_repository import (
    DEFAULT_DB_PATH as ACCOMMODATION_DB_PATH,
)
from trip_hunter.accommodation_price_history_repository import AccommodationPriceHistoryRepository
from trip_hunter.alerts.deal_formatter import format_newsletter
from trip_hunter.alerts.html_formatter import format_newsletter_html
from trip_hunter.alerts.instant_alert_formatter import format_instant_alerts
from trip_hunter.caching import FileCache
from trip_hunter.config import (
    MissingConfigError,
    load_max_price_per_night,
    load_serpapi_config,
    load_weekend_max_total,
)
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.engine.deal_filters import DealFilterCriteria, filter_deals
from trip_hunter.models import Deal, DealType, FlightComparisonGroup
from trip_hunter.price_history_repository import DEFAULT_DB_PATH as FLIGHT_DB_PATH
from trip_hunter.price_history_repository import PriceHistoryRepository
from trip_hunter.providers.serpapi_accommodation_provider import SerpApiAccommodationProvider
from trip_hunter.providers.serpapi_client import SerpApiClient
from trip_hunter.providers.serpapi_flight_provider import SerpApiGoogleFlightsProvider
from trip_hunter.providers.serpapi_hotels_client import SerpApiHotelsClient
from trip_hunter.sampling_targets import FLIGHT_TARGETS

DEFAULT_OUTPUT_DIR = Path("output")

# Broader "weekly digest" audience - see module docstring. Deliberately
# still a flat ceiling (unlike DEFAULT_INSTANT_ALERT_CRITERIA below) - the
# newsletter is a generous, browse-everything digest, not a tight
# push-notification filter.
DEFAULT_NEWSLETTER_CRITERIA = DealFilterCriteria(max_total_price=400.0, min_score=50)
# Narrower, urgency-only audience for a push notification. Duration-aware
# budget, not one flat total: a weekend-shaped trip (<= weekend_max_nights)
# must stay under weekend_max_total EUR total; a longer trip is judged on
# the accommodation's EUR/night price instead (max_price_per_night) - a
# great week-long flight deal is never excluded just because nights x
# hotel alone would exceed a weekend-sized flat budget. Both configurable
# via TRIP_HUNTER_WEEKEND_MAX_TOTAL / TRIP_HUNTER_MAX_PRICE_PER_NIGHT (see
# config.py) - defaults 250 EUR / 60 EUR per night.
DEFAULT_INSTANT_ALERT_CRITERIA = DealFilterCriteria(
    weekend_max_total=load_weekend_max_total(),
    max_price_per_night=load_max_price_per_night(),
    # No score bar: the deal type IS the trigger. FLIGHT_DROP already means
    # >= 30% AND >= 25 EUR below the route baseline (flight_deal_detector),
    # yet such a drop scores only ~45-70 (a 35% drop scores 48) - the old
    # min_score=70 silently required drops of ~55% or more.
    min_score=None,
    allowed_deal_types=frozenset(
        {DealType.ERROR_FARE, DealType.FLIGHT_DROP, DealType.COMBINED_TRIP_DROP}
    ),
)
# Tier 3 ("Good Deal" - see engine/alert_tier.py) gets its own, deliberately
# lower score bar - a VIP-exclusive "steady content" tier isn't held to
# the same urgency threshold as a Tier 1/2 alert that also reaches the
# Free channel. Same bar as DEFAULT_NEWSLETTER_CRITERIA's (50), which
# already covers these deal types for the newsletter audience.
# daily_sampler.py's alert check tries DEFAULT_INSTANT_ALERT_CRITERIA
# first and only falls back to this one if nothing qualified there - the
# two allowed_deal_types sets are disjoint, so there's no ambiguity about
# which tier a qualifying deal belongs to.
DEFAULT_TIER_3_CRITERIA = DealFilterCriteria(
    weekend_max_total=load_weekend_max_total(),
    max_price_per_night=load_max_price_per_night(),
    min_score=50,
    allowed_deal_types=frozenset({DealType.HOTEL_DROP}),  # flights alert only as a real drop (>=30% and >=25 EUR)
)

_NO_INSTANT_ALERTS_MESSAGE = "Keine passenden Instant-Alert-Deals in diesem Lauf gefunden.\n"


@dataclass(frozen=True)
class BuildResult:
    newsletter_html_path: Path
    newsletter_md_path: Path
    instant_alerts_path: Path
    newsletter_deal_count: int
    instant_alert_count: int


def build_and_export(
    deals: list[Deal],
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    newsletter_criteria: DealFilterCriteria = DEFAULT_NEWSLETTER_CRITERIA,
    instant_alert_criteria: DealFilterCriteria = DEFAULT_INSTANT_ALERT_CRITERIA,
    newsletter_title: str = "Trip Hunter — Wochenend-Radar",
) -> BuildResult:
    """Curate `deals` for both audiences and write all three exports.
    Pure function over an already-computed deal list - no provider, no
    network, no database dependency. Creates `output_dir` if missing.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    newsletter_deals = filter_deals(deals, newsletter_criteria)
    instant_deals = filter_deals(deals, instant_alert_criteria)

    html_path = output_dir / "newsletter.html"
    md_path = output_dir / "newsletter.md"
    instant_path = output_dir / "instant_alerts.txt"

    html_path.write_text(
        format_newsletter_html(newsletter_deals, title=newsletter_title), encoding="utf-8"
    )
    md_path.write_text(format_newsletter(newsletter_deals, title=newsletter_title), encoding="utf-8")

    instant_messages = format_instant_alerts(instant_deals)
    instant_content = (
        "\n\n---\n\n".join(instant_messages) + "\n" if instant_messages else _NO_INSTANT_ALERTS_MESSAGE
    )
    instant_path.write_text(instant_content, encoding="utf-8")

    return BuildResult(
        newsletter_html_path=html_path,
        newsletter_md_path=md_path,
        instant_alerts_path=instant_path,
        newsletter_deal_count=len(newsletter_deals),
        instant_alert_count=len(instant_deals),
    )


def _collect_real_deals(engine: DealEngine, flight_targets: list[FlightComparisonGroup]) -> list[Deal]:
    """Runs DealEngine.find_trip_deals(...) once per target - no route
    loops, no date loops beyond the fixed target list. Provider-agnostic:
    tests pass a DealEngine wired to fakes, `run()` passes one wired to
    real SerpApi providers.
    """
    deals: list[Deal] = []
    for group in flight_targets:
        deals.extend(
            engine.find_trip_deals(
                origin=group.origin,
                destination=group.destination,
                earliest_departure=group.departure_date,
                latest_departure=group.departure_date,
                return_date=group.return_date,
            )
        )
    return deals


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trip_hunter.build_newsletter",
        description="Build the newsletter.html / newsletter.md / instant_alerts.txt exports.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory to write the exports into (default: {DEFAULT_OUTPUT_DIR})",
    )
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def run(argv: list[str] | None = None) -> BuildResult | None:
    """CLI entry point: parses arguments, loads config, wires up the real
    SerpApi providers and the REAL runtime databases (so DealEngine can use
    our own historical baselines, not just provider fallbacks), collects
    deals across sampling_targets.FLIGHT_TARGETS, then delegates to
    `build_and_export`. See module docstring for the real credit cost.
    """
    args = _parse_args(argv)

    print("TRIP HUNTER — NEWSLETTER BUILDER")
    print()

    try:
        config = load_serpapi_config()
    except MissingConfigError as exc:
        print(f"Configuration missing: {exc}")
        return None

    cache = FileCache(ttl_seconds=config.cache_ttl_seconds)
    flight_repository = PriceHistoryRepository(db_path=FLIGHT_DB_PATH)
    accommodation_repository = AccommodationPriceHistoryRepository(db_path=ACCOMMODATION_DB_PATH)

    flight_client = SerpApiClient(api_key=config.api_key, timeout_seconds=config.request_timeout_seconds)
    flight_provider = SerpApiGoogleFlightsProvider(client=flight_client, cache=cache, currency=config.currency)

    hotels_client = SerpApiHotelsClient(api_key=config.api_key, timeout_seconds=config.request_timeout_seconds)
    accommodation_provider = SerpApiAccommodationProvider(
        client=hotels_client, cache=cache, currency=config.currency
    )

    engine = DealEngine(
        flight_provider=flight_provider,
        accommodation_provider=accommodation_provider,
        price_history_repository=flight_repository,
        accommodation_price_history_repository=accommodation_repository,
    )

    print(f"Durchsuchte Routen: {len(FLIGHT_TARGETS)}")
    deals = _collect_real_deals(engine, FLIGHT_TARGETS)
    print(f"Rohdeals gefunden: {len(deals)}")
    print()

    result = build_and_export(deals, output_dir=args.output_dir)

    print(f"Newsletter (HTML):     {result.newsletter_html_path}  ({result.newsletter_deal_count} Deal(s))")
    print(f"Newsletter (Markdown): {result.newsletter_md_path}")
    print(f"Instant Alerts:        {result.instant_alerts_path}  ({result.instant_alert_count} Deal(s))")

    return result


if __name__ == "__main__":
    run()
