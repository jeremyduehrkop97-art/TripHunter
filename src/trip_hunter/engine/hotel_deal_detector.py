"""Rule-based classification of a single accommodation offer against its baseline price."""

from __future__ import annotations

from dataclasses import dataclass

from trip_hunter.models import AccommodationOffer, DealType

HOTEL_DROP_THRESHOLD = 0.25


@dataclass(frozen=True)
class HotelDealAssessment:
    deal_type: DealType | None
    savings_absolute: float
    savings_percentage: float


def assess_accommodation(
    offer: AccommodationOffer, typical_total_price: float
) -> HotelDealAssessment:
    savings_absolute = typical_total_price - offer.total_price
    savings_percentage = (
        savings_absolute / typical_total_price if typical_total_price > 0 else 0.0
    )

    deal_type = DealType.HOTEL_DROP if savings_percentage >= HOTEL_DROP_THRESHOLD else None

    return HotelDealAssessment(
        deal_type=deal_type,
        savings_absolute=savings_absolute,
        savings_percentage=savings_percentage,
    )
