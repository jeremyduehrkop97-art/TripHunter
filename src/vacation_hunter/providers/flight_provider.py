"""Abstraction for flight data sources.

Business logic (deal detection, scoring, trip combination) only ever talks
to this interface, never to a concrete provider like Amadeus or Skyscanner.
That keeps the engine swappable: adding a new data source means writing a
new class that implements these two methods, nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from vacation_hunter.models import FlightOffer


class FlightProvider(ABC):
    @abstractmethod
    def search_flights(
        self,
        origin: str,
        destination: str,
        earliest_departure: date,
        latest_departure: date,
    ) -> list[FlightOffer]:
        """Return available flight offers for the given route and date window."""

    @abstractmethod
    def get_typical_price(self, origin: str, destination: str, month: int) -> float:
        """Return the usual/expected round-trip price for this route in a given month.

        This baseline is what deal detection compares real offers against.
        """
