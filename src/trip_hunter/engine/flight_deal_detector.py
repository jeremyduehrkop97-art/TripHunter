"""Rule-based classification of a single flight offer against its baseline price.

A flight deal is defined purely by its discount against the route's normal
price (our own median of past best prices for that exact trip, any airline
- see engine/price_statistics.py; flight/landing times play no role): at
least FLIGHT_DROP_THRESHOLD (30%) AND at least MIN_FLIGHT_DROP_SAVINGS_EUR
(25 EUR) below it. Without >= MIN_HISTORY_OBSERVATIONS of our own history
the baseline is unavailable and the conservative fallbacks apply (provider
price insight, absolute error-fare floor) - never a guessed baseline.

If no baseline price is available (e.g. a real flight-search API that only
returns current prices, no history), we must not guess. See "Baseline
Problem" in docs/PRODUCT_SPEC.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from trip_hunter.models import DealType, FlightOffer, PriceInsight

# Savings-percentage cutoffs against the typical price for the route.
# Deliberately simple and explicit so they're easy to tune later once we
# have real price history. See docs/PRODUCT_SPEC.md.
ERROR_FARE_THRESHOLD = 0.70
FLIGHT_DROP_THRESHOLD = 0.30
UNUSUALLY_LOW_THRESHOLD = 0.15

# A flight is only a FLIGHT_DROP / ERROR_FARE if it is ALSO at least this
# many EUR below its baseline - a 40% drop on a 20 EUR flight is 8 EUR of
# noise, not a deal. A cheaper-looking flight that misses this bar falls
# back to the lesser UNUSUALLY_LOW label (informational, never alerted -
# see build_newsletter.DEFAULT_TIER_3_CRITERIA).
MIN_FLIGHT_DROP_SAVINGS_EUR = 25.0


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

    big_enough = savings_absolute >= MIN_FLIGHT_DROP_SAVINGS_EUR
    if savings_percentage >= ERROR_FARE_THRESHOLD and big_enough:
        deal_type = DealType.ERROR_FARE
    elif savings_percentage >= FLIGHT_DROP_THRESHOLD and big_enough:
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

    if savings_percentage >= FLIGHT_DROP_THRESHOLD and savings_absolute >= MIN_FLIGHT_DROP_SAVINGS_EUR:
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
