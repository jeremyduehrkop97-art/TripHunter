"""Abstraction for accommodation data sources.

Mirrors `FlightProvider`: the deal engine never talks to a concrete
accommodation source like Booking.com directly, only to this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from trip_hunter.models import AccommodationOffer


class AccommodationProvider(ABC):
    @abstractmethod
    def search_accommodations(
        self,
        destination: str,
        check_in: date,
        check_out: date,
    ) -> list[AccommodationOffer]:
        """Return available accommodation offers for the destination and date range."""

    @abstractmethod
    def get_typical_total_price(self, destination: str, nights: int, month: int) -> float | None:
        """Return the usual/expected total price for a stay of this length.

        Returns None when no baseline is known for this destination -
        callers must treat that as "we don't know", never as "the price is
        normal". Mirrors FlightProvider.get_typical_price; see "Baseline
        Problem" in docs/PRODUCT_SPEC.md. Widened from a non-optional
        `float` (MVP 0.1/0.2, when only hardcoded mock data existed and a
        baseline was always available) once a real provider - which may
        genuinely have no typical-price concept - was added.
        """
