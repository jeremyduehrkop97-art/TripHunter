"""Abstraction for flight data sources.

Business logic (deal detection, scoring, trip combination) only ever talks
to this interface, never to a concrete provider like Amadeus or Skyscanner.
That keeps the engine swappable: adding a new data source means writing a
new class that implements these two methods, nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from trip_hunter.models import FlightOffer, PriceInsight


class FlightProvider(ABC):
    @abstractmethod
    def search_flights(
        self,
        origin: str,
        destination: str,
        earliest_departure: date,
        latest_departure: date,
        return_date: date | None = None,
    ) -> list[FlightOffer]:
        """Return available flight offers for the given route and date window.

        `earliest_departure`/`latest_departure` describe the departure-date
        window to search within. `return_date` is optional (one-way search
        when omitted) and is passed through as an exact date for providers
        that need one, such as a real flight search API.
        """

    @abstractmethod
    def get_typical_price(self, origin: str, destination: str, month: int) -> float | None:
        """Return the usual/expected round-trip price for this route in a given month.

        This baseline is what deal detection compares real offers against.
        Returns None when no baseline is known for this route - callers must
        treat that as "we don't know", never as "the price is normal". See
        "Baseline Problem" in docs/PRODUCT_SPEC.md.
        """

    def get_price_insight(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date | None,
    ) -> PriceInsight | None:
        """Return a provider-supplied price insight for this search, if the
        provider has one - a fallback baseline when `get_typical_price`
        returns None. Not abstract: most providers (mock, Amadeus) have no
        such concept, so the default is "no insight". Only a provider that
        genuinely receives this from its API (e.g. Google Flights Price
        Insights via SerpApi) should override it - never fabricate one.
        """
        return None
