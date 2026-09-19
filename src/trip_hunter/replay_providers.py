"""Zero-network FlightProvider/AccommodationProvider stand-ins that replay
an already-stored observation instead of calling a real provider.

Used wherever a Deal needs to be (re-)evaluated from data already in
data/trip_hunter.db without spending an additional API credit -
daily_sampler.py's automatic post-snapshot alert check and
dispatch/test_dispatch.py's --real mode both need exactly this, so it
lives here once instead of twice.

get_typical_price / get_typical_total_price both mirror the real SerpApi
providers (always None - no provider-side baseline for either domain), so
DealEngine falls back to the real repository-based baseline exactly like
production does - see "Baseline Problem" in docs/PRODUCT_SPEC.md.
"""

from __future__ import annotations

from datetime import date

from trip_hunter.models import (
    AccommodationObservation,
    AccommodationOffer,
    FlightOffer,
    PriceObservation,
)
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.flight_provider import FlightProvider


class ReplayFlightProvider(FlightProvider):
    """Replays exactly ONE already-stored PriceObservation as today's
    (only) FlightOffer - never a live or cached search."""

    def __init__(self, observation: PriceObservation) -> None:
        self._observation = observation

    def search_flights(
        self, origin: str, destination: str, earliest_departure: date, latest_departure: date,
        return_date: date | None = None,
    ) -> list[FlightOffer]:
        o = self._observation
        return [
            FlightOffer(
                origin=o.origin, destination=o.destination,
                departure_date=o.departure_date, return_date=o.return_date,
                price=o.price, currency=o.currency,
                airline=o.airline or "Unknown", stops=o.stops,
                provider=o.provider, price_confirmed_complete=True,
            )
        ]

    def get_typical_price(self, origin: str, destination: str, month: int) -> float | None:
        return None

    def get_price_insight(self, origin, destination, departure_date, return_date):
        return None


class ReplayAccommodationProvider(AccommodationProvider):
    """Replays exactly ONE already-stored AccommodationObservation as
    today's (only) AccommodationOffer - never a live or cached search.
    `observation=None` means "nothing stored yet" - returns no offers,
    same as a genuinely empty real search result.
    """

    def __init__(self, observation: AccommodationObservation | None) -> None:
        self._observation = observation

    def search_accommodations(
        self, destination: str, check_in: date, check_out: date
    ) -> list[AccommodationOffer]:
        if self._observation is None:
            return []
        o = self._observation
        return [
            AccommodationOffer(
                destination=o.destination, check_in=o.check_in, check_out=o.check_out,
                total_price=o.price, currency=o.currency,
                name=o.name or "Unknown", rating=None, provider=o.provider,
            )
        ]

    def get_typical_total_price(self, destination: str, nights: int, month: int) -> float | None:
        return None
