"""Absolute price-floor triggers for "obvious" error fares.

The normal ERROR_FARE path (flight_deal_detector.assess_flight) needs a
real baseline - our own >= 5 observations, or a provider price insight -
before it can call anything an error fare; by design it never guesses. A
genuine mistake fare is exactly the case where that safeguard would
otherwise silently do nothing: a brand-new route with zero history can
still show an obviously broken price (a European short-haul round trip
for 29 EUR), and a human deal-hunter would recognize that instantly
without needing 5 prior observations first. These floors are that same
instant recognition, made explicit and narrow - fixed EUR ceilings, not a
percentage - and are only ever consulted as the LAST resort in
DealEngine._evaluate_flight, after both the own-historical-baseline and
provider-price-insight paths have already been tried and found nothing.
A floor never overrides a real baseline that says otherwise - see
deal_engine.py for exactly where this plugs in.
"""

from __future__ import annotations

from trip_hunter.models import AccommodationOffer, FlightOffer

# Round-trip price ceilings, EUR. Deliberately two tiers, not one - see
# MID_HAUL_DESTINATIONS below for what counts as the higher tier.
SHORT_HAUL_ERROR_FARE_FLOOR = 35.0
MID_HAUL_ERROR_FARE_FLOOR = 70.0

# The paired hotel (if any accommodation data is available at all - never
# REQUIRED for the trigger to fire, only ever a negative gate when we do
# have a number to check) must not exceed this EUR/night price. An
# absurdly cheap flight paired with an expensive hotel isn't the "whole
# trip is a steal" story this trigger exists to catch.
MAX_HOTEL_PRICE_PER_NIGHT = 65.0

# Fixed, transparent score for a floor-triggered ERROR_FARE - there is no
# percentage saving to derive one from (no baseline exists, see module
# docstring), so this is a deliberate, documented constant rather than a
# guessed number. Kept just under 100 to leave headroom, not implying
# mathematical certainty.
FLOOR_TRIGGER_SCORE = 92

# Explicit IATA-code allowlist for "Mittelstrecke / Urlaubsziele" (Kanaren,
# Griechenland) - the higher-floor tier. Never inferred from geography;
# extend this set explicitly when a new mid-haul destination is added to
# sampling_targets.py - same pattern as
# serpapi_accommodation_provider.py's _DESTINATION_QUERY. Every other
# destination defaults to the short-haul European floor.
MID_HAUL_DESTINATIONS: frozenset[str] = frozenset(
    {
        # Kanaren (Canary Islands)
        "LPA", "TFS", "TFN", "ACE", "FUE", "SPC", "GMZ", "VDE",
        # Griechenland (Greece) - mainland + major islands
        "ATH", "SKG", "HER", "CHQ", "RHO", "CFU", "JMK", "JTR", "KGS",
    }
)


def error_fare_floor_for(destination: str) -> float:
    """The absolute round-trip EUR ceiling that triggers a floor-based
    ERROR_FARE for `destination` - MID_HAUL_ERROR_FARE_FLOOR for the
    explicit mid-haul allowlist, SHORT_HAUL_ERROR_FARE_FLOOR otherwise.
    """
    if destination in MID_HAUL_DESTINATIONS:
        return MID_HAUL_ERROR_FARE_FLOOR
    return SHORT_HAUL_ERROR_FARE_FLOOR


def error_fare_floor_triggered(
    flight: FlightOffer, accommodation: AccommodationOffer | None
) -> bool:
    """True if `flight`'s own price alone clears the absolute floor for
    its destination AND, if accommodation data exists at all, the
    accommodation is fairly priced. Accommodation is never REQUIRED for
    the trigger to fire - a flight-only floor deal is still newsworthy on
    its own; a hotel is only ever a reason to say no, never a reason a
    missing one blocks an otherwise-clear floor trigger.
    """
    if flight.price > error_fare_floor_for(flight.destination):
        return False

    if accommodation is not None:
        nights = (flight.return_date - flight.departure_date).days
        if nights > 0 and (accommodation.total_price / nights) > MAX_HOTEL_PRICE_PER_NIGHT:
            return False

    return True
