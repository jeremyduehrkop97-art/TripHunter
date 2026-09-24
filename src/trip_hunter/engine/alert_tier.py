"""Classifies an already-detected Deal into one of 3 alert tiers, purely
for CHANNEL ROUTING purposes (dispatch/telegram.py's dispatch_deal_alert) -
deliberately separate from DealType, the same way BaselineSource is kept
separate from DealType in models.py: DealType says what KIND of deal was
found, AlertTier says how urgently/where it should be pushed. Nothing here
re-derives or overrides a Deal's own deal_type/savings_percentage - it only
groups the engine's existing, already-tested classifications.

- Tier 1 (Error Fare): deal_type is ERROR_FARE (covers both the absolute
  floor trigger and the existing 70%-below-median relative threshold - see
  engine/error_fare_floor.py and engine/flight_deal_detector.py), OR the
  overall savings_percentage is itself >= TIER_1_SAVINGS_PCT_THRESHOLD
  (60%) even if deal_type ended up something else (e.g. a COMBINED_TRIP_DROP
  whose blended percentage happens to clear 60%). Pushed to VIP immediately
  and to Free as a teaser.
- Tier 2 (Combined Drop): FLIGHT_DROP or COMBINED_TRIP_DROP (both already
  require >= 30% flight savings by construction - see
  FLIGHT_DROP_THRESHOLD/COMBINED_TRIP_DROP_PCT_THRESHOLD) that didn't
  already qualify for Tier 1. Pushed to VIP and Free, same as Tier 1.
- Tier 3 (Good Deal): UNUSUALLY_LOW or HOTEL_DROP - a solid, worth-knowing
  saving that isn't urgent enough to interrupt the Free channel with.
  VIP-exclusive: keeps VIP subscribers getting steady content without
  spamming Free with every minor saving.
- None: BASELINE_UNAVAILABLE / PRICE_INCOMPLETE (or any future DealType
  this module doesn't yet know about) - not alert-worthy at all. In
  practice this should never reach dispatch_deal_alert, since
  DEFAULT_INSTANT_ALERT_CRITERIA.allowed_deal_types already excludes
  those deal_types upstream - treated the same as Tier 3 (VIP-only, never
  Free) as a defensive default if it ever does.
"""

from __future__ import annotations

from enum import Enum

from trip_hunter.models import Deal, DealType

# A Deal's overall savings_percentage clearing this bar promotes it to
# Tier 1 even if its own deal_type isn't ERROR_FARE - deliberately lower
# than flight_deal_detector.ERROR_FARE_THRESHOLD (0.70): that threshold
# decides DealType.ERROR_FARE itself (unchanged), this one decides
# "urgent enough for Tier 1 alert routing", a related but separate bar.
TIER_1_SAVINGS_PCT_THRESHOLD = 0.60

_TIER_2_DEAL_TYPES = frozenset({DealType.FLIGHT_DROP, DealType.COMBINED_TRIP_DROP})
_TIER_3_DEAL_TYPES = frozenset({DealType.UNUSUALLY_LOW, DealType.HOTEL_DROP})


class AlertTier(str, Enum):
    TIER_1_ERROR_FARE = "TIER_1_ERROR_FARE"
    TIER_2_COMBINED_DROP = "TIER_2_COMBINED_DROP"
    TIER_3_GOOD_DEAL = "TIER_3_GOOD_DEAL"


def classify_alert_tier(deal: Deal) -> AlertTier | None:
    """Which alert tier `deal` belongs to for channel-routing purposes, or
    None if it isn't alert-worthy at all (see module docstring)."""
    if deal.deal_type is DealType.ERROR_FARE:
        return AlertTier.TIER_1_ERROR_FARE
    if deal.savings_percentage is not None and deal.savings_percentage >= TIER_1_SAVINGS_PCT_THRESHOLD:
        return AlertTier.TIER_1_ERROR_FARE

    if deal.deal_type in _TIER_2_DEAL_TYPES:
        return AlertTier.TIER_2_COMBINED_DROP

    if deal.deal_type in _TIER_3_DEAL_TYPES:
        return AlertTier.TIER_3_GOOD_DEAL

    return None


def is_free_channel_eligible(tier: AlertTier | None) -> bool:
    """Only Tier 1/2 ever reach the Free channel - Tier 3 (and the
    defensive None case) is VIP-exclusive. A single, named predicate
    instead of inlining the tier comparison at each call site."""
    return tier in (AlertTier.TIER_1_ERROR_FARE, AlertTier.TIER_2_COMBINED_DROP)
