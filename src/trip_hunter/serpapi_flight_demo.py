"""Runnable demo: Google Flights search via SerpApi, run through our
existing flight deal detection, including provider-supplied Price Insights
where available (no hotel data yet - see MVP scope in docs/PRODUCT_SPEC.md).

Run with:
    python -m trip_hunter.serpapi_flight_demo
    python -m trip_hunter.serpapi_flight_demo HAM PMI 2026-10-02 2026-10-07

Requires TRIP_HUNTER_SERPAPI_KEY to be set - e.g. via a .env file, see
.env.example and README.md.

Credit safety: this makes at most ONE live SerpApi search per run (zero if
the result is already cached) - see "API Credit Safety" in
docs/ARCHITECTURE.md. Never loop this over multiple routes or dates.
"""

from __future__ import annotations

import sys
from datetime import date

from trip_hunter.caching import FileCache
from trip_hunter.config import MissingConfigError, load_serpapi_config
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.models import Deal, DealType
from trip_hunter.providers.errors import FlightProviderError
from trip_hunter.providers.null_accommodation_provider import NullAccommodationProvider
from trip_hunter.providers.serpapi_client import SerpApiClient
from trip_hunter.providers.serpapi_flight_provider import SerpApiGoogleFlightsProvider

_DEFAULT_ORIGIN = "HAM"
_DEFAULT_DESTINATION = "PMI"
_DEFAULT_DEPARTURE = date(2026, 10, 2)
_DEFAULT_RETURN = date(2026, 10, 7)

_USAGE = (
    "Usage: python -m trip_hunter.serpapi_flight_demo ORIGIN DEST DEPARTURE RETURN\n"
    "Example: python -m trip_hunter.serpapi_flight_demo HAM PMI 2026-10-02 2026-10-07"
)


def _parse_args(argv: list[str]) -> tuple[str, str, date, date]:
    if len(argv) == 0:
        return _DEFAULT_ORIGIN, _DEFAULT_DESTINATION, _DEFAULT_DEPARTURE, _DEFAULT_RETURN
    if len(argv) != 4:
        raise SystemExit(_USAGE)

    origin, destination, departure_str, return_str = argv
    try:
        departure_date = date.fromisoformat(departure_str)
        return_date = date.fromisoformat(return_str)
    except ValueError as exc:
        raise SystemExit(f"Invalid date: {exc}\n\n{_USAGE}") from exc

    return origin.upper(), destination.upper(), departure_date, return_date


def _format_money(value: float | None, currency: str) -> str:
    return f"{value:.2f} {currency}" if value is not None else "unknown"


def print_result(deal: Deal) -> None:
    flight = deal.flight
    stops_label = "Direct" if flight.stops == 0 else f"{flight.stops} stop(s)"
    times_label = f"{flight.departure_time or '?'} – {flight.return_time or '?'}"

    print("CHEAPEST FLIGHT")
    print()
    print(f"Airline: {flight.airline}")
    print(f"Price: {_format_money(flight.price, flight.currency)}")
    print(f"Stops: {stops_label}")
    print(f"Times: {times_label}")
    print()

    if deal.deal_type is DealType.PRICE_INCOMPLETE:
        # Generic safety fallback - not expected for a freshly normalized
        # SerpApi offer (verified complete, see docs/PRODUCT_SPEC.md), but
        # can still happen for a stale pre-MVP-0.2.2 cache entry or a future
        # provider/response shape whose price completeness isn't confirmed.
        print("PRICE INSIGHT")
        print()
        print("Not evaluated: this price is not confirmed complete, so we")
        print("won't compare it to any baseline. See 'Price Completeness'")
        print("in docs/PRODUCT_SPEC.md.")
        print()
        print("TRIP HUNTER ASSESSMENT")
        print()
        print(f"Deal type: {deal.deal_type.value}")
        print("Savings: unavailable")
        print("Savings percentage: unavailable")
        return

    print("PRICE INSIGHT")
    print()
    insight = deal.price_insight
    if insight is None:
        print("Baseline:")
        print("Unavailable")
    else:
        print(f"Provider lowest price: {_format_money(insight.provider_lowest_price, flight.currency)}")
        if insight.typical_price_low is not None and insight.typical_price_high is not None:
            print(
                f"Typical range: {insight.typical_price_low:.2f} – "
                f"{insight.typical_price_high:.2f} {flight.currency}"
            )
        else:
            print("Typical range: unavailable")
        print(f"Price level: {insight.price_level or 'unavailable'}")
        print(f"Baseline source: {deal.baseline_source.value}")
    print()

    print("TRIP HUNTER ASSESSMENT")
    print()
    print(f"Deal type: {deal.deal_type.value}")
    if deal.savings_absolute is not None and deal.savings_percentage is not None:
        print(f"Savings: {_format_money(deal.savings_absolute, flight.currency)}")
        print(f"Savings percentage: {deal.savings_percentage * 100:.0f} %")
    else:
        print("Savings: unavailable")
        print("Savings percentage: unavailable")


def run(argv: list[str] | None = None) -> list[Deal]:
    origin, destination, departure_date, return_date = _parse_args(
        argv if argv is not None else sys.argv[1:]
    )

    try:
        config = load_serpapi_config()
    except MissingConfigError as exc:
        print(f"Configuration missing: {exc}")
        return []

    client = SerpApiClient(api_key=config.api_key, timeout_seconds=config.request_timeout_seconds)
    cache = FileCache(ttl_seconds=config.cache_ttl_seconds)
    flight_provider = SerpApiGoogleFlightsProvider(
        client=client, cache=cache, currency=config.currency
    )

    engine = DealEngine(
        flight_provider=flight_provider,
        accommodation_provider=NullAccommodationProvider(),
    )

    print("GOOGLE FLIGHTS SEARCH")
    print()
    print(f"{origin} → {destination}")
    print(f"{departure_date:%d.%m.%Y} – {return_date:%d.%m.%Y}")
    print()

    try:
        deals = engine.find_trip_deals(
            origin=origin,
            destination=destination,
            earliest_departure=departure_date,
            latest_departure=departure_date,
            return_date=return_date,
        )
    except FlightProviderError as exc:
        print(f"Flight search failed: {exc}")
        return []

    if not deals:
        print("Found: 0 offers")
        return []

    print(f"Found: {len(deals)} offers")
    print()

    cheapest = min(deals, key=lambda deal: deal.flight.price)
    print_result(cheapest)

    return deals


if __name__ == "__main__":
    run()
