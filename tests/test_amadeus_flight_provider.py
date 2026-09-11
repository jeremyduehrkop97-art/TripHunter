from __future__ import annotations

from datetime import date
from typing import Any

from trip_hunter.caching import FileCache
from trip_hunter.models import FlightOffer
from trip_hunter.providers.amadeus_flight_provider import AmadeusFlightProvider


class _FakeClient:
    def __init__(self, offers: list[dict[str, Any]] | None = None):
        self.offers = offers if offers is not None else []
        self.call_count = 0

    def search_flight_offers(self, **kwargs):
        self.call_count += 1
        return self.offers


_RAW_OFFER = {
    "price": {"total": "89.00", "currency": "EUR"},
    "itineraries": [
        {
            "segments": [
                {
                    "departure": {"iataCode": "HAM", "at": "2026-10-02T06:15:00"},
                    "arrival": {"iataCode": "PMI", "at": "2026-10-02T09:05:00"},
                    "carrierCode": "X3",
                }
            ]
        },
        {
            "segments": [
                {
                    "departure": {"iataCode": "PMI", "at": "2026-10-07T10:00:00"},
                    "arrival": {"iataCode": "HAM", "at": "2026-10-07T12:50:00"},
                    "carrierCode": "X3",
                }
            ]
        },
    ],
    "validatingAirlineCodes": ["X3"],
}


def test_normalizes_valid_offer_into_flight_offer():
    client = _FakeClient(offers=[_RAW_OFFER])
    provider = AmadeusFlightProvider(client=client)

    offers = provider.search_flights(
        "HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7)
    )

    assert len(offers) == 1
    offer = offers[0]
    assert isinstance(offer, FlightOffer)
    assert offer.price == 89.0
    assert offer.currency == "EUR"
    assert offer.origin == "HAM"
    assert offer.destination == "PMI"
    assert offer.departure_date == date(2026, 10, 2)
    assert offer.return_date == date(2026, 10, 7)
    assert offer.departure_time == "06:15"
    assert offer.return_time == "12:50"
    assert offer.stops == 0
    assert offer.airline == "X3"
    assert offer.provider == "amadeus"


def test_malformed_offer_is_skipped_not_raised():
    malformed = {"price": {"total": "not-a-number", "currency": "EUR"}, "itineraries": []}
    client = _FakeClient(offers=[malformed])
    provider = AmadeusFlightProvider(client=client)

    offers = provider.search_flights("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2))

    assert offers == []


def test_offer_missing_fields_is_skipped_not_raised():
    missing_price = {"itineraries": _RAW_OFFER["itineraries"]}
    client = _FakeClient(offers=[missing_price])
    provider = AmadeusFlightProvider(client=client)

    offers = provider.search_flights("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2))

    assert offers == []


def test_empty_results_return_empty_list():
    client = _FakeClient(offers=[])
    provider = AmadeusFlightProvider(client=client)

    offers = provider.search_flights("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2))

    assert offers == []


def test_typical_price_is_always_none():
    provider = AmadeusFlightProvider(client=_FakeClient())
    assert provider.get_typical_price("HAM", "PMI", 10) is None


def test_cache_hit_avoids_second_client_call(tmp_path):
    client = _FakeClient(offers=[_RAW_OFFER])
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    provider = AmadeusFlightProvider(client=client, cache=cache)

    first = provider.search_flights(
        "HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7)
    )
    second = provider.search_flights(
        "HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7)
    )

    assert client.call_count == 1
    assert len(first) == len(second) == 1
    assert first[0].price == second[0].price
