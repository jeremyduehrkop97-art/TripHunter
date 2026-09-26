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

DEAL SHEET - `build_deal_sheet_url(...)`: the URL of the in-app deal page
(web/deal.html, served by GitHub Pages) that the VIP alert's single "Deal
sichern" button opens as a Telegram Mini App. Every deal fact travels in
the QUERY STRING (not the hash: Telegram adds its own #tgWebAppData to a
Mini App URL). Base URL: env DEAL_SHEET_URL, default DEFAULT_DEAL_SHEET_URL
(this repo's Pages site); DEAL_SHEET_URL=off (also none/0/false)
disables the sheet, and the alert falls back to plain URL buttons.

All text goes through urllib's UTF-8 percent-encoding, so umlauts, "&", "#"
and spaces in hotel/city names can't break or inject parameters.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
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

DEAL_SHEET_URL_ENV = "DEAL_SHEET_URL"
DEFAULT_DEAL_SHEET_URL = "https://jeremyduehrkop97-art.github.io/TripHunter/deal.html"
_DISABLED_VALUES = frozenset({"off", "none", "0", "false", "no", "disabled"})

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


# --- deal sheet (Telegram Mini App page) -----------------------------------------


def deal_sheet_base_url() -> str | None:
    """The configured deal-sheet page URL, the default, or None if the
    sheet is switched off (DEAL_SHEET_URL=off)."""
    configured = _env(DEAL_SHEET_URL_ENV)
    if configured is None:
        return DEFAULT_DEAL_SHEET_URL
    if configured.lower() in _DISABLED_VALUES:
        return None
    return configured


def build_deal_sheet_url(
    *,
    flight_link: str,
    hotel_link: str | None = None,
    origin_city: str = "",
    destination_city: str = "",
    destination_code: str = "",
    flag: str = "",
    departure_date: date | None = None,
    return_date: date | None = None,
    flight_price: float | None = None,
    hotel_price: float | None = None,
    total_price: float | None = None,
    hotel_name: str = "",
    savings_percent: float | None = None,
    image_url: str | None = None,
    base_url: str | None = None,
) -> str | None:
    """URL of the deal sheet for one deal, or None if the sheet is
    disabled. Prices are per person, whole euros; empty/None values are
    simply left out of the query string. The page validates everything
    again (https + allowlisted hosts for links and image), so this only
    builds - it never has to be trusted.
    """
    base = base_url if base_url is not None else deal_sheet_base_url()
    if not base:
        return None

    def whole(value: float | None) -> str | None:
        # commercial rounding (102.5 -> 103), like the alert text
        return None if value is None else str(int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))

    params = {
        "from": origin_city,
        "to": destination_city,
        "code": destination_code,
        "flag": flag,
        "dep": departure_date.isoformat() if departure_date else None,
        "ret": return_date.isoformat() if return_date else None,
        "fp": whole(flight_price),
        "hp": whole(hotel_price),
        "tp": whole(total_price),
        "hn": hotel_name,
        "sv": whole(savings_percent),
        "fl": flight_link,
        "hl": hotel_link,
        "img": image_url,
    }
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")}, quote_via=quote)
    return f"{base}{'&' if '?' in base else '?'}{query}"


# --- share links (word-of-mouth button) ---------------------------------------------

SHARE_PROVIDERS = ("whatsapp", "telegram")


def build_share_url(text: str, *, provider: str = "whatsapp", url: str | None = None) -> str:
    """A "share this with a friend" link that opens the messenger with
    `text` pre-filled: WhatsApp (api.whatsapp.com/send) or, for
    provider="telegram", Telegram's share dialog (`url` is then the shared
    link, `text` the message next to it). The text is UTF-8 percent-encoded
    (spaces as %20, never "+"), so umlauts, emoji, "&" and "#" survive."""
    if provider == "telegram":
        params = {"url": url or "", "text": text}
        return "https://t.me/share/url?" + urlencode(params, quote_via=quote)
    return "https://api.whatsapp.com/send?" + urlencode({"text": text}, quote_via=quote)
