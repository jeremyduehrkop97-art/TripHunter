"""Rule-based classification of a single accommodation offer against its baseline price."""

from __future__ import annotations

from dataclasses import dataclass

from trip_hunter.models import AccommodationOffer, DealType

HOTEL_DROP_THRESHOLD = 0.25


@dataclass(frozen=True)
class HotelDealAssessment:
    deal_type: DealType | None
    savings_absolute: float | None
    savings_percentage: float | None


def assess_accommodation(
    offer: AccommodationOffer, typical_total_price: float | None
) -> HotelDealAssessment:
    """`typical_total_price` may be None when the AccommodationProvider has
    no baseline for this destination (e.g. no Google Hotels price-insight
    equivalent and no historical baseline of our own yet - see "Baseline
    Problem" in docs/PRODUCT_SPEC.md and the module docstring in
    serpapi_accommodation_provider.py). Mirrors
    flight_deal_detector.assess_flight's handling of typical_price=None:
    no HOTEL_DROP is claimed, nothing is guessed.
    """
    if typical_total_price is None:
        return HotelDealAssessment(deal_type=None, savings_absolute=None, savings_percentage=None)

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
