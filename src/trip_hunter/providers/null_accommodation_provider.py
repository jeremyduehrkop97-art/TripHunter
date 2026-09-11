"""A no-op AccommodationProvider.

Used where no accommodation data source is wired up yet - e.g. the
real-flight demo, which is flight-only for MVP 0.2 (no hotel API). It
always reports "no offers", so DealEngine gracefully falls back to
flight-only deals instead of failing or fabricating accommodation data.
"""

from __future__ import annotations

from datetime import date

from trip_hunter.models import AccommodationOffer
from trip_hunter.providers.accommodation_provider import AccommodationProvider


class NullAccommodationProvider(AccommodationProvider):
    def search_accommodations(
        self, destination: str, check_in: date, check_out: date
    ) -> list[AccommodationOffer]:
        return []

    def get_typical_total_price(self, destination: str, nights: int, month: int) -> float | None:
        raise NotImplementedError(
            "NullAccommodationProvider has no data; this should never be called "
            "because search_accommodations always returns an empty list."
        )
