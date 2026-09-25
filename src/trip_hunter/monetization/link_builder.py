"""Deeplinks for the "✈️ Flug prüfen" / "🏨 Hotel ansehen" alert buttons.

Both builders ALWAYS return a working link and never raise: every
affiliate setting is an optional environment variable, and without it the
link is simply a clean, untracked search URL.

FLIGHTS - `build_flight_link(origin, destination, departure_date,
return_date)`. Provider (env FLIGHT_LINK_PROVIDER, or the `provider`
argument):
  - "google":     Google Flights search. No partner program, never tracked.
  - "aviasales":  Aviasales (Travelpayouts) search; with TRAVELPAYOUTS_MARKER
                  the `marker` parameter is added - Travelpayouts' own
                  tracking parameter, which is all Aviasales needs.
  - "skyscanner": Skyscanner search. Skyscanner runs through Travelpayouts
                  as a program with its own IDs, so a commission link is
                  only built when TRAVELPAYOUTS_MARKER AND
                  TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ID (the `p` of your
                  Travelpayouts program list; optional
                  TRAVELPAYOUTS_CAMPAIGN_ID) are set - it is then wrapped in
                  Travelpayouts' tp.media redirect. We do not guess program
                  IDs: missing either, you get the plain Skyscanner search.
  Unset provider: "aviasales" if TRAVELPAYOUTS_MARKER is set (the one
  provider that earns with just the marker), else "google".
  Unknown provider names fall back the same way.

HOTELS - `build_hotel_link(hotel_name, city, checkin_date, checkout_date)`:
a Booking.com search for "<hotel>, <city>" with the stay's dates and 2
adults (matching the room-for-2 prices in the alerts); with
BOOKING_AFFILIATE_ID the `aid` parameter is added. HOTEL_LINK_PROVIDER=google
switches to a plain Google Hotels search (no dates, never tracked).

All text goes through urllib's UTF-8 percent-encoding, so umlauts, "&", "#"
and spaces in hotel/city names can't break or inject parameters.
"""

from __future__ import annotations

import os
from datetime import date
from urllib.parse import quote, urlencode

# Importing config.py triggers its .env-loading side effect at import time -
# same pattern as monetization/affiliate.py.
import trip_hunter.config  # noqa: F401

BOOKING_AFFILIATE_ID_ENV = "BOOKING_AFFILIATE_ID"
TRAVELPAYOUTS_MARKER_ENV = "TRAVELPAYOUTS_MARKER"
TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ENV = "TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ID"
TRAVELPAYOUTS_CAMPAIGN_ENV = "TRAVELPAYOUTS_CAMPAIGN_ID"
FLIGHT_LINK_PROVIDER_ENV = "FLIGHT_LINK_PROVIDER"
HOTEL_LINK_PROVIDER_ENV = "HOTEL_LINK_PROVIDER"

FLIGHT_PROVIDERS = ("google", "aviasales", "skyscanner")
_GUESTS = 2


def _env(name: str) -> str | None:
    """The variable's stripped value; unset, empty or whitespace = None."""
    return (os.environ.get(name) or "").strip() or None


# --- flights --------------------------------------------------------------------


def build_flight_link(
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date | None = None,
    *,
    provider: str | None = None,
) -> str:
    """Search link for one flight connection (`return_date=None` = one
    way). See the module docstring for the provider rules."""
    marker = _env(TRAVELPAYOUTS_MARKER_ENV)
    chosen = (provider or _env(FLIGHT_LINK_PROVIDER_ENV) or "").lower()
    if chosen not in FLIGHT_PROVIDERS:
        chosen = "aviasales" if marker else "google"

    if chosen == "aviasales":
        return _aviasales_link(origin, destination, departure_date, return_date, marker)
    if chosen == "skyscanner":
        return _skyscanner_link(origin, destination, departure_date, return_date, marker)
    return _google_flights_link(origin, destination, departure_date, return_date)


def _google_flights_link(origin: str, destination: str, departure: date, return_: date | None) -> str:
    query = f"Flights from {origin} to {destination} on {departure.isoformat()}"
    if return_ is not None:
        query += f" through {return_.isoformat()}"
    return "https://www.google.com/travel/flights?" + urlencode({"q": query, "hl": "de", "curr": "EUR"}, quote_via=quote)


def _aviasales_link(
    origin: str, destination: str, departure: date, return_: date | None, marker: str | None
) -> str:
    # Aviasales search path: ORIGIN DDMM DEST [DDMM] + passenger count.
    path = f"{_code(origin)}{departure:%d%m}{_code(destination)}"
    if return_ is not None:
        path += f"{return_:%d%m}"
    url = f"https://www.aviasales.com/search/{path}1"
    return url + ("?" + urlencode({"marker": marker}, quote_via=quote) if marker else "")


def _skyscanner_link(
    origin: str, destination: str, departure: date, return_: date | None, marker: str | None
) -> str:
    path = f"{_code(origin).lower()}/{_code(destination).lower()}/{departure:%y%m%d}/"
    if return_ is not None:
        path += f"{return_:%y%m%d}/"
    plain = f"https://www.skyscanner.de/transport/fluge/{path}"
    program = _env(TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ENV)
    if not marker or not program:
        return plain
    params = {"marker": marker, "p": program, "u": plain}
    campaign = _env(TRAVELPAYOUTS_CAMPAIGN_ENV)
    if campaign:
        params["campaign_id"] = campaign
    return "https://tp.media/r?" + urlencode(params, quote_via=quote)


def _code(value: str) -> str:
    """An IATA code as a URL path segment: letters/digits only, uppercase."""
    return "".join(ch for ch in value if ch.isalnum()).upper()


# --- hotels ---------------------------------------------------------------------


def build_hotel_link(
    hotel_name: str,
    city: str,
    checkin_date: date,
    checkout_date: date,
    *,
    provider: str | None = None,
) -> str:
    """Search link for one hotel stay - Booking.com (with `aid` if
    BOOKING_AFFILIATE_ID is set) or a plain Google Hotels search."""
    place = ", ".join(part.strip() for part in (hotel_name, city) if part and part.strip())
    chosen = (provider or _env(HOTEL_LINK_PROVIDER_ENV) or "booking").lower()

    if chosen == "google":
        return "https://www.google.com/travel/search?" + urlencode({"q": place, "hl": "de"}, quote_via=quote)

    params = {
        "ss": place,
        "checkin": checkin_date.isoformat(),
        "checkout": checkout_date.isoformat(),
        "group_adults": _GUESTS,
        "no_rooms": 1,
        "group_children": 0,
    }
    aid = _env(BOOKING_AFFILIATE_ID_ENV)
    if aid:
        params["aid"] = aid
    return "https://www.booking.com/searchresults.de.html?" + urlencode(params, quote_via=quote)
