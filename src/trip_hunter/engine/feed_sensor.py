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

FlyerTalk ("Mileage Run Deals", forum 372) is the primary error-fare
source: an open vBulletin RSS 2.0 feed (no Cloudflare challenge; verified
reachable, though it can legitimately hold zero items at a given moment).
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
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

import requests

FEED_SOURCES: dict[str, str] = {
    "travel-dealz": "https://travel-dealz.de/feed/",
    "secretflying": "https://www.secretflying.com/feed/",
    "flyertalk": "https://www.flyertalk.com/forum/external.php?type=rss2&forumids=372",
}

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
    "mailand": "MXP", "milan": "MXP", "bergamo": "BGY", "venedig": "VCE", "venice": "VCE",
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

# Explicit intercontinental allowlist for the higher price bar - like
# error_fare_floor.MID_HAUL_DESTINATIONS, never inferred from geography.
_LONG_HAUL: frozenset[str] = frozenset(
    {
        "JFK", "EWR", "LAX", "SFO", "ORD", "MIA", "BOS", "IAD", "ATL", "DFW", "SEA", "YYZ", "YVR",
        "MEX", "CUN", "PUJ", "HAV", "GRU", "EZE", "BOG", "SCL", "LIM", "BKK", "SIN", "HKG", "NRT",
        "HND", "ICN", "PEK", "PVG", "DEL", "BOM", "BLR", "DXB", "DOH", "AUH", "JNB", "CPT", "NBO",
        "SYD", "MEL", "AKL", "NYC", "WAS", "CHI",
    }
)

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
_ROUTE_PAIR_RE = re.compile(r"\b([A-Z]{3})\s*(?:-|–|—|/|→|->)\s*([A-Z]{3})\b")
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


def parse_feed(xml_text: str, source: str, *, tier_1_only: bool = False) -> list[DealSignal]:
    """Parse one RSS 2.0 document into signals for deals departing from a
    German airport (`tier_1_only` keeps just the error-fare-like ones).
    Malformed or unsafe XML yields []. Pure - no network."""
    if len(xml_text) > _MAX_FEED_BYTES or re.search(r"<!(?:DOCTYPE|ENTITY)", xml_text, re.IGNORECASE):
        print(f"Feed {source}: übersprungen (zu groß oder enthält DOCTYPE/ENTITY).")
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        print(f"Feed {source}: kein gültiges XML.")
        return []

    signals: list[DealSignal] = []
    for item in root.iter("item"):
        signal = _item_to_signal(item, source)
        if signal is not None and (signal.is_tier_1 or not tier_1_only):
            signals.append(signal)
    return signals


def fetch_feed(
    url: str, *, session: requests.Session | None = None, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
) -> str | None:
    """The feed body, or None (with a printed reason) on any failure."""
    http = session or requests.Session()
    try:
        response = http.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout_seconds)
    except requests.exceptions.Timeout:
        print(f"Feed {url}: Timeout.")
        return None
    except requests.exceptions.RequestException as exc:
        print(f"Feed {url}: Netzwerkfehler ({type(exc).__name__}).")
        return None
    if response.status_code != 200:
        print(f"Feed {url}: HTTP {response.status_code}.")
        return None
    return response.text


def scan_feeds(
    sources: dict[str, str] | None = None,
    *,
    tier_1_only: bool = False,
    session: requests.Session | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[DealSignal]:
    """Fetch + parse every source; a failing source is skipped, never
    fatal. Duplicates (same link without tracking params) are dropped;
    Tier-1 signals come first, then newest first."""
    seen: set[str] = set()
    signals: list[DealSignal] = []
    for name, url in (sources if sources is not None else FEED_SOURCES).items():
        body = fetch_feed(url, session=session, timeout_seconds=timeout_seconds)
        if body is None:
            continue
        for signal in parse_feed(body, name, tier_1_only=tier_1_only):
            key = _canonical_link(signal.link) or signal.title
            if key not in seen:
                seen.add(key)
                signals.append(signal)
    signals.sort(key=lambda s: (not s.is_tier_1, -(s.published.timestamp() if s.published else 0)))
    return signals


# --- parsing helpers -----------------------------------------------------------


def _item_to_signal(item: ET.Element, source: str) -> DealSignal | None:
    title = _clean(item.findtext("title"))
    if not title:
        return None
    description = _clean(item.findtext("description"))
    categories = " ".join(_clean(c.text) for c in item.findall("category"))
    if any(m in f"{title} {categories}".lower() for m in _NON_FLIGHT_MARKERS):
        return None

    origins = find_german_origins(title)
    if not origins:
        return None

    destination = _extract_destination(title)
    price = _extract_price(title)
    destination_iata = _destination_iata(destination)
    reasons = _tier_1_reasons(title, price, destination_iata)
    return DealSignal(
        source=source,
        title=title,
        link=_clean(item.findtext("link")),
        origins=origins,
        tier_1_reasons=reasons,
        destination=destination,
        destination_iata=destination_iata,
        price=price,
        travel_dates=_extract_travel_dates(title) or _extract_travel_dates(description),
        published=_parse_date(item.findtext("pubDate")),
    )


def find_german_origins(title: str) -> tuple[str, ...]:
    """German IATA codes named in `title` as DEPARTURE airports: after
    "ab/von/from/aus" ("ab Hamburg", "von München und Frankfurt"), or on
    the left side of "→" / "to" / "nach". An airport named only as the
    destination ("Singapur → München") is not an origin."""
    route = _ROUTE_SPLIT_RE.split(title, maxsplit=1)
    left_end = len(route[0]) if len(route) == 2 else 0

    found: list[tuple[int, str]] = []  # (position in title, code)
    for pair in _route_pairs(title):
        if pair[0] in GERMAN_ORIGINS and pair[0] not in (code for _, code in found):
            found.append((pair[2], pair[0]))
    for code, names in GERMAN_ORIGINS.items():
        name_re = re.compile(rf"(?<![\w-])(?:{'|'.join(map(re.escape, names))})(?![\w-])", re.IGNORECASE)
        matches = [*name_re.finditer(title), *re.finditer(rf"\b{code}\b", title)]
        for match in sorted(matches, key=lambda m: m.start()):
            if match.start() < left_end or _ORIGIN_LEAD_RE.search(title[: match.start()]):
                if code not in (c for _, c in found):
                    found.append((match.start(), code))
                break
    return tuple(code for _, code in sorted(found))


def _route_pairs(title: str) -> list[tuple[str, str, int]]:
    """(origin, destination, position) for every "AAA-BBB" style code
    pair in `title`; pairs whose right side isn't plausibly an airport
    ("HAM-USA") are skipped."""
    return [
        (m.group(1), m.group(2), m.start())
        for m in _ROUTE_PAIR_RE.finditer(title)
        if m.group(2) not in _NOT_AN_AIRPORT
    ]


def _extract_destination(title: str) -> str | None:
    # FlyerTalk style: the code pair of a German departure names the destination.
    for origin, destination, _ in _route_pairs(title):
        if origin in GERMAN_ORIGINS:
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


def _tier_1_reasons(title: str, price: float | None, destination_iata: str | None = None) -> tuple[str, ...]:
    reasons = [f"keyword:{label}" for label, pattern in _TIER_1_KEYWORDS if pattern.search(title)]
    long_haul = destination_iata in _LONG_HAUL
    limit = TIER_1_MAX_PRICE_LONG_HAUL if long_haul else TIER_1_MAX_PRICE
    if price is not None and price <= limit:
        reasons.append(f"price<={limit:.0f}" + (":long-haul" if long_haul else ""))
    return tuple(reasons)


def _extract_travel_dates(text: str) -> str | None:
    match = _RANGE_DATE_RE.search(text) or _MONTH_RE.search(text)
    return match.group(0) if match else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", value))).strip()


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
