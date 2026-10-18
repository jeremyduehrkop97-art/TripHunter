"""Deeplinks for the "✈️ Flug prüfen" / "🏨 Hotel ansehen" alert buttons.

Both builders ALWAYS return a working link and never raise: every
affiliate setting is an optional environment variable, and without it the
link is simply a clean, untracked search URL.

FLIGHTS - `build_flight_link(origin, destination, departure_date=None,
return_date=None)`. `departure_date=None` (no exact date known at all)
still returns a real, monetized ROUTE search for `origin` ->
`destination` - never a blank, routeless provider homepage (the actual
reported bug this fixed: a generic free-text Google Flights query used to
land on an empty "Wohin?" page with neither the route nor a date
pre-filled). Provider (env FLIGHT_LINK_PROVIDER, or the `provider`
argument):
  - "google":     Google Flights search - WITH the route (and the date,
                  if known) always pre-filled in the `q=` query text. No
                  partner program, never tracked.
  - "aviasales":  Aviasales (Travelpayouts) search; with TRAVELPAYOUTS_MARKER
                  the `marker` parameter is added - Travelpayouts' own
                  tracking parameter, which is all Aviasales needs.
                  ALWAYS carries `currency=eur` too (verified live,
                  2026-10: without it, a DACH user's search opened in USD
                  - reported bug). With no exact date, this is Aviasales'
                  own pre-filled SEARCH FORM (route only, confirmed via
                  Travelpayouts' own support docs) rather than its dated
                  search-results page. NO sort/layover-quality parameter
                  (2026-10 ask, researched, not found - see _aviasales_link's
                  own docstring: the results page is a terse positional
                  path with no documented filter hook, unlike Kiwi below).
  - "skyscanner": Skyscanner search. Skyscanner runs through Travelpayouts
                  as a program with its own IDs, so a commission link is
                  only built when TRAVELPAYOUTS_MARKER AND
                  TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ID (the `p` of your
                  Travelpayouts program list; optional
                  TRAVELPAYOUTS_CAMPAIGN_ID) are set - it is then wrapped in
                  Travelpayouts' tp.media redirect. We do not guess program
                  IDs: missing either, you get the plain Skyscanner search.
  - "kiwi":       Kiwi.com deep-link search (build_kiwi_flight_link) -
                  ALWAYS the plain, direct Kiwi.com link, tracked only via
                  KIWI_AFFILIATE_ID (Kiwi's own `affilid` parameter, a
                  direct Kiwi partnership credential) - see that
                  function's own docstring for why TRAVELPAYOUTS_MARKER
                  does NOT apply to Kiwi.com here (verified live,
                  "promo_id is empty" - Kiwi.com isn't available through
                  Travelpayouts' partner-link API at all, and this
                  project never fabricates a `promo_id` it has no
                  confirmed value for). ALSO always carries `sort=quality`
                  and `max_stopovers=1` (Kiwi's own real, documented
                  parameter vocabulary - best-effort, see that function's
                  own docstring for why this is NOT the same confidence
                  level as the `currency=eur` fix below).
  Unset provider: "aviasales" if TRAVELPAYOUTS_MARKER is set (the one
  provider that earns with just the marker), else "google".
  Unknown provider names fall back the same way.

HOTELS - `build_hotel_link(hotel_name, city, checkin_date, checkout_date, *,
generic=False, destination_iata=None)`: a Booking.com search with the
stay's dates and 2 adults (matching the room-for-2 prices in the
alerts); with BOOKING_AFFILIATE_ID the `aid` parameter is added.
HOTEL_LINK_PROVIDER=google switches to a plain Google Hotels search (no
dates, never tracked).

`generic=False` (the default): `hotel_name` is a REAL, specific property
name (e.g. Deal.accommodation.name, a genuine SerpApi-priced hotel) -
`ss` ("<hotel>, <city>") searches for exactly that property, as always.

`generic=True`: `hotel_name` is NOT a real property at all, only a
category placeholder this project itself invented (e.g. "4-Sterne
Hotel", "5★ Resort" - no actual SerpApi-priced accommodation exists for
a feed-radar/daily_scanner signal). Two possible outcomes (2026-10 -
"Nutzer will kein Suchportal, sondern genau DEN besten Deal sofort
buchen"):
  1. `destination_iata` has a curated reference hotel
     (monetization/curated_hotels.py): the deeplink goes DIRECTLY to
     that ONE property's Booking.com page - no search, no results list,
     no "8,390 Treffer" (the actual reported complaint - Nairobi's
     filtered search still put a private apartment ahead of any real
     hotel).
  2. Otherwise: `ss` is the CITY ONLY, never the placeholder text -
     REPORTED BUG, verified live 2026-10: Booking.com searches `ss` as a
     literal text match, so "4-Sterne Hotel, Bangkok" landed a user on a
     property literally NAMED "Hotel FREIGOLD 4-Sterne-Superior" in
     Freistadt, Austria, 8,000km from Bangkok. Booking's own mandatory
     quality filter (4-5 stars, guest score >= 8.0 - `nflt=class=4;
     class=5;review_score=80`) and its review-score-and-price sort are
     always added - never a bare, unfiltered city list either.

DEAL SHEET - `build_deal_sheet_url(...)`: the URL of the in-app deal page
(web/deal.html, served by GitHub Pages) that the VIP alert's single "Deal
sichern" button opens as a Telegram Mini App. Every deal fact travels in
the QUERY STRING (not the hash: Telegram adds its own #tgWebAppData to a
Mini App URL). Base URL: env DEAL_SHEET_URL, default DEFAULT_DEAL_SHEET_URL
(this repo's Pages site); DEAL_SHEET_URL=off (also none/0/false)
disables the sheet, and the alert falls back to plain URL buttons.

`windows`: an optional list of extra example date windows, each a dict
with "dep"/"ret" (ISO dates), "tp" (combo total, EUR), and its own real,
already-dated "fl"/"hl" links - JSON-encoded into a single "windows" query
parameter that deal.html renders as a date-selection matrix. If adding it
would push the URL past _MAX_DEAL_SHEET_URL_LENGTH (several dated,
markered links repeated per window add up), it is dropped and the sheet
is rebuilt without it - a working single-date sheet beats a button
Telegram or a browser might reject for being too long.

Populated ONLY by engine/daily_scanner.py's own signals (via
alerts/instant_alert_formatter.signal_deal_sheet_url ->
DealSignal.matrix_windows) - every entry there already came from its own
genuine, live SerpApi search, so this is safe exactly where the
feed-signal "Urlaubspiraten model" flexible-date combo teaser that USED to
populate this (see git history around alerts/instant_alert_formatter.py's
removed _signal_combo_estimate) was not: that one fabricated the dates it
searched, causing real price mismatches on click-through. No feed-radar
signal ever populates this.

`source_link`: the feed's own real article/thread URL (DealSignal.link),
JSON-free, plain "sl" query parameter - deal.html renders it as a dezent,
small "Deal-Details der Quelle ansehen" TEXT link, never a button (the
Telegram channel itself never gets this at all any more -
alerts/instant_alert_formatter.signal_keyboards is a single, own-deal-
sheet-only button, full stop - see that function's own docstring). None
for a Deal with no such concept (the sampler's own SerpApi-verified
Deals) or a feed signal whose source has none to show (see
alerts/instant_alert_formatter.py's _original_deal_link for which
signals qualify).

All text goes through urllib's UTF-8 percent-encoding, so umlauts, "&", "#"
and spaces in hotel/city names can't break or inject parameters.
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote, urlencode

# Importing config.py triggers its .env-loading side effect at import time -
# same pattern as monetization/affiliate.py.
import trip_hunter.config  # noqa: F401
from trip_hunter.monetization.curated_hotels import CuratedHotel, curated_hotel

BOOKING_AFFILIATE_ID_ENV = "BOOKING_AFFILIATE_ID"
TRAVELPAYOUTS_MARKER_ENV = "TRAVELPAYOUTS_MARKER"
TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ENV = "TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ID"
TRAVELPAYOUTS_CAMPAIGN_ENV = "TRAVELPAYOUTS_CAMPAIGN_ID"
FLIGHT_LINK_PROVIDER_ENV = "FLIGHT_LINK_PROVIDER"
HOTEL_LINK_PROVIDER_ENV = "HOTEL_LINK_PROVIDER"
KIWI_AFFILIATE_ID_ENV = "KIWI_AFFILIATE_ID"

DEAL_SHEET_URL_ENV = "DEAL_SHEET_URL"
DEFAULT_DEAL_SHEET_URL = "https://trip-hunter.de/deal.html"
_DISABLED_VALUES = frozenset({"off", "none", "0", "false", "no", "disabled"})
# A practical safety margin under the ~4096-char limits several Telegram
# Bot API clients/servers assume for a button URL - see build_deal_sheet_url.
_MAX_DEAL_SHEET_URL_LENGTH = 3800

FLIGHT_PROVIDERS = ("google", "aviasales", "skyscanner", "kiwi")
_GUESTS = 2


def _env(name: str) -> str | None:
    """The variable's stripped value; unset, empty or whitespace = None."""
    return (os.environ.get(name) or "").strip() or None


# --- flights --------------------------------------------------------------------


def build_flight_link(
    origin: str,
    destination: str,
    departure_date: date | None = None,
    return_date: date | None = None,
    *,
    provider: str | None = None,
) -> str:
    """Search link for one flight connection (`return_date=None` = one
    way; `departure_date=None` = no exact date known at all - a real,
    monetized ROUTE search for `origin` -> `destination` either way,
    never a blank, routeless provider homepage - see the module
    docstring's "NO EXACT DATE" section). See the module docstring for
    the provider rules."""
    marker = _env(TRAVELPAYOUTS_MARKER_ENV)
    chosen = (provider or _env(FLIGHT_LINK_PROVIDER_ENV) or "").lower()
    if chosen not in FLIGHT_PROVIDERS:
        chosen = "aviasales" if marker else "google"

    if chosen == "aviasales":
        return _aviasales_link(origin, destination, departure_date, return_date, marker)
    if chosen == "skyscanner":
        return _skyscanner_link(origin, destination, departure_date, return_date, marker)
    if chosen == "kiwi":
        return build_kiwi_flight_link(origin, destination, departure_date, return_date)
    return _google_flights_link(origin, destination, departure_date, return_date)


def _google_flights_link(origin: str, destination: str, departure: date | None, return_: date | None) -> str:
    if departure is None:
        query = f"Flights from {origin} to {destination}"
    else:
        query = f"Flights from {origin} to {destination} on {departure.isoformat()}"
        if return_ is not None:
            query += f" through {return_.isoformat()}"
    return "https://www.google.com/travel/flights?" + urlencode({"q": query, "hl": "de", "curr": "EUR"}, quote_via=quote)


def _aviasales_link(
    origin: str, destination: str, departure: date | None, return_: date | None, marker: str | None
) -> str:
    # ALWAYS pinned to EUR (verified live, 2026-10: reported bug - without
    # this, Aviasales opened in USD, e.g. "$589" for a German-speaking
    # DACH user) - matches _google_flights_link's own existing "curr":
    # "EUR", which this provider never had.
    #
    # NO sort/stops/layover-quality parameter is added here (2026-10,
    # reported complaint: the default result can be a 33h itinerary with
    # a 13h Beijing layover) - RESEARCHED, NOT FOUND, NOT FABRICATED.
    # Aviasales' own search-results PAGE URL (this function's output) is
    # a terse, positional "{origin}{DDMM}{dest}{DDMM}{pax}" path with no
    # room for a filter in it at all; the "sorting"/"direct"/"transfers"
    # parameters that DO exist in Travelpayouts' documentation belong to
    # a SEPARATE Data API (a JSON feed for querying flight prices in
    # bulk, not the human results page this link sends a user to) -
    # Aviasales' own FAQ describes the "cheapest vs. convenient" ranking
    # as a tab the results PAGE itself computes client-side, with no
    # documented way to pre-select it via URL. Adding an invented
    # parameter here would be a placebo - indistinguishable from doing
    # nothing, except it would look fixed without being fixed. See
    # build_kiwi_flight_link's own `sort`/`max_stopovers` for where a
    # confirmed, real parameter vocabulary DOES exist for the fallback
    # provider instead.
    if departure is not None:
        # Aviasales search RESULTS page: ORIGIN DDMM DEST [DDMM] + passenger
        # count - a real exact-date search (verified live, 2026-10).
        path = f"{_code(origin)}{departure:%d%m}{_code(destination)}"
        if return_ is not None:
            path += f"{return_:%d%m}"
        url = f"https://www.aviasales.com/search/{path}1"
        params = {"currency": "eur"}
        if marker:
            params["marker"] = marker
        return url + "?" + urlencode(params, quote_via=quote)
    # No exact date at all: Aviasales' own PRE-FILLED SEARCH FORM instead
    # (route only, no date segment - confirmed via Travelpayouts' own
    # support docs, e.g. ".../?params=PARTNG1" for a dateless Paris-
    # Tangier search) - the user lands with the route already filled in
    # and picks their own dates there, never a blank, routeless Google
    # Flights homepage (the actual reported bug this replaces).
    params = {"params": f"{_code(origin)}{_code(destination)}1", "currency": "eur"}
    if marker:
        params["marker"] = marker
    return "https://www.aviasales.com/?" + urlencode(params, quote_via=quote)


def _skyscanner_link(
    origin: str, destination: str, departure: date | None, return_: date | None, marker: str | None
) -> str:
    path = f"{_code(origin).lower()}/{_code(destination).lower()}/"
    if departure is not None:
        path += f"{departure:%y%m%d}/"
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


def build_kiwi_flight_link(
    origin_iata: str,
    destination_iata: str,
    departure_date: date | None = None,
    return_date: date | None = None,
    affiliate_id: str | None = None,
) -> str:
    """Kiwi.com deep-link search - an alternative flight provider to
    Aviasales (FLIGHT_LINK_PROVIDER=kiwi, or pass provider="kiwi" to
    build_flight_link directly).

    REMOVED (verified live, 2026-10): this used to also wrap the link in
    Travelpayouts' generic "customlink" click-redirect
    (c111.travelpayouts.com/click) under TRAVELPAYOUTS_MARKER whenever one
    was set, on the theory that Kiwi.com is bookable through the same
    Travelpayouts account as Aviasales. A real click on that redirect
    failed with "promo_id is empty" - Travelpayouts' own docs confirm
    that endpoint requires a `promo_id` specific to a confirmed program/
    campaign (Kiwi.com is in fact explicitly NOT available through
    Travelpayouts' partner-link API at all), which this project has never
    had a verified value for and will not fabricate one for. Kiwi.com
    links are therefore always the plain, direct link below - tracked
    ONLY via `affiliate_id`/KIWI_AFFILIATE_ID (Kiwi's own, separate
    `affilid` parameter, a real, direct Kiwi partnership credential, not
    a Travelpayouts one) - never via TRAVELPAYOUTS_MARKER, which simply
    does not apply to Kiwi.com in this project any more. A route with a
    real, working flight search is strictly better than a broken,
    "monetized-looking" one that errors out instead of earning anything.

    `departure_date`/`return_date` are optional - a date-less search
    rather than a fabricated one when neither is known.

    `sort=quality`/`max_stopovers=1` (2026-10, reported bug - a default
    "cheapest, no matter how bad" result showed a 33-hour itinerary with
    a 13-hour Beijing layover): Kiwi's own confirmed Search-API/NOMAD-API
    parameter vocabulary (docs.kiwi.com - `sort` accepts "price",
    "duration", "quality" or "date"; `max_stopovers` caps the number of
    connections, "0" meaning nonstop), added to this SAME `/deep`
    redirect that already forwards other recognised parameters
    (`affilid`, `currency`, `lang`, ...) into Kiwi's own booking flow.
    BEST-EFFORT, not independently confirmed LIVE to change what the
    redirect's landing page actually shows (unlike `currency=eur` on
    Aviasales, which WAS confirmed live in an earlier task) - an
    unrecognised query parameter on a redirect is always a safe no-op,
    never a broken link, so this is added on the strength of it being
    Kiwi's own real, documented vocabulary rather than an invented name."""
    resolved_affiliate_id = affiliate_id if affiliate_id is not None else _env(KIWI_AFFILIATE_ID_ENV)
    params = {
        "from": _code(origin_iata), "to": _code(destination_iata), "sort": "quality", "max_stopovers": "1",
    }
    if departure_date is not None:
        params["departure"] = departure_date.isoformat()
    if return_date is not None:
        params["return"] = return_date.isoformat()
    if resolved_affiliate_id:
        params["affilid"] = resolved_affiliate_id
    return "https://www.kiwi.com/deep?" + urlencode(params, quote_via=quote)


# --- hotels ---------------------------------------------------------------------


def build_hotel_link(
    hotel_name: str,
    city: str,
    checkin_date: date | None = None,
    checkout_date: date | None = None,
    *,
    provider: str | None = None,
    generic: bool = False,
    destination_iata: str | None = None,
) -> str:
    """Search link for one hotel stay - Booking.com (with `aid` if
    BOOKING_AFFILIATE_ID is set) or a plain Google Hotels search.

    `checkin_date`/`checkout_date` are optional ONLY for `provider=
    "google"` (a Google Hotels search names no dates); every other
    provider needs real dates - the caller must have them, since this
    project never fabricates a stay's dates.

    `generic`: see the module docstring's own "HOTELS" section -
    `generic=True` (only ever for a category PLACEHOLDER this project
    invented itself, never a real property name) means the city search,
    not `hotel_name`, is the fallback - REPORTED BUG, verified live
    2026-10: Booking.com matched a generic "4-Sterne Hotel, Bangkok"
    search against a real property literally named "Hotel FREIGOLD
    4-Sterne-Superior" in Freistadt, Austria.

    `generic=True` AND a curated reference hotel exists for
    `destination_iata` (monetization/curated_hotels.py, `provider`
    unset or "booking" only): the deeplink goes DIRECTLY to that one
    property's own Booking.com page (with dates/`aid` if known) - never
    a search at all any more, the actual fix for the "endless Nairobi
    search list" complaint this feature addresses (2026-10). Otherwise
    (`generic=True`, no curated hotel): the CITY ONLY, with Booking's own
    mandatory quality filter (4-5 stars, guest score >= 8.0) and its
    review-score-and-price sort - never a bare, unfiltered city list."""
    chosen = (provider or _env(HOTEL_LINK_PROVIDER_ENV) or "booking").lower()

    if generic and chosen == "booking":
        curated = curated_hotel(destination_iata)
        if curated is not None:
            return _curated_booking_url(curated, checkin_date, checkout_date)

    place = city.strip() if generic else ", ".join(part.strip() for part in (hotel_name, city) if part and part.strip())

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
    if generic:
        # Booking's own mandatory quality filter ("4-5 Sterne & Bewertung
        # ab 8.0" as a FILTER, never as search TEXT any more) plus its
        # review-score-and-price sort - matches this project's own "keine
        # Dorms, nur top-bewertete Unterkünfte" quality bar, never a bare
        # cheapest-first list.
        params["nflt"] = "class=4;class=5;review_score=80"
        params["order"] = "review_score_and_price"
    aid = _env(BOOKING_AFFILIATE_ID_ENV)
    if aid:
        params["aid"] = aid
    return "https://www.booking.com/searchresults.de.html?" + urlencode(params, quote_via=quote)


def _curated_booking_url(hotel: CuratedHotel, checkin_date: date | None, checkout_date: date | None) -> str:
    """Booking.com's own property-page URL for a curated reference hotel
    (monetization/curated_hotels.py) - never a search, so the user lands
    directly on this ONE hotel's room selection. Dates are included when
    known (never fabricated if not - the dateless case still works,
    Booking's own property page just shows its availability calendar)."""
    params = {"group_adults": _GUESTS, "no_rooms": 1, "group_children": 0}
    if checkin_date is not None:
        params["checkin"] = checkin_date.isoformat()
    if checkout_date is not None:
        params["checkout"] = checkout_date.isoformat()
    aid = _env(BOOKING_AFFILIATE_ID_ENV)
    if aid:
        params["aid"] = aid
    return f"https://www.booking.com/{hotel.booking_hotel_path}?" + urlencode(params, quote_via=quote)


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
    windows: list[dict[str, object]] | None = None,
    source_link: str | None = None,
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

    def build(*, include_windows: bool) -> str:
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
            "sl": source_link,
            "img": image_url,
            "windows": (
                json.dumps(windows, separators=(",", ":"), ensure_ascii=False)
                if windows and include_windows
                else None
            ),
        }
        query = urlencode({k: v for k, v in params.items() if v not in (None, "")}, quote_via=quote)
        return f"{base}{'&' if '?' in base else '?'}{query}"

    url = build(include_windows=True)
    if windows and len(url) > _MAX_DEAL_SHEET_URL_LENGTH:
        url = build(include_windows=False)
    return url


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


