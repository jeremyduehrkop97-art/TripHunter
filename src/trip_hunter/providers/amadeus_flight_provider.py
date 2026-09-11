"""AmadeusFlightProvider: normalizes Amadeus Flight Offers Search results
into our internal FlightOffer model.

The rest of the system (deal detection, scoring, DealEngine) never sees
Amadeus' JSON shape - only FlightOffer. See docs/ARCHITECTURE.md.

Amadeus has no concept of a "typical"/baseline price - it only returns
current offers, so `get_typical_price` always returns None here. See
"Baseline Problem" in docs/PRODUCT_SPEC.md.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from trip_hunter.caching import FileCache, flight_search_cache_key
from trip_hunter.models import FlightOffer
from trip_hunter.providers.amadeus_client import AmadeusClient
from trip_hunter.providers.flight_provider import FlightProvider

_PROVIDER_NAME = "amadeus"


class AmadeusFlightProvider(FlightProvider):
    def __init__(self, client: AmadeusClient, cache: FileCache | None = None) -> None:
        self._client = client
        self._cache = cache

    def search_flights(
        self,
        origin: str,
        destination: str,
        earliest_departure: date,
        latest_departure: date,
        return_date: date | None = None,
    ) -> list[FlightOffer]:
        # Amadeus searches a single exact departure date, not a range. We use
        # `earliest_departure` as that date; scanning multiple dates per call
        # is out of scope for MVP 0.2 (see docs/PRODUCT_SPEC.md).
        departure_date_str = earliest_departure.isoformat()
        return_date_str = return_date.isoformat() if return_date else None

        cache_key = flight_search_cache_key(
            origin, destination, departure_date_str, return_date_str or ""
        )
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return [_offer_from_dict(raw) for raw in cached]

        raw_offers = self._client.search_flight_offers(
            origin=origin,
            destination=destination,
            departure_date=departure_date_str,
            return_date=return_date_str,
        )

        offers = [
            offer
            for offer in (_normalize_offer(raw, origin, destination) for raw in raw_offers)
            if offer is not None
        ]

        if self._cache is not None:
            self._cache.set(cache_key, [_offer_to_dict(offer) for offer in offers])

        return offers

    def get_typical_price(self, origin: str, destination: str, month: int) -> float | None:
        return None


def _normalize_offer(raw: dict[str, Any], origin: str, destination: str) -> FlightOffer | None:
    """Turn one raw Amadeus flight-offer object into a FlightOffer.

    Returns None for a malformed/incomplete offer instead of raising - one
    bad entry in a list of otherwise-good offers shouldn't discard the rest.
    """
    try:
        price_block = raw["price"]
        price = float(price_block["total"])
        currency = price_block["currency"]

        itineraries = raw["itineraries"]
        outbound_segments = itineraries[0]["segments"]
        first_segment = outbound_segments[0]
        stops = len(outbound_segments) - 1

        departure_date, departure_time = _split_datetime(first_segment["departure"]["at"])

        if len(itineraries) > 1:
            inbound_segments = itineraries[1]["segments"]
            return_date, return_time = _split_datetime(inbound_segments[-1]["arrival"]["at"])
        else:
            # One-way result: no return leg. We still need a return_date for
            # the FlightOffer model, so fall back to the outbound date - the
            # missing return_time makes it clear no return leg was found.
            return_date, return_time = departure_date, None

        airline = first_segment.get("carrierCode")
        validating_airlines = raw.get("validatingAirlineCodes") or []
        if validating_airlines:
            airline = validating_airlines[0]
        if not airline:
            airline = "Unknown"
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
        booking_link=None,
    )


def _split_datetime(value: str) -> tuple[date, str]:
    parsed = datetime.fromisoformat(value)
    return parsed.date(), parsed.strftime("%H:%M")


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
