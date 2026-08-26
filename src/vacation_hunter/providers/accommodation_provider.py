"""Abstraction for accommodation data sources.

Mirrors `FlightProvider`: the deal engine never talks to a concrete
accommodation source like Booking.com directly, only to this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from vacation_hunter.models import AccommodationOffer


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
    def get_typical_total_price(self, destination: str, nights: int, month: int) -> float:
        """Return the usual/expected total price for a stay of this length."""
