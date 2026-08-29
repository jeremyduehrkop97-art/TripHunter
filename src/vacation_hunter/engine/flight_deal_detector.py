"""Rule-based classification of a single flight offer against its baseline price.

If no baseline price is available (e.g. a real flight-search API that only
returns current prices, no history), we must not guess. See "Baseline
Problem" in docs/PRODUCT_SPEC.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from vacation_hunter.models import DealType, FlightOffer, PriceInsight

# Savings-percentage cutoffs against the typical price for the route.
# Deliberately simple and explicit so they're easy to tune later once we
# have real price history. See docs/PRODUCT_SPEC.md.
ERROR_FARE_THRESHOLD = 0.70
FLIGHT_DROP_THRESHOLD = 0.30
UNUSUALLY_LOW_THRESHOLD = 0.15


@dataclass(frozen=True)
class FlightDealAssessment:
    deal_type: DealType | None
    savings_absolute: float | None
    savings_percentage: float | None


def assess_flight(offer: FlightOffer, typical_price: float | None) -> FlightDealAssessment:
    if typical_price is None:
        return FlightDealAssessment(
            deal_type=DealType.BASELINE_UNAVAILABLE,
            savings_absolute=None,
            savings_percentage=None,
        )

    savings_absolute = typical_price - offer.price
    savings_percentage = savings_absolute / typical_price if typical_price > 0 else 0.0

    if savings_percentage >= ERROR_FARE_THRESHOLD:
        deal_type = DealType.ERROR_FARE
    elif savings_percentage >= FLIGHT_DROP_THRESHOLD:
        deal_type = DealType.FLIGHT_DROP
    elif savings_percentage >= UNUSUALLY_LOW_THRESHOLD:
        deal_type = DealType.UNUSUALLY_LOW
    else:
        deal_type = None

    return FlightDealAssessment(
        deal_type=deal_type,
        savings_absolute=savings_absolute,
        savings_percentage=savings_percentage,
    )


def assess_flight_price_insight(
    offer: FlightOffer, insight: PriceInsight | None
) -> FlightDealAssessment | None:
    """Classify a flight using a provider-supplied price insight instead of
    our own historical baseline (BaselineSource.PROVIDER_PRICE_INSIGHT).

    Deliberately capped below ERROR_FARE: a provider's typical-price
    estimate isn't strong enough evidence for "probably a mistake" - that
    needs its own, stricter heuristic, which does not exist yet.

    Returns None if the insight doesn't contain a usable typical range (the
    caller must then treat this as "no baseline at all", never fabricate one).
    """
    if insight is None or insight.typical_price_low is None or insight.typical_price_high is None:
        return None

    typical_price = (insight.typical_price_low + insight.typical_price_high) / 2
    if typical_price <= 0:
        return None

    savings_absolute = typical_price - offer.price
    savings_percentage = savings_absolute / typical_price

    if savings_percentage >= FLIGHT_DROP_THRESHOLD:
        deal_type = DealType.FLIGHT_DROP
    elif savings_percentage >= UNUSUALLY_LOW_THRESHOLD:
        deal_type = DealType.UNUSUALLY_LOW
    else:
        deal_type = None

    return FlightDealAssessment(
        deal_type=deal_type,
        savings_absolute=savings_absolute,
        savings_percentage=savings_percentage,
    )
