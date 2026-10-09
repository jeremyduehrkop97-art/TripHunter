"""A flight price GUIDE (Richtpreis) for hotel-first feed-radar signals: a
representative round-trip EUR estimate per person, used only to build the
illustrative hotel+flight combo total in the "Hotel-Drop inkl. Flug"
layout (see alerts/instant_alert_formatter.py's _hotel_price_summary) -
only once a real, exact date is known; a dateless hotel-lead signal never
reaches this at all any more.

WHAT THIS IS NOT: a live quote, a specific flight, or a SerpApi-verified
price. A hotel-lead feed signal has no real flight data at all (it rarely
even names a DACH departure airport - see engine/feed_sensor.py's
_is_hotel_lead_title), so this is an openly documented, reviewable
ESTIMATE grouped by the same three-tier distance classification
engine/flexible_dates.py already uses for realistic trip length - never a
guessed number for an unknown distance, and always shown to the end user
labelled "zubuchbar ab ca." ("addable, from approx."), never as a
confirmed booking price.

Ranges (as given, midpoint used as the guide figure):
  - short-haul (Europe): 90-120 EUR -> 105 EUR
  - mid-haul (Gulf/Orient, Canary Islands, North Africa): 220-280 EUR -> 250 EUR
  - long-haul (Southeast Asia, Indian Ocean, Americas, Australia/Southern Africa): 500-600 EUR -> 550 EUR
"""

from __future__ import annotations

from trip_hunter.engine.flexible_dates import LONG_HAUL_DESTINATIONS, MID_HAUL_DESTINATIONS

SHORT_HAUL_FLIGHT_GUIDE_EUR = 105
MID_HAUL_FLIGHT_GUIDE_EUR = 250
LONG_HAUL_FLIGHT_GUIDE_EUR = 550


def flight_price_guide_for(destination_iata: str | None) -> int:
    """EUR round-trip guide price per person to `destination_iata` - the
    long- or mid-haul figure for the explicit allowlists engine/
    flexible_dates.py already curates, else the short-haul figure. A
    destination with no resolved IATA code at all gets the short-haul
    (cheapest, least presumptuous) figure rather than a guessed, more
    generous one."""
    if destination_iata in LONG_HAUL_DESTINATIONS:
        return LONG_HAUL_FLIGHT_GUIDE_EUR
    if destination_iata in MID_HAUL_DESTINATIONS:
        return MID_HAUL_FLIGHT_GUIDE_EUR
    return SHORT_HAUL_FLIGHT_GUIDE_EUR
