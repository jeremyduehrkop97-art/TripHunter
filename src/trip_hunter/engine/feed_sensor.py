"""Free early-warning sensor: scans public deal-blog RSS feeds
(Travel-Dealz, Secret Flying) for flight deals departing from German
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
"ORIGIN→DEST" pair whose left code is a German airport (JFK-FRA, i.e. an
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
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
import os
from datetime import datetime, timedelta, timezone
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
    # German departure airport survive the filters.
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
# has no German origin and is dropped rather than guessed.
_LINK_MUST_CONTAIN: dict[str, str] = {"urlaubspiraten": "/fluege/"}
_DESCRIPTION_SOURCES = frozenset({"urlaubspiraten"})

TIER_1_MAX_PRICE = 40.0  # short-haul (Europe)
TIER_1_MAX_PRICE_LONG_HAUL = 250.0  # intercontinental
_MAX_FEED_BYTES = 2_000_000
_DEFAULT_TIMEOUT_SECONDS = 10.0
_USER_AGENT = "Mozilla/5.0 (compatible; TripHunterFeedSensor/0.1)"

# IATA code -> names it appears under in German/English deal titles.
GERMAN_ORIGINS: dict[str, tuple[str, ...]] = {
    "HAM": ("Hamburg",),
    "BER": ("Berlin",),
    "FRA": ("Frankfurt",),
    "MUC": ("München", "Muenchen", "Munich"),
    "DUS": ("Düsseldorf", "Duesseldorf", "Dusseldorf"),
}

# Explicit allowlist, never inferred - extend when a destination matters.
_CITY_TO_IATA: dict[str, str] = {
    "palma": "PMI", "mallorca": "PMI", "barcelona": "BCN", "rom": "FCO", "rome": "FCO",
    "lissabon": "LIS", "lisbon": "LIS", "porto": "OPO", "madrid": "MAD", "malaga": "AGP",
    "málaga": "AGP", "sevilla": "SVQ", "valencia": "VLC", "ibiza": "IBZ", "faro": "FAO",
    "paris": "CDG", "london": "LON", "amsterdam": "AMS", "wien": "VIE", "vienna": "VIE",
    "mailand": "MXP", "milan": "MXP", "chiang mai": "CNX", "taipeh": "TPE", "taipei": "TPE", "calgary": "YYC", "karibik": "PUJ", "tokyo": "TYO", "tokio": "TYO", "seoul": "SEL", "los angeles": "LAX", "san francisco": "SFO", "miami": "MIA", "chicago": "CHI", "boston": "BOS", "toronto": "YYZ", "mexico city": "MEX", "cancun": "CUN", "bali": "DPS", "denpasar": "DPS", "singapore": "SIN", "singapur": "SIN", "hong kong": "HKG", "delhi": "DEL", "mumbai": "BOM", "sydney": "SYD", "cape town": "CPT", "kapstadt": "CPT", "punta cana": "PUJ", "havana": "HAV", "malediven": "MLE", "bischkek": "FRU", "bergamo": "BGY", "venedig": "VCE", "venice": "VCE",
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

# Deal categories that are never a flight from a German airport.
_NON_FLIGHT_MARKERS = ("kreuzfahrt", "cruise", "gutschein", "interrail")

# Headline words that can lead a title without naming a place.
_NOT_A_DESTINATION = frozenset(
    {"error fare", "mistake fare", "error", "mistake", "drop", "preisfehler", "fehlerpreis", "flug", "flüge", "flights", "flight", "deal", "angebot", "achtung", "wow"}
)

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
    """One deal spotted in a feed - an unverified hint, see module docstring."""

    source: str
    title: str
    link: str
    origins: tuple[str, ...]  # German IATA codes, e.g. ("MUC", "FRA")
    tier_1_reasons: tuple[str, ...]
    destination: str | None = None  # free text as written, e.g. "Taipeh"
    destination_iata: str | None = None
    price: float | None = None  # EUR, the first price named in the title
    travel_dates: str | None = None  # raw text, e.g. "12.10.–19.10." / "Oktober 2026"
    published: datetime | None = None

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
    German airport (`tier_1_only` keeps just the error-fare-like ones).
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
    outcome ("ok: 2 Abflüge ab DE, 0 Tier 1" / "nicht erreichbar"), so the
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
    its fresh German departures, or None if no URL worked."""
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
        status[name] = f"ok: {len(fresh)} Abflüge ab DE, {sum(s.is_tier_1 for s in fresh)} Tier 1"
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
    origins = find_german_origins(text)
    if not origins:
        return None

    destination = _extract_destination(title)
    if destination is None and source in _DESCRIPTION_SOURCES:
        match = _DESC_DESTINATION_RE.search(description)
        destination = match.group("dest") if match else None
    price = _extract_price(text)
    destination_iata = _destination_iata(destination)
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
    )


def find_german_origins(title: str) -> tuple[str, ...]:
    """German IATA codes named in `title` as DEPARTURE airports: after
    "ab/von/from/aus" ("ab Hamburg", "von München und Frankfurt"), or on
    the left side of "→" / "to" / "nach". An airport named only as the
    destination ("Singapur → München") is not an origin."""
    route = _ROUTE_SPLIT_RE.split(title, maxsplit=1)
    left_end = len(route[0]) if len(route) == 2 else 0

    found: list[tuple[int, str]] = []  # (position in title, code)
    for departures, _, position in _routes(title):
        for code in departures:
            if code in GERMAN_ORIGINS and code not in (c for _, c in found):
                found.append((position, code))
    for code, names in GERMAN_ORIGINS.items():
        name_re = re.compile(rf"(?<![\w-])(?:{'|'.join(map(re.escape, names))})(?![\w-])", re.IGNORECASE)
        matches = [*name_re.finditer(title), *re.finditer(rf"\b{code}\b", title)]
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


def _extract_destination(title: str) -> str | None:
    # FlyerTalk style: the code pair of a German departure names the destination.
    for departures, destination, _ in _routes(title):
        if destination and any(code in GERMAN_ORIGINS for code in departures):
            return destination
    route = _ROUTE_SPLIT_RE.split(title, maxsplit=1)
    if len(route) == 2 and route[1].strip():
        dest = _DEST_STOP_RE.split(route[1], maxsplit=1)[0]
    else:
        head = title.rsplit(":", 1)[-1].strip() if ":" in title.split(" ab ")[0] else title
        # "<dest> ab <price/origin> ..." - the destination leads the title.
        match = re.match(r"^(?P<dest>.+?)\s+(?:ab|von|from)\s", head, re.IGNORECASE)
        if not match:
            return None
        dest = match.group("dest")
    dest = re.sub(r"[^\w\s,.'()/-]", "", dest)  # drop emoji/flags
    dest = re.sub(r"\s+", " ", dest).strip(" ,-–")
    dest = re.sub(r"^(?:Flug|Flüge|Flights?)\s+", "", dest, flags=re.IGNORECASE)
    if not dest or dest.lower() in _NOT_A_DESTINATION or find_german_origins(f"ab {dest}"):
        return None  # a German airport is the origin here, not the destination
    return dest


def _destination_iata(destination: str | None) -> str | None:
    if not destination:
        return None
    if re.fullmatch(r"[A-Z]{3}", destination) and destination not in GERMAN_ORIGINS:
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
