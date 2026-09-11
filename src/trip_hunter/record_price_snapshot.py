"""Manual CLI: record exactly ONE controlled historical price observation
from a single real search snapshot - the human-triggered counterpart to
the automatic-collection groundwork laid in MVP 0.3/0.3.1/0.3.2/0.4.

Run with:
    python -m trip_hunter.record_price_snapshot \\
        --origin HAM --destination PMI \\
        --departure 2026-10-02 --return 2026-10-07 \\
        --currency EUR

Requires TRIP_HUNTER_SERPAPI_KEY to be set (see .env.example) - this
command makes a real SerpApi request unless the exact same search is
already cached (see "Source: LIVE RESPONSE / CACHE HIT" below).

What this command does, in order:
1. Builds an EXPLICIT FlightComparisonGroup from exactly the CLI
   arguments given - never an inferred or guessed group. This is the same
   pattern historical_price_demo.py uses (see "Explicit Comparison
   Groups" in docs/PRODUCT_SPEC.md).
2. Runs ONE logical search via SerpApiGoogleFlightsProvider - no route
   loops, no date loops, no retries that could silently spend more
   credits. The existing per-search-key cache still applies normally, so
   re-running this for a search made within the cache TTL costs no
   additional credit (labeled "CACHE HIT" below instead of
   "LIVE RESPONSE" - see the note on how that's detected).
3. Reduces the result to at most one PriceObservation via
   observation_from_search_results(...) - NEVER by looping
   observation_from_flight_offer(...) over every offer (see "Observation
   Semantics" in docs/PRODUCT_SPEC.md for why that would bias the
   baseline).
4. Stores it in the REAL runtime database (DEFAULT_DB_PATH,
   data/trip_hunter.db) - never the isolated demo database from
   MVP 0.4.1 (data/demo_trip_hunter.db). This command must never touch
   the demo DB, full stop. CACHE HIT RULE (added after a real sampling
   bug found in production use): a PriceObservation is only ever stored
   when source_label == "LIVE RESPONSE". A cache hit re-reads a response
   fetched at an earlier point in time - it is NOT a new market
   measurement, so repository.add_observation(...) is never called for
   it, no matter what observed_at would have been computed. The search
   result, cheapest offer, and provider price insight are still printed
   for a cache hit; only persistence is skipped. See "Cache Hit Rule" in
   docs/PRODUCT_SPEC.md.
5. Reports the resulting observation count for this exact comparison
   group and whether get_historical_baseline(...) - unmodified, existing
   logic, MIN_HISTORY_OBSERVATIONS=5 - considers it available yet.

Scope: round-trip searches only (matches the CLI's required --departure/
--return pair). One-way support would need its own CLI shape and isn't
part of MVP 0.4.2.

IMPORTANT - this command is MANUAL, not a sampling strategy. Running it
five times in a row within a few minutes does NOT produce a meaningful
historical baseline - it mostly produces duplicate-of-the-day rows (see
deduplication below) and, in the rare case prices genuinely changed within
minutes, a false sense of "5 observations" that doesn't reflect real
market movement over time. Meaningful history comes from separate search
snapshots spread across separate points in time (e.g. once a day, on
different days) - the same principle historical_price_demo.py's fixture
data already illustrates. MVP 0.4.2 does not enforce or automate any
particular cadence; a scheduler is explicitly out of scope.

FUTURE MULTI-PROVIDER NOTE (architecture hint only, not implemented here):
once a single planned measurement point can query several real providers
(e.g. SerpApi + Aviasales + Skyscanner), those must NOT automatically be
recorded as that many independent time observations - a "measurement
snapshot" spanning several providers at one point in time should likely
still reduce to the single cheapest comparable price across providers for
that moment, the same way one provider's multiple offers already do. Not
built now; documented for when a second real provider exists.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from trip_hunter.caching import FileCache, flight_search_cache_key
from trip_hunter.config import MissingConfigError, load_serpapi_config
from trip_hunter.engine.price_statistics import MIN_HISTORY_OBSERVATIONS, get_historical_baseline
from trip_hunter.models import FlightComparisonGroup, TripType
from trip_hunter.price_history_repository import (
    DEFAULT_DB_PATH,
    PriceHistoryRepository,
    observation_from_search_results,
)
from trip_hunter.providers.errors import FlightProviderError
from trip_hunter.providers.flight_provider import FlightProvider
from trip_hunter.providers.serpapi_client import SerpApiClient
from trip_hunter.providers.serpapi_flight_provider import SerpApiGoogleFlightsProvider


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trip_hunter.record_price_snapshot",
        description=(
            "Record exactly ONE controlled historical price observation from a "
            "single real search snapshot. See 'Controlled Historical Sampling' "
            "in docs/PRODUCT_SPEC.md."
        ),
    )
    parser.add_argument("--origin", required=True, help="Origin airport code, e.g. HAM")
    parser.add_argument("--destination", required=True, help="Destination airport code, e.g. PMI")
    parser.add_argument("--departure", required=True, help="Departure date, YYYY-MM-DD")
    parser.add_argument(
        "--return", dest="return_date", required=True, help="Return date, YYYY-MM-DD"
    )
    parser.add_argument("--currency", default="EUR", help="Currency code (default: EUR)")
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def run(argv: list[str] | None = None) -> None:
    """CLI entry point: parses arguments, loads config, wires up the real
    SerpApi provider and the REAL runtime repository, then delegates to
    `_record_snapshot`. Kept thin and deliberately hard to unit test in
    isolation - all the actual snapshot logic below is provider-agnostic
    and covered directly by tests via a fake FlightProvider, so tests never
    need a real HTTP call.
    """
    args = _parse_args(argv)

    origin = args.origin.upper()
    destination = args.destination.upper()
    departure_date = date.fromisoformat(args.departure)
    return_date = date.fromisoformat(args.return_date)
    currency = args.currency.upper()

    # The EXPLICIT comparison group, from exactly these CLI arguments - no
    # heuristic, nothing inferred. See "Explicit Comparison Groups" in
    # docs/PRODUCT_SPEC.md.
    comparison_group = FlightComparisonGroup(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        trip_type=TripType.ROUND_TRIP,
        currency=currency,
    )

    try:
        config = load_serpapi_config()
    except MissingConfigError as exc:
        print(f"Configuration missing: {exc}")
        return

    client = SerpApiClient(api_key=config.api_key, timeout_seconds=config.request_timeout_seconds)
    cache = FileCache(ttl_seconds=config.cache_ttl_seconds)
    provider = SerpApiGoogleFlightsProvider(client=client, cache=cache, currency=currency)

    # Detect cache hit vs. live request BEFORE calling search_flights(),
    # using the same public cache-key helper the provider uses internally.
    # This is a non-destructive read (FileCache.get never triggers a
    # request itself), so checking it doesn't cost anything and needs no
    # changes to the provider. If this ever stops lining up with the
    # provider's internal cache key, this label may become inaccurate -
    # not hacked further than this for MVP 0.4.2, see docs/ARCHITECTURE.md.
    cache_key = flight_search_cache_key(
        origin, destination, departure_date.isoformat(), return_date.isoformat(), currency
    )
    was_cached_before_search = cache.get(cache_key) is not None
    source_label = "CACHE HIT" if was_cached_before_search else "LIVE RESPONSE"

    # ALWAYS the real runtime database - never the isolated demo database
    # from MVP 0.4.1. This command must never be able to touch
    # data/demo_trip_hunter.db.
    repository = PriceHistoryRepository(db_path=DEFAULT_DB_PATH)

    _record_snapshot(
        provider=provider,
        repository=repository,
        comparison_group=comparison_group,
        source_label=source_label,
        currency=currency,
    )


def _record_snapshot(
    provider: FlightProvider,
    repository: PriceHistoryRepository,
    comparison_group: FlightComparisonGroup,
    source_label: str,
    currency: str,
    observed_at: datetime | None = None,
) -> None:
    """The actual snapshot logic, independent of argparse/config/which
    concrete provider or database is used - exercised directly by tests
    with a fake FlightProvider and a temp-file repository, so no test ever
    makes a real HTTP call. `run()` above is the only place that wires up
    the real SerpApi provider and the real runtime database.
    """
    origin = comparison_group.origin
    destination = comparison_group.destination
    departure_date = comparison_group.departure_date
    return_date = comparison_group.return_date

    print("TRIP HUNTER — PRICE SNAPSHOT")
    print()
    print("Comparison Group:")
    print(f"{origin} → {destination}")
    print(f"{departure_date} → {return_date}")
    print("Roundtrip")
    print(currency)
    print("Mode: cheapest_any")
    print()

    try:
        # Exactly ONE logical search: one route, one exact date pair, no
        # loops, no retries. The client itself never retries on failure.
        offers = provider.search_flights(
            origin, destination, departure_date, departure_date, return_date=return_date
        )
    except FlightProviderError as exc:
        print(f"Flight search failed: {exc}")
        return

    print(f"Source: {source_label}")
    print(f"Offers found: {len(offers)}")
    print()

    observed_at = observed_at or datetime.now(timezone.utc)
    # The ONLY approved way to turn a search result into history: reduces
    # the whole snapshot to at most one observation for comparison_group.
    # Never observation_from_flight_offer(...) looped over every offer.
    observation = observation_from_search_results(offers, comparison_group, observed_at=observed_at)

    if observation is None:
        print("Cheapest valid comparable offer: none found")
        print()
        print("Observation:")
        print("Stored: NO")
        print(
            "Reason: no offer matched the comparison group with a "
            "confirmed complete price (route/dates/currency must match "
            "exactly, and price_confirmed_complete must be True)."
        )
        return

    stops_label = "Direct" if observation.stops == 0 else f"{observation.stops} stop(s)"
    print("Cheapest valid comparable offer:")
    print(f"{observation.price:.2f} {observation.currency}")
    print(observation.airline)
    print(stops_label)
    print()

    print("Observation:")
    if source_label == "CACHE HIT":
        # A cache hit is NOT a new market measurement - it's a re-read of
        # a response fetched at an earlier, unrelated point in time. Never
        # call add_observation() here: doing so would persist a
        # PriceObservation stamped with today's observed_at even though
        # the underlying price data is stale, silently inflating the
        # historical observation count without a genuinely new snapshot.
        # Only a LIVE RESPONSE may create a Historical Measurement Snapshot.
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

    all_observations = repository.get_observations(
        origin, destination, departure_date, return_date, TripType.ROUND_TRIP, currency
    )
    observation_count = len(all_observations)

    print("Historical observations:")
    print(f"{observation_count} / {MIN_HISTORY_OBSERVATIONS} required")
    print()

    baseline = get_historical_baseline(
        repository,
        origin,
        destination,
        departure_date,
        return_date,
        TripType.ROUND_TRIP,
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

    # Context only - never stored as our own history. See "Provider Price
    # Insight" vs "Our Historical Data" in docs/PRODUCT_SPEC.md.
    insight = provider.get_price_insight(origin, destination, departure_date, return_date)
    if insight is not None:
        print()
        print("PROVIDER PRICE INSIGHT (context only - not stored as our data)")
        print(f"Provider lowest price: {insight.provider_lowest_price}")
        if insight.typical_price_low is not None and insight.typical_price_high is not None:
            print(f"Typical range: {insight.typical_price_low:.2f}–{insight.typical_price_high:.2f} {currency}")
        print(f"Price level: {insight.price_level}")


if __name__ == "__main__":
    run()
