"""SerpApiGoogleFlightsProvider: normalizes SerpApi's Google Flights results
into our internal FlightOffer and PriceInsight models.

The rest of the system (deal detection, scoring, DealEngine) never sees
SerpApi's or Google Flights' JSON shape - only FlightOffer/PriceInsight.
See docs/ARCHITECTURE.md.

Credit safety: `search_flights` and `get_price_insight` both go through the
same cached `_search`, so a single demo run (search, then look up the price
insight for the cheapest offer) makes at most ONE live SerpApi request, not
two. See "API Credit Safety" in docs/ARCHITECTURE.md.

Assumption flagged for verification against a real response: SerpApi does
not label which legs of a round-trip result are the return leg, so we
detect the split by finding the first leg that departs from our searched
destination (i.e. the flight home). If that assumption turns out to be
wrong once tested against real data, only `_split_outbound_return` needs
to change - everything downstream is unaffected.
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

        has_return_leg = return_date is not None
        offers = [
            offer
            for offer in (
                _normalize_offer(raw, origin, destination, has_return_leg, self._currency)
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
    has_return_leg: bool,
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
        else:
            # Either a one-way search, or we couldn't identify a distinct
            # return leg in the response - fall back to the outbound date
            # rather than guessing at a split point. The missing return_time
            # makes it clear no return leg was found.
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

    current_price: float | None = None
    raw_current_price = raw_insight.get("lowest_price")
    if raw_current_price is not None:
        try:
            current_price = float(raw_current_price)
        except (TypeError, ValueError):
            current_price = None

    price_level = raw_insight.get("price_level")

    if current_price is None and typical_low is None and typical_high is None and not price_level:
        return None

    return PriceInsight(
        current_price=current_price,
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
    )


def _insight_to_dict(insight: PriceInsight) -> dict[str, Any]:
    return {
        "current_price": insight.current_price,
        "typical_price_low": insight.typical_price_low,
        "typical_price_high": insight.typical_price_high,
        "price_level": insight.price_level,
        "source": insight.source,
    }


def _insight_from_dict(data: dict[str, Any]) -> PriceInsight:
    return PriceInsight(
        current_price=data.get("current_price"),
        typical_price_low=data.get("typical_price_low"),
        typical_price_high=data.get("typical_price_high"),
        price_level=data.get("price_level"),
        source=data.get("source", _INSIGHT_SOURCE),
    )
