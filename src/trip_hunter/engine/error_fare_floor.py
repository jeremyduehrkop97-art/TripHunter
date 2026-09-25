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

from trip_hunter.models import FlightOffer

# Round-trip price ceilings, EUR. Deliberately two tiers, not one - see
# MID_HAUL_DESTINATIONS below for what counts as the higher tier.
SHORT_HAUL_ERROR_FARE_FLOOR = 35.0
MID_HAUL_ERROR_FARE_FLOOR = 70.0

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


# Explicit allowlist of European short-haul destinations (incl. the
# mid-haul Canary/Greek ones above, which are served nonstop from
# Germany) - the routes where engine/quality_gate.py insists on nonstop
# flights. Positive list on purpose: a destination not listed here is
# never forced nonstop, so an unlisted long-haul city can't be wrongly
# dropped. Extend it together with sampling_targets.py.
SHORT_HAUL_DESTINATIONS: frozenset[str] = frozenset(
    {
        *MID_HAUL_DESTINATIONS,
        "PMI", "BCN", "FCO", "LIS", "BGY", "MXP", "VCE", "VIE", "STN", "LON", "LHR", "LGW", "OPO", "FAO",
        "MAD", "AGP", "SVQ", "VLC", "IBZ", "CDG", "NCE", "AMS", "DUB", "CPH", "PRG", "BUD", "IST", "AYT",
    }
)

# Explicit intercontinental allowlist (higher Tier-1 price bar in
# engine/feed_sensor.py) - never inferred from geography.
LONG_HAUL_DESTINATIONS: frozenset[str] = frozenset(
    {
        "JFK", "EWR", "LAX", "SFO", "ORD", "MIA", "BOS", "IAD", "ATL", "DFW", "SEA", "YYZ", "YVR",
        "MEX", "CUN", "PUJ", "HAV", "GRU", "EZE", "BOG", "SCL", "LIM", "BKK", "CNX", "SIN", "HKG", "NRT",
        "HND", "ICN", "PEK", "PVG", "DEL", "BOM", "BLR", "DXB", "DOH", "AUH", "JNB", "CPT", "NBO",
        "SYD", "MEL", "AKL", "NYC", "WAS", "CHI", "MLE", "FRU", "TYO", "SEL", "DPS", "TPE", "YYC",
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


def error_fare_floor_triggered(flight: FlightOffer) -> bool:
    """True if `flight`'s own price alone clears the absolute floor for
    its destination. Deliberately independent of any hotel (price,
    rating, or absence): an airline mistake fare must always fire as
    Tier 1, never be blocked by an expensive - or missing - hotel. Flight
    times are ignored for the same reason (see engine/quality_gate.py,
    which exempts Tier 1).
    """
    return flight.price <= error_fare_floor_for(flight.destination)
