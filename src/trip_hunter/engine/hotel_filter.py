"""Which hotel offers may be suggested next to a flight.

Two independent rules:

1. `is_acceptable_stay` - a HARD exclusion of shared sleeping (dorm beds,
   bunk beds, capsules, shared rooms) and of plain youth hostels. It is
   applied where offers enter the system (SerpApiAccommodationProvider),
   so snapshots, baselines and alerts all see the same population - a
   14 EUR dorm bed must not drag the hotel baseline down and then make
   every real room look expensive.

   Hybrid hostel chains (a&o, Generator, ...) are deliberately NOT
   excluded by name: they sell cheap private double rooms. Only the
   room_type / description text decides. "shared" alone is far too
   ambiguous ("shared lounge", "shared terrace" on a perfectly normal
   private room), so it only counts next to a room/bed noun ("shared
   room", "shared dorm", "beds shared"). Trade-off, stated plainly: a
   hybrid whose listing text mentions dorm rooms is excluded too, because
   the provider's price is the cheapest rate - which is then the dorm bed.

2. `meets_min_rating` / `best_accommodation` - the quality preference:
   >= MIN_HOTEL_RATING stars (0-5 scale; an unrated hotel is not blocked,
   like everywhere else). Among the remaining offers the cheapest wins -
   with the flight fixed, cheapest hotel is also the best total price per
   person (flight + hotel / 2).
"""

from __future__ import annotations

import re
from typing import Iterable

from trip_hunter.models import AccommodationOffer

MIN_HOTEL_RATING = 3.8

_YOUTH_HOSTEL_RE = re.compile(r"youth[\s-]+hostels?|jugendherbergen?", re.IGNORECASE)

# Matched against room_type + description only (never the hotel name).
_SHARED_SLEEPING_RE = re.compile(
    r"\bdorm(?:itor(?:y|ies))?s?\b"
    r"|\bbunk[\s-]*beds?\b"
    r"|\bcapsules?\b"
    r"|\bshared\s+(?:dorm\w*|rooms?|beds?|bunks?|sleeping|accommodations?)\b"
    r"|\b(?:dorm\w*|rooms?|beds?)\s+(?:are\s+)?shared\b"
    r"|bett(?:en)?\s+im\s+schlafsaal"
    r"|mehrbettzimmer"
    r"|schlafsa{1,2}l(?:en?)?\b"
    r"|schlafsäle(?:n)?\b",
    re.IGNORECASE,
)


def is_acceptable_stay(offer: AccommodationOffer) -> bool:
    """False for a plain youth hostel (name or text) or shared sleeping
    (room_type / description)."""
    text = " ".join(part for part in (offer.room_type, offer.description) if part)
    if _YOUTH_HOSTEL_RE.search(f"{offer.name} {text}"):
        return False
    return _SHARED_SLEEPING_RE.search(text) is None


def meets_min_rating(offer: AccommodationOffer) -> bool:
    return offer.rating is None or offer.rating >= MIN_HOTEL_RATING


def best_accommodation(
    offers: Iterable[AccommodationOffer], *, require_rating: bool = True
) -> AccommodationOffer | None:
    """Cheapest acceptable, sufficiently rated offer. With
    `require_rating=False` (error fares: the hotel rating is irrelevant),
    a low-rated offer is used only when no well-rated one exists."""
    acceptable = [offer for offer in offers if is_acceptable_stay(offer)]
    preferred = [offer for offer in acceptable if meets_min_rating(offer)]
    pool = preferred if (preferred or require_rating) else acceptable
    return min(pool, key=lambda offer: offer.total_price) if pool else None
