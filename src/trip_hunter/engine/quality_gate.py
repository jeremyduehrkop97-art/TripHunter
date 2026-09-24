"""Quality filters for regular (Tier 2 / Tier 3) deals - keeps out
junk hotels and unpleasant flight times. Tier 1 (error fares) is exempt
by design: a mistake fare is worth booking whatever the hotel rating or
departure time (see alert_tier.classify_alert_tier for what counts as
Tier 1). Deals with no alert tier (BASELINE_UNAVAILABLE etc.) are not
gated either.

Both checks are defensive: a missing value never blocks a deal.
- Hotel: `AccommodationOffer.rating` is on the 0.0-5.0 star scale
  (SerpApi / Google Hotels); rating None or no accommodation = pass.
- Stops: on short-haul destinations (error_fare_floor.SHORT_HAUL_DESTINATIONS,
  an explicit European allowlist) only NONSTOP flights (`stops == 0`)
  pass - nobody books a weekend city trip with a stopover. Destinations
  outside that list are not forced nonstop.
- Flight times: `FlightOffer.departure_time` / `return_time` are optional
  "HH:MM" strings. `departure_time` is the OUTBOUND departure, checked
  against EARLIEST_OUTBOUND_DEPARTURE. `return_time` is NOT the return
  flight's departure - providers only fill it with the return leg's
  ARRIVAL time (or leave it None), so the "return departs after 12:00"
  rule cannot be checked from the current data model and is not applied.
  Missing or unparsable times never block a deal.
"""

from __future__ import annotations

from datetime import time

from trip_hunter.engine.alert_tier import AlertTier, classify_alert_tier
from trip_hunter.engine.error_fare_floor import SHORT_HAUL_DESTINATIONS
from trip_hunter.models import Deal

MIN_HOTEL_RATING = 3.8
EARLIEST_OUTBOUND_DEPARTURE = time(6, 0)

_GATED_TIERS = frozenset({AlertTier.TIER_2_COMBINED_DROP, AlertTier.TIER_3_GOOD_DEAL})


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def passes_quality_gate(deal: Deal) -> bool:
    if classify_alert_tier(deal) not in _GATED_TIERS:
        return True

    accommodation = deal.accommodation
    if accommodation is not None and accommodation.rating is not None:
        if accommodation.rating < MIN_HOTEL_RATING:
            return False

    if deal.flight.destination in SHORT_HAUL_DESTINATIONS and deal.flight.stops != 0:
        return False

    departure = _parse_hhmm(deal.flight.departure_time)
    if departure is not None and departure < EARLIEST_OUTBOUND_DEPARTURE:
        return False

    return True
