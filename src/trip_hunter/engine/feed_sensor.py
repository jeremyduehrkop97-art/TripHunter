"""Free early-warning sensor: scans public deal-blog RSS feeds
(Travel-Dealz, Secret Flying) for flight deals departing from DACH
(Germany, Austria, Switzerland)
airports and reports them as standardized `DealSignal` events.

Phase 1 is deliberately DECOUPLED from the rest of the pipeline: the
sensor only observes and reports. It never scores, alerts or spends
SerpApi credits - a later phase hands its signals to the sampler to
trigger targeted verification scans (origin + destination_iata), and only
what a real price scan confirms becomes a Deal. A signal is a hint from a
third party, never a verified price.

Everything is heuristic text matching on free-form deal titles, so the
parser is conservative and honest: a field that can't be determined is
None/empty, never guessed. In particular `destination_iata` is only set
for destinations in the explicit _CITY_TO_IATA allowlist (same pattern as
error_fare_floor.MID_HAUL_DESTINATIONS) or when the title names a bare
IATA code; `travel_dates` is the raw date text found ("12.10.–19.10.",
"Oktober 2026"), not a parsed range.

Pure parsing (`parse_feed`) is separate from network access
(`fetch_feed` / `scan_feeds`), which never raises: a feed that is down,
blocked (Secret Flying currently sits behind a Cloudflare challenge that
plain HTTP clients don't pass - we do not try to circumvent it) or
malformed is skipped with a printed reason, and the other sources are
still scanned.

Sources (FEED_SOURCES): Travel-Dealz, Urlaubspiraten, FlyerTalk, Fly4free,
mydealz (travel group), Secret Flying and Flynous. Secret Flying and
Flynous sit behind Cloudflare/WAF blocks and yield nothing without a
mirror (env TRIP_HUNTER_FEED_MIRROR_<NAME>; RSS 2.0 and Atom are both
read); no public mirror was reachable when checked (rsshub.app 403,
public RSS-Bridge instance 404). Everything is fail-safe: a dead, blocked,
malformed or crashing source or item is skipped, never fatal.

FlyerTalk ("Mileage Run Deals", forum 372) is the primary error-fare
source: an open vBulletin RSS 2.0 feed (no Cloudflare challenge; verified
reachable, though it currently returns zero items even though the forum's
HTML page lists threads). Extra query parameters (days=30, count=20,
limit=20, lastpost=true) were tried against the live feed and change
nothing - the RSS window is a server-side vBulletin setting - so none is
added to the URL; a feed that stays empty is simply not a source of
signals.
Its thread titles name routes as codes - "LH: FRA-JFK 280 EUR",
"BA/AA: DUS-MIA €320 rt", "HAM-LIS from 35€" - so a departure is also
recognised from an "ORIGIN-DEST" / "ORIGIN - DEST" / "ORIGIN/DEST" /
"ORIGIN→DEST" pair whose left code is a DACH airport (JFK-FRA, i.e. an
inbound flight, is not a departure), and that pair also gives the
destination IATA. Only EUR prices are read; a "$300" or "£250" is
ignored rather than converted, so it can never trigger the price rule.

Tier-1 detection mirrors engine/alert_tier.py's idea of an error fare:
explicit keywords ("Mistake", "Error", "Drop", "Preisfehler", "extrem
günstig" ...) or a flight price <= TIER_1_MAX_PRICE (short-haul) /
TIER_1_MAX_PRICE_LONG_HAUL (destination in the explicit _LONG_HAUL
allowlist; an unknown destination counts as short-haul, so it never
triggers on the generous long-haul bar by accident). Feed XML is untrusted, so documents
with a DOCTYPE/ENTITY declaration are rejected (XML entity-expansion
attacks) and size is capped.

HOTEL-FIRST SIGNALS (DealSignal.deal_lead="hotel"): a feed deal can also
be led by the HOTEL, not the flight - "5* Luxusresort auf Bali ab
45€/Nacht" names no flight at all, only a heavily discounted stay, yet is
exactly the kind of deal this project wants to monetize as a "Hotel-Drop
inkl. Flug" package (alerts/instant_alert_formatter.py's
_signal_hotel_combo_estimate adds a flight-price GUIDE from
monetization/flight_price_guide.py on top of the feed's own real nightly
rate, the mirror image of the flight-first flexible-date combo teaser's
hotel guide price). Detected by _is_hotel_lead_title (a star rating or
hotel/resort/overnight-stay word - never a discount percentage alone,
which also appears in ordinary flight-promo titles this module already
rejects elsewhere) and, unlike every other signal here, never requires a
real DACH departure airport to be named (_build_signal) - a hotel offer
is origin-agnostic by nature, so one usually isn't. Its price is read
ONLY from an explicit ".../Nacht" marker (_extract_hotel_nightly_price) -
an unmarked price is never assumed to be the nightly rate, since it could
just as easily be a flat package total. feed_radar.is_hotel_deal_worthy,
not an absolute price cap, is this project's bargain threshold for these
(a hotel's nightly rate has no destination-independent ceiling that means
anything the way a flight price does) - the feed's own self-reported
discount percentage (_extract_discount_percent) has to clear a minimum.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
import os
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

import requests

from trip_hunter.engine.error_fare_floor import LONG_HAUL_DESTINATIONS

FEED_SOURCES: dict[str, str] = {
    "travel-dealz": "https://travel-dealz.de/feed/",
    "secretflying": "https://www.secretflying.com/feed/",
    "flyertalk": "https://www.flyertalk.com/forum/external.php?type=rss2&forumids=372",
    "urlaubspiraten": "https://www.urlaubspiraten.de/feed",
    # Fly4free's main feed is fresh and open; its /flight-deals/europe/
    # feed is sorted so old items lead, hence not used.
    "fly4free": "https://www.fly4free.com/feed/",
    # Flynous answers every non-browser request with a WAF block ("Your
    # request was blocked", HTTP 403 - also with other User-Agents), so it
    # is registered but currently yields nothing; a self-hosted mirror via
    # TRIP_HUNTER_FEED_MIRROR_FLYNOUS would make it live.
    "flynous": "https://www.flynous.com/feed",
    # mydealz' travel group: an open, fresh RSS 2.0 feed of German community
    # deals (flights "von Frankfurt", packages, hotels) - only items with a
    # DACH departure airport survive the filters.
    "mydealz": "https://www.mydealz.de/rss/gruppe/reisen",
}

# Fallback URLs tried (in order) after a source's own URL fails or isn't
# valid XML. Secret Flying's own feed sits behind a Cloudflare challenge we
# don't try to pass; its official FeedBurner feed IS open but was last
# updated in July 2025, so it only helps if Secret Flying revives it - the
# freshness filter (DEFAULT_MAX_SIGNAL_AGE) keeps its old items from
# posing as fresh signals. A self-hosted mirror (e.g. your own RSSHub) can
# be added without a code change: TRIP_HUNTER_FEED_MIRROR_<NAME>, e.g.
# TRIP_HUNTER_FEED_MIRROR_SECRETFLYING, is tried FIRST. (rsshub.app itself
# returns 403 to non-approved clients and asks not to be used in
# production; the "secretflying" Telegram channel has no public web
# preview, so a Telegram-based RSSHub route has nothing to read.)
FEED_MIRRORS: dict[str, tuple[str, ...]] = {
    "secretflying": ("https://feeds.feedburner.com/SecretFlying",),
}

# Older than this = the deal is most likely gone; an error fare lives hours.
DEFAULT_MAX_SIGNAL_AGE = timedelta(days=3)

# Urlaubspiraten mixes hotels, packages and cruises into one feed; only
# /fluege/ items are flight deals. Their titles ("Günstige Flüge nach
# Chiang Mai") carry neither origin nor price - those sit in the German
# description ("Los gehts ab Frankfurt", "ab nur 495 €"), so origin and
# price are read from title + description for these sources. Tier-1
# KEYWORDS still only look at the title (prose could say "kein
# Preisfehler"). "Ab vielen Flughäfen" names no airport, so such an item
# has no DACH origin and is dropped rather than guessed.
_LINK_MUST_CONTAIN: dict[str, str] = {"urlaubspiraten": "/fluege/"}
_DESCRIPTION_SOURCES = frozenset({"urlaubspiraten"})

TIER_1_MAX_PRICE = 40.0  # short-haul (Europe)
TIER_1_MAX_PRICE_LONG_HAUL = 250.0  # intercontinental
_MAX_FEED_BYTES = 2_000_000
_DEFAULT_TIMEOUT_SECONDS = 10.0
_USER_AGENT = "Mozilla/5.0 (compatible; TripHunterFeedSensor/0.1)"

# IATA code -> names it appears under in German/English deal titles.
# Deliberately DACH (Germany, Austria, Switzerland), not Germany alone - a
# reader in Vienna or Zurich reads the same feeds. MLH/EAP are the French/
# neutral IATA/designator variants some feeds use for the EuroAirport
# Basel-Mulhouse-Freiburg; they resolve to the same canonical code, BSL.
DACH_ORIGINS: dict[str, tuple[str, ...]] = {
    # Germany
    "HAM": ("Hamburg",),
    "BER": ("Berlin",),
    "FRA": ("Frankfurt",),
    "MUC": ("München", "Muenchen", "Munich"),
    "DUS": ("Düsseldorf", "Duesseldorf", "Dusseldorf"),
    # Austria
    "VIE": ("Wien", "Vienna"),
    "SZG": ("Salzburg",),
    "INN": ("Innsbruck",),
    # Switzerland
    "ZRH": ("Zürich", "Zuerich", "Zurich"),
    "GVA": ("Genf", "Geneva", "Genève"),
    "BSL": ("Basel", "Mulhouse", "EuroAirport", "Basel-Mulhouse-Freiburg", "MLH", "EAP"),
}

# Alternate IATA/designator codes that funnel into a canonical DACH_ORIGINS
# key when they appear as a bare 3-letter code (e.g. a FlyerTalk "MLH-LIS"
# chain) rather than by name - MLH (Mulhouse) / EAP (EuroAirport) mean the
# same physical airport as BSL (Basel).
_ORIGIN_CODE_ALIASES: dict[str, str] = {"MLH": "BSL", "EAP": "BSL"}


def _canonical_origin(code: str) -> str:
    return _ORIGIN_CODE_ALIASES.get(code, code)

# Explicit allowlist, never inferred - extend when a destination matters.
_CITY_TO_IATA: dict[str, str] = {
    "palma": "PMI", "mallorca": "PMI", "barcelona": "BCN", "rom": "FCO", "rome": "FCO",
    "lissabon": "LIS", "lisbon": "LIS", "porto": "OPO", "madrid": "MAD", "malaga": "AGP",
    "málaga": "AGP", "sevilla": "SVQ", "valencia": "VLC", "ibiza": "IBZ", "faro": "FAO",
    "paris": "CDG", "london": "LON", "amsterdam": "AMS", "wien": "VIE", "vienna": "VIE", "zürich": "ZRH", "zuerich": "ZRH", "zurich": "ZRH",
    "genf": "GVA", "geneva": "GVA", "genève": "GVA", "salzburg": "SZG", "innsbruck": "INN", "basel": "BSL",
    "mailand": "MXP", "milan": "MXP", "chiang mai": "CNX", "taipeh": "TPE", "taipei": "TPE", "calgary": "YYC", "karibik": "PUJ", "tokyo": "TYO", "tokio": "TYO", "seoul": "SEL", "los angeles": "LAX", "san francisco": "SFO", "miami": "MIA", "chicago": "CHI", "boston": "BOS", "toronto": "YYZ", "mexico city": "MEX", "cancun": "CUN", "bali": "DPS", "denpasar": "DPS", "singapore": "SIN", "singapur": "SIN", "hong kong": "HKG", "delhi": "DEL", "mumbai": "BOM", "sydney": "SYD", "cape town": "CPT", "kapstadt": "CPT", "punta cana": "PUJ", "havana": "HAV", "malediven": "MLE", "maldives": "MLE", "seychellen": "SEZ", "seychelles": "SEZ", "faroe islands": "FAE", "färöer-inseln": "FAE", "färöer": "FAE", "phuket": "HKT", "krabi": "KBV", "bischkek": "FRU", "bergamo": "BGY", "venedig": "VCE", "venice": "VCE",
    "stansted": "STN", "nizza": "NCE", "nice": "NCE", "dublin": "DUB",
    "kopenhagen": "CPH", "copenhagen": "CPH", "prag": "PRG", "prague": "PRG",
    "budapest": "BUD", "athen": "ATH", "athens": "ATH", "kreta": "HER", "crete": "HER",
    "rhodos": "RHO", "rhodes": "RHO", "istanbul": "IST", "antalya": "AYT",
    "teneriffa": "TFS", "tenerife": "TFS", "gran canaria": "LPA", "fuerteventura": "FUE",
    "lanzarote": "ACE", "kairo": "CAI", "cairo": "CAI", "marrakesch": "RAK",
    "marrakech": "RAK", "dubai": "DXB", "bangkok": "BKK", "new york": "JFK",
}

# (label, pattern) - word-bounded so "Drop" doesn't fire inside "Dropbox".
_TIER_1_KEYWORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(rf"(?<!\w){pattern}(?!\w)", re.IGNORECASE))
    for label, pattern in (
        ("mistake", r"mistakes?"),
        ("error", r"errors?"),
        ("drop", r"drop(?:s|ped)?"),
        ("preisfehler", r"preisfehler"),
        ("fehlerpreis", r"fehlerpreis"),
        ("extrem günstig", r"extrem\s+g(?:ü|ue)nstig"),
    )
)

_LONG_HAUL = LONG_HAUL_DESTINATIONS

# Deal categories that are never a flight from a DACH airport.
_NON_FLIGHT_MARKERS = ("kreuzfahrt", "cruise", "gutschein", "interrail", "nachtzug", "bahnticket")

# Headline words that can lead a title without naming a place. Includes
# cabin-class and fare-jargon terms (the reported bug: "Frankfurt nach
# Business Class" read a TARIFF as the destination) - a real destination
# candidate is never just "Business Class"/"OW"/"RT" on its own once
# cleaned, so these are always safe exact-match rejections.
_NOT_A_DESTINATION = frozenset(
    {
        "error fare", "mistake fare", "error", "mistake", "drop", "preisfehler", "fehlerpreis",
        "flug", "flüge", "flights", "flight", "deal", "angebot", "achtung", "wow",
        "business class", "first class", "premium economy", "economy class", "business", "first",
        "star alliance", "skyteam", "oneworld", "gabelflug", "stopover", "roundtrip", "ow", "rt",
    }
)

# Promo/campaign noise that must never be treated as a destination, even
# as PART of a longer candidate string (unlike _NOT_A_DESTINATION above,
# which only rejects an exact match) - "Ryanair Blitzverkauf Flüge" is not
# a place just because nothing else matched. Deliberately broad: catching
# too many "not a real destination" cases is safe (the signal is simply
# dropped, per this module's "never post an incomplete alert" rule),
# guessing a fake one is not.
_PROMO_DESTINATION_RE = re.compile(
    r"(?<!\w)(?:"
    r"blitzverkauf|flash\s*sale|super\s*sale|sale|gutschein|rabattaktion|rabatt(?:e)?|"
    r"aktion|coupon|voucher|promo(?:tion)?|"
    r"flug|fl[üu]ge|flights?|flight"
    r")(?!\w)",
    re.IGNORECASE,
)

# Destinations that are never a legitimate flight DEAL from a DACH
# departure airport - short DACH-neighbor hops with no meaningful nonstop
# flight-deal market at all. A real deal blog never markets "Frankfurt ->
# Stuttgart" as a bargain flight; the reported "Düsseldorf nach Brüssel
# für 19 €" traced back to a mis-parsed train+flight combo fare (the real
# trip was a 178 € connection via Amsterdam) rather than an actual DUS-BRU
# flight deal. Deliberately a narrow, explicit, reviewable list of both
# names and codes (checked case-insensitively) - not a computed distance,
# since this project has no airport-coordinate data to back one, and a
# wrong distance guess is exactly the kind of fabricated fact this module
# never allows. Extend it by name the next time a phantom short-hop shows
# up; a route split-second-guessing real geography is not the goal here.
_IMPLAUSIBLE_DACH_NEIGHBOR_DESTINATIONS = frozenset(
    {
        "brüssel", "bruessel", "brussels", "bru",
        "stuttgart", "str",
        "nürnberg", "nuernberg", "nuremberg", "nue", "nur",
        "köln", "koeln", "cologne", "cgn",
        "dortmund", "dtm",
    }
)


def _is_implausible_short_hop(destination: str) -> bool:
    return destination.strip().lower() in _IMPLAUSIBLE_DACH_NEIGHBOR_DESTINATIONS

_TITLE_ORIGIN_KEYWORD = r"(?:\bab|\bvon|\bfrom|\baus)"
_CONNECTOR = r"(?:,|/|&|\bund\b|\boder\b|\band\b|\bor\b)"
# "<keyword> [Name <connector>]* " right before an airport name.
_ORIGIN_LEAD_RE = re.compile(
    rf"{_TITLE_ORIGIN_KEYWORD}\s+(?:[^\s,/&]+(?:\s+[^\s,/&]+)?\s*{_CONNECTOR}\s*)*$",
    re.IGNORECASE,
)
# "FRA-JFK", "DUS - MIA", "HAM/LIS", "FRA→JFK" (FlyerTalk thread titles).
_ROUTE_CHAIN_RE = re.compile(r"\b[A-Z]{3}(?:\s*(?:->|→|–|—|-|/)\s*[A-Z]{3})+\b")
_CHAIN_SPLIT_RE = re.compile(r"\s*(?:->|→|–|—|-)\s*")
# Three-letter words that follow a code pair in titles but aren't airports.
_NOT_AN_AIRPORT = frozenset({"USA", "EUR", "USD", "GBP", "CAD", "AUD", "THE", "AND", "ALL"})
_ROUTE_SPLIT_RE = re.compile(r"\s*(?:→|->|➔|➜|\bto\b|\bnach\b)\s*", re.IGNORECASE)
_DEST_STOP_RE = re.compile(r"\s+(?:for|für|ab|from|von|mit|with)\b|[:(\[€]|\s[–-]\s|\d", re.IGNORECASE)

# Marketing filler that sometimes leads a destination candidate - stripped
# repeatedly (front to back) so a stacked "Cheap Holiday in X" reduces to
# "X". Genuinely never needed for "flights/flüge to/nach X" phrasing
# (that "to"/"nach" already IS _ROUTE_SPLIT_RE's own separator, so the
# route-split branch below never even sees the filler word) - only for
# titles reaching the destination-leads fallback branch ("<dest> ab ...").
_MARKETING_PREFIX_RE = re.compile(
    r"^(?:"
    r"holiday\s+in|urlaub\s+in|"
    r"cheap\s+(?:non-?stop\s+)?flights?|g[üu]nstige\s+fl[üu]ge|non-?stop\s+flights?|"
    r"flights?|fl[üu]ge?|flug"
    r")\b\s*",
    re.IGNORECASE,
)

# A leading English article ("the Maldives", "a cheap flight" - the latter
# is already eaten by _MARKETING_PREFIX_RE first) is never part of a place
# name, so it's stripped the same way a marketing phrase is - "the "/"a "/
# "an " only, LEADING only ("An Nam" is never a hit since "an" there isn't
# followed by a space-then-nothing-relevant... it still is a false
# positive risk in theory, but no feed source this project reads names a
# real destination starting with an English article).
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|an?)\s+", re.IGNORECASE)

# German text for a feed destination that names a multi-airport REGION,
# not one specific airport - deliberately never given an IATA code (that
# would mean guessing which of several real airports the feed meant,
# exactly what this module's "never guess" rule forbids). Applied only
# after cleaning, so "the Canary Islands" and "Canary Islands" both hit
# it. A region with effectively one real airport (Faroe Islands -> FAE)
# is instead added straight to _CITY_TO_IATA above - it's a real code, not
# a guess.
_DESTINATION_TRANSLATIONS: dict[str, str] = {
    "canary islands": "Kanarische Inseln",
    "azores": "Azoren",
}


def _clean_destination_text(raw: str) -> str:
    """Turn a raw destination candidate into display-ready text: cut at
    the first price/currency/"ab"/"für"/... marker (same cut point
    _DEST_STOP_RE already used only on the route-split branch, now shared
    by every branch), drop emoji/flags, strip a leading marketing phrase
    or English article (possibly several, stacked - "the Holiday in X"
    reduces just like "Holiday in the X" would), translate a known
    English region name to German (_DESTINATION_TRANSLATIONS), and -
    matching this project's own convention for compound place names (see
    airport_names.py's "Faro (Algarve)", "Kreta (Heraklion)") - turn a
    bare "City, Country" shape into "City (Country)"."""
    text = _DEST_STOP_RE.split(raw, maxsplit=1)[0]
    text = re.sub(r"[^\w\s,.'()/-]", "", text)  # drop emoji/flags
    text = re.sub(r"\s+", " ", text).strip(" ,-–")
    previous = None
    while previous != text:
        previous = text
        text = _MARKETING_PREFIX_RE.sub("", text).strip(" ,-–")
        text = _LEADING_ARTICLE_RE.sub("", text).strip(" ,-–")
    translated = _DESTINATION_TRANSLATIONS.get(text.lower())
    if translated:
        return translated
    match = re.fullmatch(r"([^,()]+),\s*([^,()]+)", text)
    if match:
        text = f"{match.group(1).strip()} ({match.group(2).strip()})"
    return text
_PRICE_RE = re.compile(
    r"(?:€\s?(?P<a>\d[\d.,]*))|(?:(?P<b>\d[\d.,]*)\s?(?:€|EUR\b|Euro\b))",
)
_RANGE_DATE_RE = re.compile(
    r"\b\d{1,2}\.\d{1,2}\.(?:\d{2,4})?\s*(?:–|-|bis)\s*\d{1,2}\.\d{1,2}\.(?:\d{2,4})?"
)
_MONTHS = (
    "Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember|"
    "January|February|March|May|June|July|October|December"
)
_MONTHS_SHORT = "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_MONTH_RE = re.compile(rf"\b(?:{_MONTHS}|{_MONTHS_SHORT})(?:\s+20\d\d)?\b")
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class DealSignal:
    """One deal spotted in a feed - an unverified hint, see module docstring.

    `deal_lead` ("flight" by default, or "hotel") says which half of the
    trip the FEED itself actually priced - see the module docstring's
    "HOTEL-FIRST SIGNALS" section. It changes what `price` even means:
    for "flight" it's the first flight/total price named in the title
    (as always); for "hotel" it's the hotel's own nightly rate in EUR
    (never a flight price, and never a hotel TOTAL either - only ever set
    from an explicit ".../Nacht" marker, so it's never ambiguous with one).
    `origins` may be empty for a "hotel" signal - a hotel offer is
    origin-agnostic by nature, so the feed title usually never names one
    at all (see alerts/instant_alert_formatter.py's
    DEFAULT_HOTEL_DEAL_ORIGIN for how that's handled honestly at display
    time, never stored here as if it were a detected fact).
    `hotel_discount_percent` is the feed's own self-reported discount
    (e.g. "-65%"), only ever read for a "hotel" signal - see
    feed_radar.is_hotel_deal_worthy for why this, not an absolute price,
    is this project's bargain threshold for a hotel-first deal.
    """

    source: str
    title: str
    link: str
    origins: tuple[str, ...]  # DACH IATA codes, e.g. ("MUC", "VIE") - may be empty for deal_lead="hotel"
    tier_1_reasons: tuple[str, ...]
    destination: str | None = None  # free text as written, e.g. "Taipeh"
    destination_iata: str | None = None
    price: float | None = None  # EUR - flight price, or hotel nightly rate if deal_lead="hotel"
    travel_dates: str | None = None  # raw text, e.g. "12.10.–19.10." / "Oktober 2026"
    published: datetime | None = None
    deal_lead: str = "flight"  # "flight" or "hotel"
    hotel_discount_percent: int | None = None  # only ever set for deal_lead="hotel"

    @property
    def is_tier_1(self) -> bool:
        return bool(self.tier_1_reasons)


def _load_root(xml_text: str, source: str) -> ET.Element | None:
    """The parsed document, or None (with a printed reason) if it is
    oversized, unsafe (DOCTYPE/ENTITY) or not valid XML."""
    if len(xml_text) > _MAX_FEED_BYTES or re.search(r"<!(?:DOCTYPE|ENTITY)", xml_text, re.IGNORECASE):
        print(f"Feed {source}: übersprungen (zu groß oder enthält DOCTYPE/ENTITY).")
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        print(f"Feed {source}: kein gültiges XML.")
        return None
    if root.tag not in ("rss", f"{_ATOM_NS}feed"):
        # e.g. a mirror's XHTML error page, which IS well-formed XML.
        print(f"Feed {source}: kein RSS-/Atom-Feed.")
        return None
    return root


def _signals_from_root(root: ET.Element, source: str, tier_1_only: bool) -> list[DealSignal]:
    signals: list[DealSignal] = []
    for item in [*root.iter("item"), *root.iter(f"{_ATOM_NS}entry")]:
        signal = _item_to_signal(item, source)
        if signal is not None and (signal.is_tier_1 or not tier_1_only):
            signals.append(signal)
    return signals


def parse_feed(xml_text: str, source: str, *, tier_1_only: bool = False) -> list[DealSignal]:
    """Parse one RSS 2.0 document into signals for deals departing from a
    DACH airport (`tier_1_only` keeps just the error-fare-like ones).
    Malformed or unsafe XML yields []. Pure - no network."""
    root = _load_root(xml_text, source)
    return [] if root is None else _signals_from_root(root, source, tier_1_only)


def fetch_feed(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    quiet: bool = False,
) -> str | None:
    """The feed body, or None on any failure (timeout, network error, bad
    status) - never raises. The reason is printed unless `quiet` (used for
    fallback mirrors, where scan_feeds prints one summary line instead)."""
    http = session or requests.Session()
    try:
        response = http.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout_seconds)
    except requests.exceptions.Timeout:
        if not quiet:
            print(f"Feed {url}: Timeout.")
        return None
    except requests.exceptions.RequestException as exc:
        if not quiet:
            print(f"Feed {url}: Netzwerkfehler ({type(exc).__name__}).")
        return None
    if response.status_code != 200:
        if not quiet:
            print(f"Feed {url}: HTTP {response.status_code}.")
        return None
    return response.text


def _mirror_env_var(name: str) -> str:
    return "TRIP_HUNTER_FEED_MIRROR_" + re.sub(r"[^A-Z0-9]", "", name.upper())


def _default_sources() -> dict[str, tuple[str, ...]]:
    """Every registered source with its URL chain: the env mirror (if
    configured) first, then the official URL, then FEED_MIRRORS."""
    sources: dict[str, tuple[str, ...]] = {}
    for name, url in FEED_SOURCES.items():
        configured = os.environ.get(_mirror_env_var(name), "").strip()
        sources[name] = (*((configured,) if configured else ()), url, *FEED_MIRRORS.get(name, ()))
    return sources


def scan_feeds(
    sources: dict[str, str | Sequence[str]] | None = None,
    *,
    tier_1_only: bool = False,
    max_age: timedelta | None = DEFAULT_MAX_SIGNAL_AGE,
    now: datetime | None = None,
    status: dict[str, str] | None = None,
    session: requests.Session | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[DealSignal]:
    """Fetch + parse every source; a failing source is skipped, never
    fatal. If `status` is given it is filled per source with a one-line
    outcome ("ok: 2 Abflüge aus DACH, 0 Tier 1" / "nicht erreichbar"), so the
    caller can show which feeds actually ran. A source may be one URL or a chain of fallback URLs: the first
    that answers with valid XML is used, later ones are not requested.
    Signals older than `max_age` (by their pubDate; `None` disables the
    filter, and an item without a date is kept) are dropped. Duplicates
    (same link without tracking params) are dropped; Tier-1 signals come
    first, then newest first."""
    resolved = sources if sources is not None else _default_sources()
    cutoff = None if max_age is None else (now or datetime.now(timezone.utc)) - max_age
    seen: set[str] = set()
    signals: list[DealSignal] = []
    for name, urls in resolved.items():
        try:
            fresh = _scan_source(name, urls, cutoff, status, session, timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - fail-safe: no source may ever crash the run
            print(f"Feed {name}: übersprungen ({type(exc).__name__}).")
            if status is not None:
                status[name] = "Fehler"
            continue
        if fresh is None:
            continue
        for signal in fresh:
            if tier_1_only and not signal.is_tier_1:
                continue
            key = _canonical_link(signal.link) or signal.title
            if key not in seen:
                seen.add(key)
                signals.append(signal)
    signals.sort(key=lambda s: (not s.is_tier_1, -(s.published.timestamp() if s.published else 0)))
    return signals


def _scan_source(
    name: str,
    urls: str | Sequence[str],
    cutoff: datetime | None,
    status: dict[str, str] | None,
    session: requests.Session | None,
    timeout_seconds: float,
) -> list[DealSignal] | None:
    """One source: first URL of its chain that answers with a valid feed;
    its fresh DACH departures, or None if no URL worked."""
    candidates = (urls,) if isinstance(urls, str) else tuple(urls)
    root = None
    for url in candidates:
        body = fetch_feed(url, session=session, timeout_seconds=timeout_seconds, quiet=len(candidates) > 1)
        if body is not None:
            root = _load_root(body, name)
        if root is not None:
            break
    if root is None:
        if len(candidates) > 1:
            print(f"Feed {name}: keine Quelle erreichbar ({len(candidates)} URLs) - übersprungen.")
        if status is not None:
            status[name] = "nicht erreichbar"
        return None
    fresh = [
        signal
        for signal in _signals_from_root(root, name, False)
        if cutoff is None or signal.published is None or _aware(signal.published) >= cutoff
    ]
    if status is not None:
        status[name] = f"ok: {len(fresh)} Abflüge aus DACH, {sum(s.is_tier_1 for s in fresh)} Tier 1"
    return fresh


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# --- parsing helpers -----------------------------------------------------------


_DESC_DESTINATION_RE = re.compile(
    r"Flüge?\s+nach\s+(?P<dest>[A-ZÄÖÜ][\wäöüß.-]*(?:\s+[A-ZÄÖÜ][\wäöüß.-]*)?)"
)


_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _text(element: ET.Element, *names: str) -> str | None:
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return found.text
    return None


def _normalise(item: ET.Element) -> tuple[str, str, str | None, str, list[str]]:
    """(title, link, date text, description, categories) of an RSS <item>
    or an Atom <entry> (RSS-Bridge's default format), tags cleaned."""
    if item.tag == f"{_ATOM_NS}entry":
        link_el = next(
            (l for l in item.findall(f"{_ATOM_NS}link") if l.get("rel") in (None, "alternate")), None
        )
        return (
            _clean(_text(item, f"{_ATOM_NS}title")),
            (link_el.get("href") or "").strip() if link_el is not None else "",
            _text(item, f"{_ATOM_NS}published", f"{_ATOM_NS}updated"),
            _clean(_text(item, f"{_ATOM_NS}summary", f"{_ATOM_NS}content")),
            [_clean(c.get("term")) for c in item.findall(f"{_ATOM_NS}category")],
        )
    return (
        _clean(item.findtext("title")),
        _clean(item.findtext("link")),
        item.findtext("pubDate"),
        _clean(item.findtext("description")),
        [_clean(c.text) for c in item.findall("category")],
    )


def _item_to_signal(item: ET.Element, source: str) -> DealSignal | None:
    try:
        return _build_signal(item, source)
    except Exception as exc:  # noqa: BLE001 - one odd item must never cost the whole feed
        print(f"Feed {source}: Eintrag übersprungen ({type(exc).__name__}).")
        return None


def _build_signal(item: ET.Element, source: str) -> DealSignal | None:
    title, link, date_text, description, category_list = _normalise(item)
    if not title:
        return None
    required = _LINK_MUST_CONTAIN.get(source)
    if required is not None and required not in link:
        return None
    categories = " ".join(category_list)
    if any(m in f"{title} {categories}".lower() for m in _NON_FLIGHT_MARKERS):
        return None

    text = f"{title} {description}" if source in _DESCRIPTION_SOURCES else title
    origins = find_dach_origins(text)
    is_hotel_candidate = _is_hotel_lead_title(title)
    hotel_nightly_price = _extract_hotel_nightly_price(title) if is_hotel_candidate else None
    # A hotel-lead title bypasses the DACH-origin requirement (see
    # DealSignal's own docstring) ONLY once it actually names a real
    # nightly price - a hotel/star-rating word alone, with neither a
    # price nor a departure airport, is exactly the kind of unrelated
    # noise (a city-break ad for a DACH city itself, a non-DACH source's
    # own-city hotel mention, ...) the origin gate existed to drop in the
    # first place, and still should.
    hotel_lead = is_hotel_candidate and hotel_nightly_price is not None
    if not origins and not hotel_lead:
        return None

    destination = _extract_destination(title, hotel_lead=hotel_lead)
    if destination is None and source in _DESCRIPTION_SOURCES:
        match = _DESC_DESTINATION_RE.search(description)
        destination = match.group("dest") if match else None
    destination_iata = _destination_iata(destination)

    if hotel_lead:
        price = hotel_nightly_price
        discount_percent = _extract_discount_percent(title)
        reasons: tuple[str, ...] = ()  # see DealSignal docstring - hotel signals are never Tier-1
    else:
        price = _extract_price(text)
        discount_percent = None
        reasons = _tier_1_reasons(title, price, destination_iata, category_list)

    return DealSignal(
        source=source,
        title=title,
        link=link,
        origins=origins,
        tier_1_reasons=reasons,
        destination=destination,
        destination_iata=destination_iata,
        price=price,
        travel_dates=_extract_travel_dates(title) or _extract_travel_dates(description),
        published=_parse_date(date_text),
        deal_lead="hotel" if hotel_lead else "flight",
        hotel_discount_percent=discount_percent,
    )


def find_dach_origins(title: str) -> tuple[str, ...]:
    """DACH IATA codes named in `title` as DEPARTURE airports: after
    "ab/von/from/aus" ("ab Hamburg", "von München und Frankfurt"), or on
    the left side of "→" / "to" / "nach". An airport named only as the
    destination ("Singapur → München") is not an origin."""
    route = _ROUTE_SPLIT_RE.split(title, maxsplit=1)
    left_end = len(route[0]) if len(route) == 2 else 0

    found: list[tuple[int, str]] = []  # (position in title, code)
    for departures, _, position in _routes(title):
        for raw_code in departures:
            code = _canonical_origin(raw_code)
            if code in DACH_ORIGINS and code not in (c for _, c in found):
                found.append((position, code))
    for code, names in DACH_ORIGINS.items():
        name_re = re.compile(rf"(?<![\w-])(?:{'|'.join(map(re.escape, names))})(?![\w-])", re.IGNORECASE)
        aliases = [alias for alias, canonical in _ORIGIN_CODE_ALIASES.items() if canonical == code]
        matches = [
            *name_re.finditer(title),
            *re.finditer(rf"\b{code}\b", title),
            *(m for alias in aliases for m in re.finditer(rf"\b{alias}\b", title)),
        ]
        for match in sorted(matches, key=lambda m: m.start()):
            if match.start() < left_end or _ORIGIN_LEAD_RE.search(title[: match.start()]):
                if code not in (c for _, c in found):
                    found.append((match.start(), code))
                break
    return tuple(code for _, code in sorted(found, key=lambda item: item[0]))


def _routes(title: str) -> list[tuple[list[str], str | None, int]]:
    """Every code chain in `title` as (departure codes, destination, position).

    "-" / "→" separate the legs, "/" lists alternatives within a leg:
    "MUC/FRA-JFK" departs MUC or FRA for JFK; "DUB-MIA/ORD" goes DUB to
    MIA or ORD (first named); "LGA-ZRH-FRA-JFK" departs LGA only - FRA is
    a connection, not a departure. A lone "HAM/LIS" (no dash) reads as the
    route HAM -> LIS. A chain whose destination is a non-airport word
    ("HAM-USA") is skipped.
    """
    routes = []
    for match in _ROUTE_CHAIN_RE.finditer(title):
        legs = [leg.split("/") for leg in _CHAIN_SPLIT_RE.split(match.group(0))]
        if len(legs) == 1 and len(legs[0]) == 2:
            legs = [[legs[0][0]], [legs[0][1]]]
        destination = legs[1][0] if len(legs) > 1 else None
        if destination in _NOT_AN_AIRPORT:
            continue
        routes.append((legs[0], destination, match.start()))
    return routes


def _extract_destination(title: str, *, hotel_lead: bool = False) -> str | None:
    # FlyerTalk style: the code pair of a DACH departure names the destination.
    for departures, destination, _ in _routes(title):
        if destination and any(_canonical_origin(code) in DACH_ORIGINS for code in departures):
            if _is_implausible_short_hop(destination):
                return None
            return destination

    # Travel-Dealz style: "<Destination>: <details> ab/von <Origin> ab
    # <Preis>" - the keyword before a leading colon IS the destination
    # when it's a place we actually recognise (never guessed - only via
    # _CITY_TO_IATA/a bare code, exactly like _destination_iata elsewhere).
    # A generic label ("Preisfehler:", "Extrem günstig:") never resolves,
    # so it falls through to the logic below unchanged, which finds the
    # destination after the colon instead.
    if ":" in title:
        prefix = _clean_destination_text(title.split(":", 1)[0])
        # Only the _CITY_TO_IATA name lookup, never the bare-3-letter-code
        # path: a marketing exclamation like "HOT" or "TOP" is also 3
        # uppercase letters and would otherwise be misread as an airport.
        if prefix and prefix.lower() in _CITY_TO_IATA and not _is_implausible_short_hop(prefix):
            return prefix

    route = _ROUTE_SPLIT_RE.split(title, maxsplit=1)
    is_explicit_route = len(route) == 2 and bool(route[1].strip())
    ambiguous_fallback = False
    if is_explicit_route:
        dest = route[1]
    elif hotel_lead and len(hotel_route := _HOTEL_DEST_SPLIT_RE.split(title, maxsplit=1)) == 2 and hotel_route[1].strip():
        # Hotel-lead titles name their place with "auf/in/on", not "nach"/
        # "to" (a hotel sits IN a place; nothing is travelling TO it in the
        # sentence) - "5* Luxusresort auf Bali ab 45€/Nacht" -> "Bali".
        dest = hotel_route[1]
    else:
        head = title.rsplit(":", 1)[-1].strip() if ":" in title.split(" ab ")[0] else title
        # "<dest> ab <price/origin> ..." - the destination leads the title.
        match = re.match(r"^(?P<dest>.+?)\s+(?:ab|von|from)\s", head, re.IGNORECASE)
        if not match:
            return None
        dest = match.group("dest")
        ambiguous_fallback = True
    dest = _clean_destination_text(dest)
    if (
        not dest
        or dest.lower() in _NOT_A_DESTINATION
        or _PROMO_DESTINATION_RE.search(dest)
        or _is_implausible_short_hop(dest)
    ):
        return None
    if ambiguous_fallback and find_dach_origins(f"ab {dest}"):
        # Only the "<dest> ab ..." fallback is this ambiguous ("Hamburg ab
        # 30€" -> dest would wrongly be "Hamburg", the actual ORIGIN) - an
        # explicit route ("HAM to VIE", "HAM-ZRH") already names a real
        # destination even when it's itself a DACH airport (Vienna,
        # Zurich, ... are real, desirable destinations too). The hotel
        # "auf/in/on" split has no such double meaning, so it's exempt.
        return None
    return dest


def _destination_iata(destination: str | None) -> str | None:
    if not destination:
        return None
    if re.fullmatch(r"[A-Z]{3}", destination):
        return destination
    lowered = destination.lower()
    for name, code in _CITY_TO_IATA.items():
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lowered):
            return code
    return None


def _extract_price(title: str) -> float | None:
    for match in _PRICE_RE.finditer(title):
        # "133€/Nacht", "4€ pro Tag": not a flight price.
        if re.match(r"\s*(?:/|pro\s|p\.\s?)\s*(?:Nacht|Tag|Night|Day)", title[match.end():], re.IGNORECASE):
            continue
        return _to_float(match.group("a") or match.group("b"))
    return None


# --- hotel-first signals ("Hotel-Drop inkl. Flug") -------------------------------
# See the module docstring's "HOTEL-FIRST SIGNALS" section and DealSignal's
# own docstring for what deal_lead="hotel" changes.

_HOTEL_DEST_SPLIT_RE = re.compile(r"\s+(?:auf|in|on)\s+", re.IGNORECASE)

_HOTEL_LEAD_RE = re.compile(
    r"(?<!\w)(?:hotel|resort|övernachtung|übernachtung|overnight)(?!\w)"
    r"|\d\s*[★*](?!\w)"
    r"|\d\s*-?\s*sterne\b",
    re.IGNORECASE,
)


def _is_hotel_lead_title(title: str) -> bool:
    """True if `title` primarily advertises a HOTEL/resort stay (names a
    star rating or a hotel/resort/overnight-stay word), not a flight -
    the trigger for this project's "Hotel-Drop inkl. Flug" reverse-combo
    (see alerts/instant_alert_formatter.py's _signal_hotel_combo_estimate).
    Never triggered by a discount percentage alone - "-65%" also appears
    in ordinary flight-promo titles this module already rejects
    elsewhere, so it is only ever used as a BARGAIN threshold
    (feed_radar.is_hotel_deal_worthy), never a detection signal on its
    own."""
    return _HOTEL_LEAD_RE.search(title) is not None


# A price followed by "/Nacht" ("pro Nacht", "p. Nacht") is this project's
# only accepted signal for a hotel's NIGHTLY rate - never guessed from an
# unmarked price, which could just as easily be a stay's flat total.
_NIGHTLY_PRICE_RE = re.compile(
    r"(?:€\s?(?P<a>\d[\d.,]*)|(?P<b>\d[\d.,]*)\s?(?:€|EUR\b|Euro\b))"
    r"\s*(?:/|pro\s|p\.\s?)\s*(?:Nacht|Night)\b",
    re.IGNORECASE,
)


def _extract_hotel_nightly_price(title: str) -> float | None:
    """The hotel's EUR/night rate, or None if the title never actually
    marks a price as per-night - an unmarked price is never assumed to be
    the nightly rate (it could be a flat package total instead), so a
    hotel-lead signal with no explicit ".../Nacht" price simply has no
    price at all rather than a guessed one."""
    match = _NIGHTLY_PRICE_RE.search(title)
    return _to_float(match.group("a") or match.group("b")) if match else None


_DISCOUNT_PERCENT_RE = re.compile(r"-\s*(\d{1,3})\s*%")


def _extract_discount_percent(title: str) -> int | None:
    """The feed's own self-reported discount ("-65%"), sanity-bounded to
    1-95% (never a typo'd/fabricated-looking 100%+ "discount") - or None
    if the title states none at all."""
    match = _DISCOUNT_PERCENT_RE.search(title)
    if not match:
        return None
    value = int(match.group(1))
    return value if 0 < value <= 95 else None


def _to_float(raw: str) -> float | None:
    raw = raw.rstrip(".,")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", raw):
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


# Category/tag names a deal site uses for genuine mistake fares (Fly4free
# files them under "Error").
_ERROR_CATEGORIES = frozenset({"error", "error fare", "error fares", "mistake fare", "preisfehler"})


def _tier_1_reasons(
    title: str,
    price: float | None,
    destination_iata: str | None = None,
    categories: Sequence[str] = (),
) -> tuple[str, ...]:
    reasons = [f"keyword:{label}" for label, pattern in _TIER_1_KEYWORDS if pattern.search(title)]
    if any(c.strip().lower() in _ERROR_CATEGORIES for c in categories):
        reasons.append("category:error")
    long_haul = destination_iata in _LONG_HAUL
    limit = TIER_1_MAX_PRICE_LONG_HAUL if long_haul else TIER_1_MAX_PRICE
    if price is not None and price <= limit:
        reasons.append(f"price<={limit:.0f}" + (":long-haul" if long_haul else ""))
    return tuple(reasons)


def _extract_travel_dates(text: str) -> str | None:
    match = _RANGE_DATE_RE.search(text) or _MONTH_RE.search(text)
    return match.group(0) if match else None


def parse_travel_date_range(travel_dates: str | None, *, today: date | None = None) -> tuple[date, date] | None:
    """A concrete (departure, return) pair from `DealSignal.travel_dates`,
    or None. Only the day-precise "12.10.–19.10.2026" shape (matched by
    _RANGE_DATE_RE) can ever produce one - a month-only string
    ("Oktober 2026") names no actual day, and turning it into one would be
    exactly the kind of fabricated date this project never allows.

    A missing year on either half borrows the OTHER half's year; if
    NEITHER half has one, the year is inferred from `today` (rolled to
    next year if the resulting departure date would already be in the
    past) - never left to default to year 1900 by accident. Returns None
    for anything that doesn't parse into two real calendar dates with the
    return after the departure.
    """
    if not travel_dates:
        return None
    match = _RANGE_DATE_RE.search(travel_dates)
    if not match:
        return None
    parts = re.split(r"\s*(?:–|-|bis)\s*", match.group(0))
    if len(parts) != 2:
        return None

    resolved_today = today or date.today()

    def split_dmy(part: str) -> tuple[int, int, int | None]:
        numbers = part.strip(".").split(".")
        day, month = int(numbers[0]), int(numbers[1])
        year = int(numbers[2]) if len(numbers) > 2 and numbers[2] else None
        if year is not None and year < 100:
            year += 2000
        return day, month, year

    try:
        dep_day, dep_month, dep_year = split_dmy(parts[0])
        ret_day, ret_month, ret_year = split_dmy(parts[1])
    except (ValueError, IndexError):
        return None

    year = dep_year or ret_year
    if year is None:
        year = resolved_today.year
        if date(year, dep_month, dep_day) < resolved_today:
            year += 1
    dep_year = dep_year or year
    ret_year = ret_year or year

    try:
        departure = date(dep_year, dep_month, dep_day)
        return_ = date(ret_year, ret_month, ret_day)
    except ValueError:
        return None
    return (departure, return_) if return_ > departure else None


def _parse_date(value: str | None) -> datetime | None:
    """RFC 822 (RSS pubDate) or ISO 8601 (Atom published/updated); None if
    neither parses."""
    if not value:
        return None
    text = value.strip()
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean(value: str | None) -> str:
    if not value:
        return ""
    # FlyerTalk's ISO-8859-1 feed carries the cp1252 euro sign as \x80.
    text = html.unescape(_TAG_RE.sub(" ", value)).replace("\x80", "€")
    return re.sub(r"\s+", " ", text).strip()


def _canonical_link(link: str) -> str:
    """Link without tracking parameters (utm_*) - other query parameters
    stay, since some forums identify a thread only by e.g. ?t=123."""
    if not link:
        return ""
    parts = urlsplit(link)
    query = "&".join(
        pair for pair in parts.query.split("&") if pair and not pair.lower().startswith("utm_")
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
