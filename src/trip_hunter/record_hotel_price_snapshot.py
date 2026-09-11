"""Manual CLI: record exactly ONE controlled historical accommodation price
observation from a single real search snapshot - the accommodation-side
counterpart to record_price_snapshot.py. Mirrors that command's structure
and every one of its safety rules; see its module docstring for the full
rationale, repeated here only where the accommodation domain actually
differs.

Run with:
    python -m trip_hunter.record_hotel_price_snapshot \\
        --destination PMI \\
        --check-in 2026-10-02 --check-out 2026-10-07 \\
        --currency EUR

Requires TRIP_HUNTER_SERPAPI_KEY to be set (see .env.example) - this
command makes a real SerpApi request (engine=google_hotels) unless the
exact same search is already cached (see "Source: LIVE RESPONSE /
CACHE HIT" below).

What this command does, in order:
1. Builds an EXPLICIT AccommodationComparisonGroup from exactly the CLI
   arguments given - never inferred or guessed. See
   AccommodationComparisonGroup in models.py.
2. Runs ONE logical search via SerpApiAccommodationProvider - no
   destination loops, no date loops, no retries. The existing
   per-search-key cache still applies (labeled "CACHE HIT" below instead
   of "LIVE RESPONSE" - see the note on how that's detected, identical
   mechanism to record_price_snapshot.py).
3. Reduces the result to at most one AccommodationObservation via
   observation_from_accommodation_search_results(...) - NEVER by looping
   observation_from_accommodation_offer(...) over every offer. See
   "Observation Semantics" in docs/PRODUCT_SPEC.md (the flight-side
   rationale this mirrors exactly).
4. Stores it in the REAL runtime database (DEFAULT_DB_PATH,
   data/trip_hunter.db, same file the flight side uses - a different
   table, see accommodation_price_history_repository.py) - never the
   isolated demo database. CACHE HIT RULE (same one that fixed a real
   sampling bug on the flight side): an AccommodationObservation is only
   ever stored when source_label == "LIVE RESPONSE". A cache hit is NOT a
   new market measurement; repository.add_observation(...) is never
   called for it, no matter what observed_at would have been computed.
   The search result and cheapest offer are still printed for a cache
   hit; only persistence is skipped. See "Cache Hit Rule" in
   docs/PRODUCT_SPEC.md.
5. Reports the resulting observation count for this exact comparison
   group and whether get_accommodation_historical_baseline(...) -
   unmodified, existing logic, MIN_HISTORY_OBSERVATIONS=5 - considers it
   available yet.

Deliberately different from record_price_snapshot.py: AccommodationProvider
has no "get insight" equivalent method (see
serpapi_accommodation_provider.py's module docstring - Google Hotels has no
documented price-insight concept), so there is no "PROVIDER PRICE INSIGHT"
section here at all - not omitted by oversight, there is simply nothing to
show.

IMPORTANT - this command is MANUAL, not a sampling strategy. Running it
several times in a row within a few minutes does NOT produce a meaningful
historical baseline - see the identical warning in
record_price_snapshot.py's module docstring, which applies here unchanged.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from trip_hunter.accommodation_price_history_repository import (
    DEFAULT_DB_PATH,
    AccommodationPriceHistoryRepository,
    observation_from_accommodation_search_results,
)
from trip_hunter.caching import FileCache, hotel_search_cache_key
from trip_hunter.config import MissingConfigError, load_serpapi_config
from trip_hunter.engine.price_statistics import (
    MIN_HISTORY_OBSERVATIONS,
    get_accommodation_historical_baseline,
)
from trip_hunter.models import AccommodationComparisonGroup
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.errors import FlightProviderError
from trip_hunter.providers.serpapi_accommodation_provider import SerpApiAccommodationProvider
from trip_hunter.providers.serpapi_hotels_client import SerpApiHotelsClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trip_hunter.record_hotel_price_snapshot",
        description=(
            "Record exactly ONE controlled historical accommodation price "
            "observation from a single real search snapshot. See "
            "'Controlled Historical Sampling' in docs/PRODUCT_SPEC.md."
        ),
    )
    parser.add_argument("--destination", required=True, help="Destination code, e.g. PMI")
    parser.add_argument("--check-in", required=True, help="Check-in date, YYYY-MM-DD")
    parser.add_argument("--check-out", required=True, help="Check-out date, YYYY-MM-DD")
    parser.add_argument("--currency", default="EUR", help="Currency code (default: EUR)")
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def run(argv: list[str] | None = None) -> None:
    """CLI entry point: parses arguments, loads config, wires up the real
    SerpApi accommodation provider and the REAL runtime repository, then
    delegates to `_record_snapshot`. Kept thin and deliberately hard to
    unit test in isolation - all the actual snapshot logic below is
    provider-agnostic and covered directly by tests via a fake
    AccommodationProvider, so tests never need a real HTTP call.
    """
    args = _parse_args(argv)

    destination = args.destination.upper()
    check_in = date.fromisoformat(args.check_in)
    check_out = date.fromisoformat(args.check_out)
    currency = args.currency.upper()

    # The EXPLICIT comparison group, from exactly these CLI arguments - no
    # heuristic, nothing inferred. See AccommodationComparisonGroup in
    # models.py.
    comparison_group = AccommodationComparisonGroup(
        destination=destination,
        check_in=check_in,
        check_out=check_out,
        currency=currency,
    )

    try:
        config = load_serpapi_config()
    except MissingConfigError as exc:
        print(f"Configuration missing: {exc}")
        return

    client = SerpApiHotelsClient(api_key=config.api_key, timeout_seconds=config.request_timeout_seconds)
    cache = FileCache(ttl_seconds=config.cache_ttl_seconds)
    provider = SerpApiAccommodationProvider(client=client, cache=cache, currency=currency)

    # Detect cache hit vs. live request BEFORE calling search_accommodations(),
    # using the same public cache-key helper the provider uses internally -
    # identical mechanism to record_price_snapshot.py, see its comment for
    # the documented limitation (duplicated cache-key computation).
    cache_key = hotel_search_cache_key(
        destination, check_in.isoformat(), check_out.isoformat(), currency
    )
    was_cached_before_search = cache.get(cache_key) is not None
    source_label = "CACHE HIT" if was_cached_before_search else "LIVE RESPONSE"

    # ALWAYS the real runtime database - never the isolated demo database.
    # This command must never be able to touch a demo DB path.
    repository = AccommodationPriceHistoryRepository(db_path=DEFAULT_DB_PATH)

    _record_snapshot(
        provider=provider,
        repository=repository,
        comparison_group=comparison_group,
        source_label=source_label,
    )


def _record_snapshot(
    provider: AccommodationProvider,
    repository: AccommodationPriceHistoryRepository,
    comparison_group: AccommodationComparisonGroup,
    source_label: str,
    observed_at: datetime | None = None,
) -> None:
    """The actual snapshot logic, independent of argparse/config/which
    concrete provider or database is used - exercised directly by tests
    with a fake AccommodationProvider and a temp-file repository, so no
    test ever makes a real HTTP call. `run()` above is the only place that
    wires up the real SerpApi provider and the real runtime database.
    """
    destination = comparison_group.destination
    check_in = comparison_group.check_in
    check_out = comparison_group.check_out
    currency = comparison_group.currency

    print("TRIP HUNTER — HOTEL PRICE SNAPSHOT")
    print()
    print("Comparison Group:")
    print(destination)
    print(f"{check_in} → {check_out}")
    print(currency)
    print("Mode: cheapest_any")
    print()

    try:
        # Exactly ONE logical search: one destination, one exact date pair,
        # no loops, no retries. The client itself never retries on failure.
        offers = provider.search_accommodations(destination, check_in, check_out)
    except FlightProviderError as exc:
        print(f"Accommodation search failed: {exc}")
        return

    print(f"Source: {source_label}")
    print(f"Offers found: {len(offers)}")
    print()

    observed_at = observed_at or datetime.now(timezone.utc)
    # The ONLY approved way to turn a search result into history: reduces
    # the whole snapshot to at most one observation for comparison_group.
    # Never observation_from_accommodation_offer(...) looped over every offer.
    observation = observation_from_accommodation_search_results(
        offers, comparison_group, observed_at=observed_at
    )

    if observation is None:
        print("Cheapest valid comparable offer: none found")
        print()
        print("Observation:")
        print("Stored: NO")
        print(
            "Reason: no offer matched the comparison group exactly "
            "(destination/dates/currency must match)."
        )
        return

    print("Cheapest valid comparable offer:")
    print(f"{observation.price:.2f} {observation.currency}")
    if observation.name:
        print(observation.name)
    print()

    print("Observation:")
    if source_label == "CACHE HIT":
        # A cache hit is NOT a new market measurement - see "Cache Hit
        # Rule" in docs/PRODUCT_SPEC.md. Never call add_observation() here.
        print("Stored: NO")
        print("Reason: cache hit — no new market measurement.")
    else:
        was_new = repository.add_observation(observation)
        if was_new:
            print("Stored: YES")
        else:
            print("Stored: NO")
            print(
                "Reason: duplicate observation (same comparison group, "
                "provider, and price already recorded today)."
            )
        print(f"Observed at: {observation.observed_at.isoformat()}")
        print(f"Provider: {observation.provider}")
    print()

    all_observations = repository.get_observations(destination, check_in, check_out, currency)
    observation_count = len(all_observations)

    print("Historical observations:")
    print(f"{observation_count} / {MIN_HISTORY_OBSERVATIONS} required")
    print()

    baseline = get_accommodation_historical_baseline(
        repository,
        destination,
        check_in,
        check_out,
        currency,
        current_price=observation.price,
    )

    print("Historical baseline:")
    if baseline is None:
        print("NOT AVAILABLE YET")
        missing = max(0, MIN_HISTORY_OBSERVATIONS - observation_count)
        print(f"Need {missing} more observation(s).")
    else:
        stats = baseline.statistics
        print("AVAILABLE")
        print()
        print(f"Median: {stats.median:.2f} {currency}")
        print(f"P25: {stats.p25:.2f} {currency}")
        print(f"P75: {stats.p75:.2f} {currency}")
        print(f"Current difference from median: {baseline.percent_diff_from_median:.0f} %")

    # No "PROVIDER PRICE INSIGHT" section: AccommodationProvider has no
    # "get insight" equivalent method - see module docstring.


if __name__ == "__main__":
    run()
