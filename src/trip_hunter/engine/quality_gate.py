"""Quality filters for regular (Tier 2 / Tier 3) deals. Tier 1 (error
fares) is exempt by design: a mistake fare is worth booking whatever the
hotel rating or routing (see alert_tier.classify_alert_tier for what
counts as Tier 1). Deals with no alert tier (BASELINE_UNAVAILABLE etc.)
are not gated either.

- Hotel: `AccommodationOffer.rating` is on the 0.0-5.0 star scale
  (SerpApi / Google Hotels) and must be >= MIN_HOTEL_RATING; a missing
  rating or no accommodation passes. (The engine already prefers
  well-rated hotels - engine/hotel_filter.py - so this only bites when no
  well-rated one existed.)
- Stops: on short-haul destinations (error_fare_floor.SHORT_HAUL_DESTINATIONS,
  an explicit European allowlist) only NONSTOP flights (`stops == 0`)
  pass - nobody books a weekend city trip with a stopover. Destinations
  outside that list are not forced nonstop.

Flight times are deliberately NOT restricted: we optimise purely on
price. (A comfortable departure is merely highlighted in the alert -
alerts/instant_alert_formatter.py.)
"""

from __future__ import annotations

from trip_hunter.engine.alert_tier import AlertTier, classify_alert_tier
from trip_hunter.engine.error_fare_floor import SHORT_HAUL_DESTINATIONS
from trip_hunter.engine.hotel_filter import MIN_HOTEL_RATING
from trip_hunter.models import Deal

_GATED_TIERS = frozenset({AlertTier.TIER_2_COMBINED_DROP, AlertTier.TIER_3_GOOD_DEAL})


def passes_quality_gate(deal: Deal) -> bool:
    if classify_alert_tier(deal) not in _GATED_TIERS:
        return True

    accommodation = deal.accommodation
    if accommodation is not None and accommodation.rating is not None:
        if accommodation.rating < MIN_HOTEL_RATING:
            return False

    if deal.flight.destination in SHORT_HAUL_DESTINATIONS and deal.flight.stops != 0:
        return False

    return True
