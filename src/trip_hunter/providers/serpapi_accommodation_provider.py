"""SerpApiAccommodationProvider: normalizes SerpApi's Google Hotels results
into our internal AccommodationOffer model.

The rest of the system (deal detection, scoring, DealEngine) never sees
SerpApi's or Google Hotels' JSON shape - only AccommodationOffer. Mirrors
serpapi_flight_provider.py's structure and conventions - see
docs/ARCHITECTURE.md.

Verified against a real live response (1 controlled, human-approved SerpApi
credit, 2026-09-11, q="Palma de Mallorca, Spain", 2026-10-02..2026-10-07,
EUR - see tests/fixtures/serpapi_hotels_pmi.json): `properties[]` entries
use exactly `total_rate.extracted_lowest` / `rate_per_night.extracted_lowest`
and `name`/`overall_rating` as assumed - no normalizer changes were needed
for those. Two real findings this fixture confirmed:
1. A `link` field (the property's own listing/booking URL) is present on
   every entry and costs nothing extra to capture - added as
   AccommodationOffer.booking_link.
2. At least one real property ("Hotel Basilica") had NEITHER `total_rate`
   NOR `rate_per_night` at all (no price data returned, likely sold out for
   the requested dates) - confirming the "skip, don't guess" fallback below
   is not a hypothetical edge case but observed real behavior.
`overall_rating`/`reviews` can be entirely absent (seen on vacation-rental
type entries) - already handled by the existing None-defaulting. This is
NOT a claim that every possible Google Hotels response shape is covered
(e.g. `type: "vacation rental"` entries were seen but aren't distinguished
from hotels in our model) - only that the fields we actually normalize
matched real data for this one query.

Destination -> search query: Google Hotels has no concept of an airport/
IATA code, only a free-text location query (e.g. "Palma de Mallorca,
Spain"). We never guess a query from a 3-letter code - `_DESTINATION_QUERY`
is an explicit, small mapping (same pattern as
mock_accommodation_provider.py's `_BASELINE_PRICE_PER_NIGHT`). A
destination missing from the mapping returns an empty offer list (same
"nothing found" signal `search_accommodations` already uses elsewhere,
e.g. NullAccommodationProvider) rather than raising or guessing a query -
but see the docstring on `_DESTINATION_QUERY` for why that's a coverage
gap, not "no hotels available".

Typical price baseline: `get_typical_total_price` always returns None.
SerpApi's Google Hotels engine has no equivalent of Google Flights'
`price_insights` (no documented typical-price-range or price-level field
for hotels) - inventing a heuristic from the search results themselves
would repeat the exact mistake "Observation Semantics" already ruled out
for flights (treating one snapshot's own result spread as if it were a
baseline). A real accommodation baseline would need its own historical
observation system, analogous to price_history_repository.py for flights -
not built here; see docs/PRODUCT_SPEC.md "Baseline Problem".

Credit safety: one `search_accommodations` call is one cached SerpApi
request, same TTL/cache mechanism as the flight provider. No route loops,
no automatic retries.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trip_hunter.caching import FileCache, hotel_search_cache_key
from trip_hunter.models import AccommodationOffer
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.serpapi_hotels_client import SerpApiHotelsClient

_PROVIDER_NAME = "serpapi_google_hotels"

# Explicit destination(IATA code) -> Google Hotels search query mapping.
# Deliberately NOT a geocoding/airport-database lookup - MVP scope tracks
# the same small set of routes the rest of the project has used so far
# (see docs/PRODUCT_SPEC.md). Extend this dict (or pass a custom one via
# the constructor) before searching a new destination; an unmapped
# destination returns no offers rather than a guessed query.
_DESTINATION_QUERY: dict[str, str] = {
    "PMI": "Palma de Mallorca, Spain",
    # Added for the daily_sampler.py weekend-getaway hotel targets - see
    # sampling_targets.py.
    "BCN": "Barcelona, Spain",
    "FCO": "Rome, Italy",
}


class SerpApiAccommodationProvider(AccommodationProvider):
    def __init__(
        self,
        client: SerpApiHotelsClient,
        cache: FileCache | None = None,
        currency: str = "EUR",
        destination_query: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        self._cache = cache
        self._currency = currency
        self._destination_query = destination_query or _DESTINATION_QUERY

    def search_accommodations(
        self, destination: str, check_in: date, check_out: date
    ) -> list[AccommodationOffer]:
        query = self._destination_query.get(destination)
        if query is None:
            # Not "no hotels available" - we simply don't have a search
            # query mapped for this destination code yet. See module
            # docstring. Never guess a query from the raw code.
            return []

        nights = (check_out - check_in).days
        check_in_str = check_in.isoformat()
        check_out_str = check_out.isoformat()

        cache_key = hotel_search_cache_key(destination, check_in_str, check_out_str, self._currency)
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return [_offer_from_dict(raw) for raw in cached]

        response_json = self._client.search_hotels(
            query=query,
            check_in_date=check_in_str,
            check_out_date=check_out_str,
            currency=self._currency,
        )

        offers = [
            offer
            for offer in (
                _normalize_property(raw, destination, check_in, check_out, self._currency, nights)
                for raw in (response_json.get("properties") or [])
            )
            if offer is not None
        ]

        if self._cache is not None:
            self._cache.set(cache_key, [_offer_to_dict(offer) for offer in offers])

        return offers

    def get_typical_total_price(self, destination: str, nights: int, month: int) -> float | None:
        # See module docstring "Typical price baseline": Google Hotels has
        # no price-insight equivalent, and we don't yet have our own
        # historical accommodation baseline. Honestly unknown, not guessed.
        return None


def _normalize_property(
    raw: dict[str, Any],
    destination: str,
    check_in: date,
    check_out: date,
    currency: str,
    nights: int,
) -> AccommodationOffer | None:
    """Turn one raw SerpApi `properties[]` entry into an AccommodationOffer.

    Returns None for a malformed/incomplete entry instead of raising - one
    bad entry in an otherwise-good list shouldn't discard the rest (same
    convention as serpapi_flight_provider.py's `_normalize_offer`).

    Defensive total-price normalization: SerpApi's documented schema
    reports both a per-stay `total_rate` and a `rate_per_night`, each with
    an `extracted_lowest` numeric field (already currency-converted to the
    requested `currency` - we trust that conversion, never parse the
    formatted `lowest` string ourselves; parsing a currency-symbol string
    is fragile and unnecessary when a clean number is already provided).
    Total price wins when present and parseable; otherwise it's derived as
    rate_per_night * nights. Neither present/parseable -> skip the entry.
    """
    try:
        name = raw["name"]
    except (KeyError, TypeError):
        return None

    total_price = _extracted_lowest(raw.get("total_rate"))
    if total_price is None:
        per_night = _extracted_lowest(raw.get("rate_per_night"))
        if per_night is None or nights <= 0:
            return None
        total_price = round(per_night * nights, 2)

    rating = raw.get("overall_rating")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    booking_link = raw.get("link")
    if not isinstance(booking_link, str):
        booking_link = None

    return AccommodationOffer(
        destination=destination,
        check_in=check_in,
        check_out=check_out,
        total_price=total_price,
        currency=currency,
        name=name,
        rating=rating,
        provider=_PROVIDER_NAME,
        booking_link=booking_link,
    )


def _extracted_lowest(rate_block: Any) -> float | None:
    if not isinstance(rate_block, dict):
        return None
    value = rate_block.get("extracted_lowest")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _offer_to_dict(offer: AccommodationOffer) -> dict[str, Any]:
    return {
        "destination": offer.destination,
        "check_in": offer.check_in.isoformat(),
        "check_out": offer.check_out.isoformat(),
        "total_price": offer.total_price,
        "currency": offer.currency,
        "name": offer.name,
        "rating": offer.rating,
        "provider": offer.provider,
        "booking_link": offer.booking_link,
    }


def _offer_from_dict(data: dict[str, Any]) -> AccommodationOffer:
    return AccommodationOffer(
        destination=data["destination"],
        check_in=date.fromisoformat(data["check_in"]),
        check_out=date.fromisoformat(data["check_out"]),
        total_price=data["total_price"],
        currency=data["currency"],
        name=data["name"],
        rating=data.get("rating"),
        provider=data["provider"],
        booking_link=data.get("booking_link"),
    )
