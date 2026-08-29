"""SerpApiGoogleFlightsProvider: normalizes SerpApi's Google Flights results
into our internal FlightOffer and PriceInsight models.

The rest of the system (deal detection, scoring, DealEngine) never sees
SerpApi's or Google Flights' JSON shape - only FlightOffer/PriceInsight.
See docs/ARCHITECTURE.md.

Credit safety: `search_flights` and `get_price_insight` both go through the
same cached `_search`, so a single demo run (search, then look up the price
insight for the cheapest offer) makes at most ONE live SerpApi request, not
two. See "API Credit Safety" in docs/ARCHITECTURE.md.

Verified against a real response (2026-08, HAM->PMI round trip): SerpApi's
`flights` array for a round-trip result only ever contained the outbound
leg - no return leg to be found by `_split_outbound_return`. We therefore
treat "no return leg detected" as the expected case for round trips, not
an edge case: `return_date` falls back to the caller's requested return
date (never to the departure date - that would silently imply a same-day
return), and `return_time` stays None to make clear we don't have SerpApi's
actual return-flight time. If a future response *does* include a detectable
return leg, `_split_outbound_return` still picks it up correctly.

Price completeness (MVP 0.2.2): SerpApi's Google Flights engine returns
round-trip results in two steps - an initial search yields outbound options
plus a `departure_token` per option, which must be exchanged in a SECOND
request to get matching return options. We deliberately do NOT make that
second, credit-costly request (see "API Credit Safety" in
docs/ARCHITECTURE.md), and after researching SerpApi's own docs plus
several independent third-party sources, none gave an authoritative,
unambiguous confirmation of whether the `price` shown at this first step
already represents the full round-trip total. Rather than guess, every
round-trip offer from this provider is marked
`price_confirmed_complete=False` - see "Price Completeness" in
docs/PRODUCT_SPEC.md for what that triggers in the deal engine. A one-way
search has no such ambiguity and keeps `price_confirmed_complete=True`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from vacation_hunter.caching import FileCache, flight_search_cache_key
from vacation_hunter.models import FlightOffer, PriceInsight
from vacation_hunter.providers.flight_provider import FlightProvider
from vacation_hunter.providers.serpapi_client import SerpApiClient

_PROVIDER_NAME = "serpapi_google_flights"
_INSIGHT_SOURCE = "google_flights"


class SerpApiGoogleFlightsProvider(FlightProvider):
    def __init__(
        self,
        client: SerpApiClient,
        cache: FileCache | None = None,
        currency: str = "EUR",
    ) -> None:
        self._client = client
        self._cache = cache
        self._currency = currency

    def search_flights(
        self,
        origin: str,
        destination: str,
        earliest_departure: date,
        latest_departure: date,
        return_date: date | None = None,
    ) -> list[FlightOffer]:
        offers, _ = self._search(origin, destination, earliest_departure, return_date)
        return offers

    def get_typical_price(self, origin: str, destination: str, month: int) -> float | None:
        # Google Flights/SerpApi has no concept of our own historical
        # baseline - use get_price_insight() instead. See "Baseline Problem"
        # in docs/PRODUCT_SPEC.md.
        return None

    def get_price_insight(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date | None,
    ) -> PriceInsight | None:
        _, insight = self._search(origin, destination, departure_date, return_date)
        return insight

    def _search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date | None,
    ) -> tuple[list[FlightOffer], PriceInsight | None]:
        departure_date_str = departure_date.isoformat()
        return_date_str = return_date.isoformat() if return_date else None

        cache_key = flight_search_cache_key(
            origin, destination, departure_date_str, return_date_str or "", self._currency
        )
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                offers = [_offer_from_dict(raw) for raw in cached["offers"]]
                insight = _insight_from_dict(cached["price_insight"]) if cached["price_insight"] else None
                return offers, insight

        response_json = self._client.search_flights(
            origin=origin,
            destination=destination,
            outbound_date=departure_date_str,
            return_date=return_date_str,
            currency=self._currency,
        )

        offers = [
            offer
            for offer in (
                _normalize_offer(raw, origin, destination, return_date, self._currency)
                for raw in _all_flight_entries(response_json)
            )
            if offer is not None
        ]
        insight = _extract_price_insight(response_json)

        if self._cache is not None:
            self._cache.set(
                cache_key,
                {
                    "offers": [_offer_to_dict(offer) for offer in offers],
                    "price_insight": _insight_to_dict(insight) if insight else None,
                },
            )

        return offers, insight


def _all_flight_entries(response_json: dict[str, Any]) -> list[dict[str, Any]]:
    best = response_json.get("best_flights") or []
    other = response_json.get("other_flights") or []
    return [*best, *other]


def _split_outbound_return(
    flights: list[dict[str, Any]], destination: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    for index in range(1, len(flights)):
        if flights[index].get("departure_airport", {}).get("id") == destination:
            return flights[:index], flights[index:]
    return flights, None


def _normalize_offer(
    raw: dict[str, Any],
    origin: str,
    destination: str,
    requested_return_date: date | None,
    currency: str,
) -> FlightOffer | None:
    """Turn one raw SerpApi flight-offer object into a FlightOffer.

    Returns None for a malformed/incomplete offer instead of raising - one
    bad entry in a list of otherwise-good offers shouldn't discard the rest.
    """
    try:
        price = float(raw["price"])
        flights = raw["flights"]
        if not flights:
            return None

        outbound_legs, return_legs = _split_outbound_return(flights, destination)
        if not outbound_legs:
            return None

        first_leg = outbound_legs[0]
        departure_date, departure_time = _parse_datetime(first_leg["departure_airport"]["time"])
        stops = len(outbound_legs) - 1

        if return_legs:
            last_return_leg = return_legs[-1]
            return_date, return_time = _parse_datetime(last_return_leg["arrival_airport"]["time"])
        elif requested_return_date is not None:
            # SerpApi's `flights` array for a round-trip result has only
            # ever been observed to contain the outbound leg - no return
            # leg to detect. Rather than guessing at a split point (or,
            # worse, silently collapsing to the departure date), use the
            # return date the caller actually asked for. return_time stays
            # None so callers can tell we don't know the actual return
            # flight time from this data.
            return_date, return_time = requested_return_date, None
        else:
            # A genuine one-way search: no return leg expected.
            return_date, return_time = departure_date, None

        airline = first_leg.get("airline") or "Unknown"
    except (KeyError, IndexError, TypeError, ValueError):
        return None

    return FlightOffer(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        price=price,
        currency=currency,
        airline=airline,
        stops=stops,
        provider=_PROVIDER_NAME,
        departure_time=departure_time,
        return_time=return_time,
        booking_link=None,  # Not resolved: would need a second, credit-costly
        # SerpApi request per offer. See "API Credit Safety" in docs/ARCHITECTURE.md.
        # Round trip: `price` is from step 1 (before a departure_token
        # follow-up), completeness unconfirmed - see module docstring and
        # "Price Completeness" in docs/PRODUCT_SPEC.md. One-way has no such
        # ambiguity.
        price_confirmed_complete=requested_return_date is None,
    )


def _parse_datetime(value: str) -> tuple[date, str]:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return parsed.date(), parsed.strftime("%H:%M")


def _extract_price_insight(response_json: dict[str, Any]) -> PriceInsight | None:
    raw_insight = response_json.get("price_insights")
    if not raw_insight:
        return None

    typical_low: float | None = None
    typical_high: float | None = None
    typical_range = raw_insight.get("typical_price_range")
    if isinstance(typical_range, (list, tuple)) and len(typical_range) == 2:
        try:
            typical_low = float(typical_range[0])
            typical_high = float(typical_range[1])
        except (TypeError, ValueError):
            typical_low = typical_high = None

    provider_lowest_price: float | None = None
    raw_lowest_price = raw_insight.get("lowest_price")
    if raw_lowest_price is not None:
        try:
            provider_lowest_price = float(raw_lowest_price)
        except (TypeError, ValueError):
            provider_lowest_price = None

    price_level = raw_insight.get("price_level")

    if (
        provider_lowest_price is None
        and typical_low is None
        and typical_high is None
        and not price_level
    ):
        return None

    return PriceInsight(
        provider_lowest_price=provider_lowest_price,
        typical_price_low=typical_low,
        typical_price_high=typical_high,
        price_level=price_level,
        source=_INSIGHT_SOURCE,
    )


def _offer_to_dict(offer: FlightOffer) -> dict[str, Any]:
    return {
        "origin": offer.origin,
        "destination": offer.destination,
        "departure_date": offer.departure_date.isoformat(),
        "return_date": offer.return_date.isoformat(),
        "price": offer.price,
        "currency": offer.currency,
        "airline": offer.airline,
        "stops": offer.stops,
        "provider": offer.provider,
        "departure_time": offer.departure_time,
        "return_time": offer.return_time,
        "booking_link": offer.booking_link,
        "price_confirmed_complete": offer.price_confirmed_complete,
    }


def _offer_from_dict(data: dict[str, Any]) -> FlightOffer:
    return FlightOffer(
        origin=data["origin"],
        destination=data["destination"],
        departure_date=date.fromisoformat(data["departure_date"]),
        return_date=date.fromisoformat(data["return_date"]),
        price=data["price"],
        currency=data["currency"],
        airline=data["airline"],
        stops=data["stops"],
        provider=data["provider"],
        departure_time=data.get("departure_time"),
        return_time=data.get("return_time"),
        booking_link=data.get("booking_link"),
        # Older cache entries (written before MVP 0.2.2) predate this field.
        # Defaulting a missing value to True would silently mask an
        # unconfirmed round-trip price, so default to False instead - the
        # safer assumption - and let the TTL naturally clear stale entries.
        price_confirmed_complete=data.get("price_confirmed_complete", False),
    )


def _insight_to_dict(insight: PriceInsight) -> dict[str, Any]:
    return {
        "provider_lowest_price": insight.provider_lowest_price,
        "typical_price_low": insight.typical_price_low,
        "typical_price_high": insight.typical_price_high,
        "price_level": insight.price_level,
        "source": insight.source,
    }


def _insight_from_dict(data: dict[str, Any]) -> PriceInsight:
    return PriceInsight(
        provider_lowest_price=data.get("provider_lowest_price"),
        typical_price_low=data.get("typical_price_low"),
        typical_price_high=data.get("typical_price_high"),
        price_level=data.get("price_level"),
        source=data.get("source", _INSIGHT_SOURCE),
    )
