"""Runnable demo: real flight data via AmadeusFlightProvider, run through
our existing flight deal detection (no hotel data yet - see MVP 0.2 scope
in docs/PRODUCT_SPEC.md).

Run with:
    python -m vacation_hunter.real_flight_demo
    python -m vacation_hunter.real_flight_demo HAM PMI 2026-10-02 2026-10-07

Defaults to HAM -> PMI, 2026-10-02 - 2026-10-07 if no arguments are given.

Requires VACATION_HUNTER_FLIGHT_API_KEY and VACATION_HUNTER_FLIGHT_API_SECRET
to be set - e.g. via a .env file, see .env.example and README.md.
"""

from __future__ import annotations

import sys
from datetime import date

from vacation_hunter.caching import FileCache
from vacation_hunter.config import MissingConfigError, load_flight_api_config
from vacation_hunter.engine.deal_engine import DealEngine
from vacation_hunter.models import Deal, DealType
from vacation_hunter.providers.amadeus_client import AmadeusClient
from vacation_hunter.providers.amadeus_flight_provider import AmadeusFlightProvider
from vacation_hunter.providers.errors import FlightProviderError
from vacation_hunter.providers.null_accommodation_provider import NullAccommodationProvider

_DEFAULT_ORIGIN = "HAM"
_DEFAULT_DESTINATION = "PMI"
_DEFAULT_DEPARTURE = date(2026, 10, 2)
_DEFAULT_RETURN = date(2026, 10, 7)

_USAGE = (
    "Usage: python -m vacation_hunter.real_flight_demo ORIGIN DEST DEPARTURE RETURN\n"
    "Example: python -m vacation_hunter.real_flight_demo HAM PMI 2026-10-02 2026-10-07"
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


def print_offer(deal: Deal) -> None:
    flight = deal.flight
    stops_label = "Direct" if flight.stops == 0 else f"{flight.stops} stop(s)"
    print(f"{flight.origin} → {flight.destination}")
    print(f"{flight.departure_date:%d.%m.%Y} – {flight.return_date:%d.%m.%Y}")
    print(f"{flight.price:.2f} {flight.currency}")
    print(stops_label)
    print(f"Airline: {flight.airline}")
    print(f"Provider: {flight.provider}")
    if deal.deal_type is DealType.BASELINE_UNAVAILABLE:
        print("Deal assessment: no baseline price known yet (BASELINE_UNAVAILABLE)")
    else:
        print(f"Deal assessment: {deal.deal_type.value}")


def run(argv: list[str] | None = None) -> list[Deal]:
    origin, destination, departure_date, return_date = _parse_args(
        argv if argv is not None else sys.argv[1:]
    )

    try:
        config = load_flight_api_config()
    except MissingConfigError as exc:
        print(f"Configuration missing: {exc}")
        return []

    client = AmadeusClient(
        api_key=config.api_key,
        api_secret=config.api_secret,
        base_url=config.base_url,
        timeout_seconds=config.request_timeout_seconds,
    )
    cache = FileCache(ttl_seconds=config.cache_ttl_seconds)
    flight_provider = AmadeusFlightProvider(client=client, cache=cache)

    engine = DealEngine(
        flight_provider=flight_provider,
        accommodation_provider=NullAccommodationProvider(),
    )

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
        print("Found 0 flight offers.")
        return []

    print(f"Found {len(deals)} flight offer(s)")
    print()
    print("Cheapest:")
    print()
    cheapest = min(deals, key=lambda deal: deal.flight.price)
    print_offer(cheapest)

    return deals


if __name__ == "__main__":
    run()
