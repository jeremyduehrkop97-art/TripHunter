"""Curation filters for turning DealEngine's raw output into a short,
user-facing list - "Wochenend-Getaways unter fixen Budgetgrenzen" and
"flexible Spontan-Trips" (see docs/PRODUCT_SPEC.md "Executive Summary").

Deliberately simple, explicit criteria - no scoring model of its own, no
machine learning. All filters compose with AND: a Deal must satisfy every
criterion that's actually set (None/False = "no constraint from this
criterion") to be kept. Never guesses a value the Deal doesn't actually
have - a Deal with no score (BASELINE_UNAVAILABLE, PRICE_INCOMPLETE) fails
any `min_score` check rather than being silently let through.
"""

from __future__ import annotations

from dataclasses import dataclass

from trip_hunter.models import Deal, DealType

# Weekday numbers per datetime.date.weekday(): Monday=0 ... Sunday=6.
_FRIDAY = 4
_SUNDAY = 6


@dataclass(frozen=True)
class DealFilterCriteria:
    """Every field is optional - unset (None / False) means "don't filter
    on this". Trip duration is always computed from the flight's own
    departure/return dates (works whether or not an accommodation offer is
    attached - see AccommodationOffer.nights for the identical formula on
    the hotel side).
    """

    # Budget ceiling for the WHOLE trip (flight + accommodation combined
    # when present, flight-only otherwise) - see Deal.actual_total_price.
    max_total_price: float | None = None
    # Trip length in nights, inclusive on both ends.
    min_nights: int | None = None
    max_nights: int | None = None
    # Strict "genuine weekend" shape: departs Friday, returns Sunday (2
    # nights) - stricter than min_nights=2/max_nights=2, which would also
    # accept e.g. a Tuesday-Thursday 2-nighter. Composable with min/max
    # nights, though redundant if both are set to exactly 2.
    weekend_trip_only: bool = False
    # Deal.score.total must be >= this. A Deal with score=None (no
    # baseline was available to score against) never satisfies this.
    min_score: int | None = None
    # If set, Deal.deal_type must be one of these. None = any deal type.
    allowed_deal_types: frozenset[DealType] | None = None

    def __post_init__(self) -> None:
        if self.min_nights is not None and self.max_nights is not None:
            if self.min_nights > self.max_nights:
                raise ValueError(
                    "DealFilterCriteria: min_nights must not be greater than max_nights."
                )

    @classmethod
    def weekend_getaway(cls, max_total_price: float, min_score: int = 60) -> "DealFilterCriteria":
        """Preset for the "Wochenend-Getaways unter fixen Budgetgrenzen"
        product vision: a genuine Friday->Sunday weekend, under a fixed
        budget ceiling, only deals good enough to actually be worth an alert.
        """
        return cls(max_total_price=max_total_price, weekend_trip_only=True, min_score=min_score)

    @classmethod
    def spontaneous_trip(
        cls,
        max_total_price: float,
        min_nights: int = 2,
        max_nights: int = 4,
        min_score: int = 60,
    ) -> "DealFilterCriteria":
        """Preset for the "flexible Spontan-Trips" product vision: any
        short trip (2-4 nights by default) under budget, not tied to a
        specific weekday pattern.
        """
        return cls(
            max_total_price=max_total_price,
            min_nights=min_nights,
            max_nights=max_nights,
            min_score=min_score,
        )


def matches(deal: Deal, criteria: DealFilterCriteria) -> bool:
    """Does `deal` satisfy every set criterion?"""
    if criteria.max_total_price is not None and deal.actual_total_price > criteria.max_total_price:
        return False

    nights = (deal.flight.return_date - deal.flight.departure_date).days

    if criteria.min_nights is not None and nights < criteria.min_nights:
        return False
    if criteria.max_nights is not None and nights > criteria.max_nights:
        return False

    if criteria.weekend_trip_only:
        departs_friday = deal.flight.departure_date.weekday() == _FRIDAY
        returns_sunday = deal.flight.return_date.weekday() == _SUNDAY
        if not (departs_friday and returns_sunday):
            return False

    if criteria.min_score is not None:
        if deal.score is None or deal.score.total < criteria.min_score:
            return False

    if criteria.allowed_deal_types is not None:
        if deal.deal_type not in criteria.allowed_deal_types:
            return False

    return True


def filter_deals(deals: list[Deal], criteria: DealFilterCriteria) -> list[Deal]:
    """Reduce a raw DealEngine result list to only the deals worth
    surfacing to a user, per `criteria`. Preserves the input order.
    """
    return [deal for deal in deals if matches(deal, criteria)]
