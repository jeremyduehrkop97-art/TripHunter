"""Curated, hand-picked 4-5★ reference hotels per top destination -
replaces a generic Booking.com CITY search for a destination this
project has a specific, verified property for.

WHY: a generic city search (monetization/link_builder.py build_hotel_link's
own `generic=True`/`stars` filter, itself a fix for an earlier reported
bug - see that function's own "HOTEL FREIGOLD" docstring) still lands a
user on a long results list, often topped by whatever Booking's own
ranking currently favours - in a live test, a filtered Nairobi search
put a private apartment ahead of any real hotel, deep in an 8,000+
result list. Users don't want a search portal, they want to book THE
deal immediately. Each entry below is a single, specific, hand-verified
property that clears this project's own quality bar: 4 or 5 stars, a
Booking.com guest rating of 8.4+ ("Hervorragend"/"Sehr gut"), and a
genuinely good price-to-quality position for its destination.

EXTERNAL FACTS (hotel names, Booking.com property-page slugs, star
ratings, guest review scores): verified live via web search, 2026-10 -
never invented. `base_price_per_night_eur` is this project's own
REALISTIC AVERAGE-RATE ESTIMATE for a double room (same "openly
documented, reviewable estimate, never a live quote" convention
monetization/hotel_price_guide.py's own docstring already establishes
for its tier table - "Richtwert" in every alert/deal-sheet that shows
it, never claimed to be a live price). A hotel's name, star rating,
guest score or Booking.com slug CAN change over time (a renovation,
rebrand, delisting or re-listing) - this registry should be
spot-checked periodically, not treated as permanently accurate.

CAPACITY: a boutique property's tiny room count can sell out outright
under real demand - the reported bug that retired Riad Challa (RAK,
8-12 rooms) below. Every other entry was re-checked for the same risk,
2026-10: the smallest two (The Independent Hotel, Rome, 45 rooms; Nakar
Hotel, Palma, 57 rooms) are real commercial-scale properties, nowhere
near Riad Challa's single-digit/low-teens count, so both stay as-is -
this project's bar is "not a handful of rooms", not "not boutique".

SCOPE, deliberately narrow: only used as a substitute for a GENERIC
hotel placeholder this project invented itself (no real accommodation
data at all - the "4-Sterne Hotel" case monetization/link_builder.py's
`generic=True` already covers). NEVER substituted for a feed's own
specific hotel-lead claim (e.g. "5* Luxusresort auf Bali ab 45€/Nacht") -
swapping in an unrelated curated property there would misrepresent the
actual deal the feed found, recreating the exact "wrong hotel" bug class
this registry exists to fix, just with a different wrong hotel. See
alerts/instant_alert_formatter.py's own call sites for which branches
this applies to and which deliberately don't.

Deliberately NOT exhaustive - only this project's own curated top
destinations (the same set engine/daily_scanner.py already searches,
plus Seoul). An uncurated destination falls back to build_hotel_link's
existing generic city search - a working, if broader, search beats no
link at all.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CuratedHotel:
    # Display name INCLUDING its star-rating suffix (e.g. "Nakar Hotel
    # 4★") - shown as-is in both the Telegram message and the deal
    # sheet's "hn" field, never a bare name with the rating implied.
    hotel_name: str
    # Booking.com's own property-page path (never a search page), e.g.
    # "hotel/th/column-bangkok.de.html" - appended to
    # "https://www.booking.com/" as-is.
    booking_hotel_path: str
    # A realistic average EUR/night rate for a double room - this
    # project's own ESTIMATE (see module docstring), never a live quote.
    base_price_per_night_eur: int
    # This registry's own hard floor, kept here so a test can verify
    # every entry actually clears it (never trust-and-forget):
    stars: int  # 4 or 5
    review_score: float  # Booking.com's own guest score, out of 10, >= 8.4


# IATA destination code -> its one hand-picked reference hotel. Matches
# engine/daily_scanner.DESTINATIONS (the project's own existing curated
# top-destination list) plus Seoul (SEL) - never a separate, drifting
# allowlist of "top destinations".
CURATED_HOTELS: dict[str, CuratedHotel] = {
    # Short-haul / Europe
    "PMI": CuratedHotel("Nakar Hotel 4★", "hotel/es/nkr.de.html", 145, 4, 8.9),
    "BCN": CuratedHotel("W Barcelona 5★", "hotel/es/w-barcelona.de.html", 290, 5, 8.5),
    "LIS": CuratedHotel("Hotel Mundial 4★", "hotel/pt/hotelmundial.de.html", 130, 4, 8.5),
    "FCO": CuratedHotel("The Independent Hotel 4★", "hotel/it/the-independent.de.html", 140, 4, 8.5),
    # Mid-haul (Gulf, Canary Islands, North Africa)
    "DXB": CuratedHotel("Rove Downtown Dubai 4★", "hotel/ae/rove-downtown-dubai.de.html", 115, 4, 9.2),
    "TFS": CuratedHotel("H10 Costa Adeje Palace 4★", "hotel/es/h10dostaadejepalace.de.html", 150, 4, 8.5),
    # Was Riad Challa Hotel & Spa (4★, 8-12 rooms) - a live test found it
    # sold out immediately: a boutique riad's tiny room count makes it a
    # poor fit for a deal that can route many readers to the same property
    # at once. Replaced with a 503-room 5★ resort for real availability at
    # scale (verified live, 2026-10: Booking.com overall score 8.6/10,
    # https://www.booking.com/hotel/ma/movenpick-mansour-eddahbi-amp-amp-palais-des-congres-marrakech.html).
    "RAK": CuratedHotel(
        "Mövenpick Hotel Mansour Eddahbi Marrakech 5★",
        "hotel/ma/movenpick-mansour-eddahbi-amp-amp-palais-des-congres-marrakech.de.html", 190, 5, 8.6,
    ),
    # Long-haul (Americas, Southeast Asia, East Africa)
    "JFK": CuratedHotel("MADE Hotel 4★", "hotel/us/made.de.html", 260, 4, 8.7),
    "MIA": CuratedHotel("The Betsy Hotel, South Beach 4★", "hotel/us/the-betsy.de.html", 320, 4, 8.6),
    "BKK": CuratedHotel("Column Bangkok Hotel 5★", "hotel/th/column-bangkok.de.html", 95, 5, 8.7),
    "DPS": CuratedHotel("THE HAVEN Bali Seminyak 4★", "hotel/id/the-haven-seminyak.de.html", 75, 4, 8.5),
    "NBO": CuratedHotel(
        "Mövenpick Hotel & Residences Nairobi 5★", "hotel/ke/movenpick-amp-residences-nairobi.de.html", 130, 5, 8.4,
    ),
    "CUN": CuratedHotel(
        "Riu Palace Kukulkan - Adults Only - All Inclusive 5★",
        "hotel/mx/riu-palace-kukulkan-adults-only-all-inclusive.de.html", 190, 5, 8.5,
    ),
    "SEL": CuratedHotel("L7 HONGDAE by LOTTE 4★", "hotel/kr/l7-hongdae.de.html", 95, 4, 8.7),
}

# This registry's own hard floor (see module/class docstrings) - never
# just documentation, also asserted against every entry by
# tests/test_curated_hotels.py.
MIN_STARS = 4
MIN_REVIEW_SCORE = 8.4


def curated_hotel(destination_iata: str | None) -> CuratedHotel | None:
    """The curated reference hotel for `destination_iata`, or None if
    this destination isn't curated at all - callers fall back to a
    generic filtered city search (monetization/link_builder.py's own
    `generic`/`stars`), never a crash or a guessed entry."""
    if not destination_iata:
        return None
    return CURATED_HOTELS.get(destination_iata)
