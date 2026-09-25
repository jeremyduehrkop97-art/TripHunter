"""Tests for SerpApiAccommodationProvider. All HTTP is faked via a fake
client - no real network calls anywhere in this file. The one exception to
"no real data" is `_REAL_PMI_RESPONSE`, loaded from a fixture captured by
exactly ONE controlled, human-approved live SerpApi call (see the fixture
file's own header comment and the module docstring in
serpapi_accommodation_provider.py) - even that fixture is replayed through
the fake client below, never fetched over the network in a test run.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from trip_hunter.caching import FileCache, hotel_search_cache_key
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.models import AccommodationOffer, DealType
from trip_hunter.providers.mock_flight_provider import MockFlightProvider
from trip_hunter.providers.serpapi_accommodation_provider import SerpApiAccommodationProvider

_CHECK_IN = date(2026, 10, 2)
_CHECK_OUT = date(2026, 10, 7)  # 5 nights

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_REAL_PMI_RESPONSE: dict[str, Any] = json.loads(
    (_FIXTURES_DIR / "serpapi_hotels_pmi.json").read_text(encoding="utf-8")
)


class _FakeClient:
    def __init__(self, response: dict[str, Any] | None = None):
        self.response = response if response is not None else {}
        self.call_count = 0
        self.last_kwargs: dict[str, Any] | None = None

    def search_hotels(self, **kwargs):
        self.call_count += 1
        self.last_kwargs = kwargs
        return self.response


_RESPONSE_WITH_TOTAL_RATE: dict[str, Any] = {
    "properties": [
        {
            "name": "Hotel Playa Sol",
            "total_rate": {"lowest": "€205", "extracted_lowest": 205},
            "rate_per_night": {"lowest": "€41", "extracted_lowest": 41},
            "overall_rating": 4.2,
        },
        {
            "name": "Ocean View Suites",
            "total_rate": {"lowest": "€610", "extracted_lowest": 610},
            "rate_per_night": {"lowest": "€122", "extracted_lowest": 122},
            "overall_rating": 4.6,
        },
    ]
}


def _provider(client: _FakeClient, cache: FileCache | None = None) -> SerpApiAccommodationProvider:
    return SerpApiAccommodationProvider(client=client, cache=cache)


# --- Normalization ---------------------------------------------------------


def test_normalizes_valid_properties_into_accommodation_offers():
    client = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert len(offers) == 2
    cheapest = min(offers, key=lambda o: o.total_price)
    assert isinstance(cheapest, AccommodationOffer)
    assert cheapest.name == "Hotel Playa Sol"
    assert cheapest.total_price == 205.0
    assert cheapest.currency == "EUR"
    assert cheapest.destination == "PMI"
    assert cheapest.check_in == _CHECK_IN
    assert cheapest.check_out == _CHECK_OUT
    assert cheapest.rating == 4.2
    assert cheapest.provider == "serpapi_google_hotels"


def test_prefers_total_rate_over_rate_per_night_times_nights():
    # rate_per_night * 5 nights would give 41*5=205 here too - use a
    # mismatched per-night rate to prove total_rate wins when both exist.
    response = {
        "properties": [
            {
                "name": "Hotel Playa Sol",
                "total_rate": {"extracted_lowest": 205},
                "rate_per_night": {"extracted_lowest": 999},
            }
        ]
    }
    client = _FakeClient(response=response)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert len(offers) == 1
    assert offers[0].total_price == 205.0


def test_falls_back_to_rate_per_night_times_nights_when_total_rate_missing():
    response = {
        "properties": [
            {
                "name": "Hotel Playa Sol",
                "rate_per_night": {"extracted_lowest": 41},
                # no total_rate at all
            }
        ]
    }
    client = _FakeClient(response=response)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert len(offers) == 1
    assert offers[0].total_price == 205.0  # 41 EUR/night * 5 nights


def test_falls_back_when_total_rate_is_unparseable():
    response = {
        "properties": [
            {
                "name": "Hotel Playa Sol",
                "total_rate": {"extracted_lowest": None},
                "rate_per_night": {"extracted_lowest": 41},
            }
        ]
    }
    client = _FakeClient(response=response)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert len(offers) == 1
    assert offers[0].total_price == 205.0


def test_entry_with_neither_total_rate_nor_rate_per_night_is_skipped_not_raised():
    response = {
        "properties": [
            {"name": "No Price Hotel"},
            _RESPONSE_WITH_TOTAL_RATE["properties"][0],
        ]
    }
    client = _FakeClient(response=response)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert len(offers) == 1
    assert offers[0].name == "Hotel Playa Sol"


def test_entry_missing_name_is_skipped_not_raised():
    response = {
        "properties": [
            {"total_rate": {"extracted_lowest": 100}},
            _RESPONSE_WITH_TOTAL_RATE["properties"][0],
        ]
    }
    client = _FakeClient(response=response)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert len(offers) == 1
    assert offers[0].name == "Hotel Playa Sol"


def test_missing_rating_defaults_to_none():
    response = {"properties": [{"name": "No Rating Hotel", "total_rate": {"extracted_lowest": 150}}]}
    client = _FakeClient(response=response)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert len(offers) == 1
    assert offers[0].rating is None


def test_empty_properties_return_empty_list():
    client = _FakeClient(response={"properties": []})
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert offers == []


def test_missing_properties_key_returns_empty_list():
    client = _FakeClient(response={})
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert offers == []


# --- Destination -> query mapping ------------------------------------------


def test_unmapped_destination_returns_empty_list_without_calling_client():
    client = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    provider = _provider(client)

    offers = provider.search_accommodations("XYZ", _CHECK_IN, _CHECK_OUT)

    assert offers == []
    assert client.call_count == 0


def test_bcn_is_mapped_for_the_daily_sampler_weekend_getaway_target():
    """Regression test: sampling_targets.py's HOTEL_TARGETS includes BCN -
    the default mapping must resolve it, not just the explicitly
    documented PMI example."""
    client = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    provider = _provider(client)

    provider.search_accommodations("BCN", _CHECK_IN, _CHECK_OUT)

    assert client.call_count == 1
    assert client.last_kwargs["query"] == "Barcelona, Spain"


def test_fco_is_mapped_for_the_daily_sampler_weekend_getaway_target():
    """Regression test: sampling_targets.py's HOTEL_TARGETS includes FCO
    (Rome) - the default mapping must resolve it too."""
    client = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    provider = _provider(client)

    provider.search_accommodations("FCO", _CHECK_IN, _CHECK_OUT)

    assert client.call_count == 1
    assert client.last_kwargs["query"] == "Rome, Italy"


def test_custom_destination_query_mapping_is_used():
    client = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    provider = SerpApiAccommodationProvider(
        client=client, destination_query={"AGP": "Malaga, Spain"}
    )

    provider.search_accommodations("AGP", _CHECK_IN, _CHECK_OUT)

    assert client.call_count == 1
    assert client.last_kwargs["query"] == "Malaga, Spain"


def test_query_resolved_from_destination_code():
    client = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    provider = _provider(client)

    provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert client.last_kwargs["query"] == "Palma de Mallorca, Spain"
    assert client.last_kwargs["check_in_date"] == "2026-10-02"
    assert client.last_kwargs["check_out_date"] == "2026-10-07"


# --- Typical price baseline -------------------------------------------------


def test_typical_total_price_is_always_none():
    provider = _provider(_FakeClient())
    assert provider.get_typical_total_price("PMI", 5, 10) is None


# --- Caching / credit safety -------------------------------------------------


def test_cache_hit_avoids_second_client_call(tmp_path):
    client = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    provider = _provider(client, cache=cache)

    first = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)
    second = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert client.call_count == 1
    assert len(first) == len(second) == 2


def test_cache_round_trip_preserves_offer_fields(tmp_path):
    client = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    provider = _provider(client, cache=cache)

    provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)
    cached_offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    cheapest = min(cached_offers, key=lambda o: o.total_price)
    assert cheapest.name == "Hotel Playa Sol"
    assert cheapest.total_price == 205.0
    assert cheapest.rating == 4.2
    assert cheapest.provider == "serpapi_google_hotels"


def test_cache_key_differs_by_currency(tmp_path):
    client_eur = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    client_usd = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)

    provider_eur = SerpApiAccommodationProvider(client=client_eur, cache=cache, currency="EUR")
    provider_usd = SerpApiAccommodationProvider(client=client_usd, cache=cache, currency="USD")

    provider_eur.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)
    provider_usd.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert client_eur.call_count == 1
    assert client_usd.call_count == 1


def test_hotel_and_flight_cache_keys_never_collide(tmp_path):
    from trip_hunter.caching import flight_search_cache_key

    hotel_key = hotel_search_cache_key("PMI", "2026-10-02", "2026-10-07", "EUR")
    flight_key = flight_search_cache_key("HAM", "PMI", "2026-10-02", "2026-10-07", "EUR")

    assert hotel_key != flight_key
    assert hotel_key.startswith("hotel:")
    assert flight_key.startswith("flight:")


# --- DealEngine integration: documents the remaining gap ------------------
# Even with a REAL accommodation provider wired in, COMBINED_TRIP_DROP still
# cannot trigger yet: get_typical_total_price() is honestly None (no Google
# Hotels price-insight equivalent, no accommodation history repository -
# see module docstring "Typical price baseline"), and DealEngine only
# attempts combine()/COMBINED_TRIP_DROP when an accommodation baseline is
# known. This is the next real blocker after this provider, not a bug here.


def test_real_accommodation_offer_is_attached_but_never_triggers_combined_trip_drop():
    flight_provider = MockFlightProvider()  # HAM->PMI: 79 EUR vs. 180 EUR baseline
    hotels_client = _FakeClient(response=_RESPONSE_WITH_TOTAL_RATE)
    accommodation_provider = SerpApiAccommodationProvider(client=hotels_client)
    engine = DealEngine(flight_provider=flight_provider, accommodation_provider=accommodation_provider)

    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 2),
        latest_departure=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
    )

    assert len(deals) == 1
    deal = deals[0]
    # The real, cheapest accommodation offer IS attached to the deal...
    assert deal.accommodation is not None
    assert deal.accommodation.name == "Hotel Playa Sol"
    assert deal.accommodation.provider == "serpapi_google_hotels"
    # ...but with no known baseline for it, so no hotel-level comparison
    # was possible and the deal stays flight-only.
    assert deal.expected_accommodation_price is None
    assert deal.deal_type is DealType.FLIGHT_DROP
    assert deal.deal_type is not DealType.COMBINED_TRIP_DROP


# --- Real fixture (1 controlled live SerpApi call, 2026-09-11) ------------
# tests/fixtures/serpapi_hotels_pmi.json: q="Palma de Mallorca, Spain",
# check_in=2026-10-02, check_out=2026-10-07, currency=EUR. 20 properties
# returned; 19 carry total_rate/rate_per_night, one ("Hotel Basilica") has
# neither - a real, observed case for the "skip, don't guess" fallback.


def test_real_fixture_normalizes_into_the_expected_number_of_offers():
    client = _FakeClient(response=_REAL_PMI_RESPONSE)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    # 20 properties in the raw fixture, 1 without any price data at all.
    assert len(_REAL_PMI_RESPONSE["properties"]) == 20
    assert len(offers) == 19


def test_real_fixture_property_without_any_rate_is_skipped_not_raised():
    client = _FakeClient(response=_REAL_PMI_RESPONSE)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert "Hotel Basilica" not in {offer.name for offer in offers}


def test_real_fixture_cheapest_offer_matches_expected_values():
    client = _FakeClient(response=_REAL_PMI_RESPONSE)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)
    cheapest = min(offers, key=lambda o: o.total_price)

    assert cheapest.name == "The Boc Hostels - City"
    assert cheapest.total_price == 457.0
    assert cheapest.currency == "EUR"
    assert cheapest.rating == 4.4
    assert cheapest.provider == "serpapi_google_hotels"


def test_real_fixture_offers_carry_a_booking_link():
    client = _FakeClient(response=_REAL_PMI_RESPONSE)
    provider = _provider(client)

    offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert all(offer.booking_link is not None for offer in offers)
    assert all(offer.booking_link.startswith("http") for offer in offers)


def test_real_fixture_survives_cache_round_trip_with_booking_link(tmp_path):
    client = _FakeClient(response=_REAL_PMI_RESPONSE)
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    provider = _provider(client, cache=cache)

    provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)
    cached_offers = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert client.call_count == 1
    cheapest = min(cached_offers, key=lambda o: o.total_price)
    assert cheapest.name == "The Boc Hostels - City"
    assert cheapest.booking_link == "https://thebochostels.com/hostal/the-boc-city/"


# --- shared sleeping / youth hostels are filtered out --------------------------


def _prop(name, price, *, description=None, type_="hotel", rating=4.2):
    prop = {"name": name, "total_rate": {"extracted_lowest": price}, "overall_rating": rating, "type": type_}
    if description is not None:
        prop["description"] = description
    return prop


_MIXED_RESPONSE = {
    "properties": [
        _prop("Sunny Dorms", 40, description="Hostel with 8-bed dormitory rooms"),
        _prop("Youth Hostel Palma", 45, description="Simple rooms"),
        _prop("Jugendherberge Mallorca", 50),
        _prop("Capsule Stay", 60, description="Modern capsule pods"),
        _prop("Beach Room Share", 55, type_="vacation rental", description="A shared room near the beach"),
        _prop("a&o Palma", 90, description="Private double rooms with ensuite bathroom and a shared lounge"),
        _prop("Hotel Playa Sol", 205, description="Stylish rooms & suites"),
    ]
}


def test_dorms_capsules_shared_rooms_and_youth_hostels_are_dropped_hybrids_kept():
    offers = _provider(_FakeClient(response=_MIXED_RESPONSE)).search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert [o.name for o in offers] == ["a&o Palma", "Hotel Playa Sol"]


def test_room_type_and_description_are_carried_on_the_offer():
    (offer, _) = _provider(_FakeClient(response=_MIXED_RESPONSE)).search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert offer.room_type == "hotel"
    assert "private double rooms" in offer.description.lower()


def test_real_fixture_still_yields_offers_after_filtering():
    offers = _provider(_FakeClient(response=_REAL_PMI_RESPONSE)).search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)
    assert offers and all(o.description is None or "dorm" not in o.description.lower() for o in offers)


def test_cache_keeps_everything_but_reads_are_filtered_with_one_live_call(tmp_path):
    client = _FakeClient(response=_MIXED_RESPONSE)
    provider = _provider(client, FileCache(cache_dir=tmp_path, ttl_seconds=3600))

    first = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)
    second = provider.search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)

    assert client.call_count == 1
    assert [o.name for o in first] == [o.name for o in second] == ["a&o Palma", "Hotel Playa Sol"]


def test_old_cache_entries_without_the_new_fields_still_load(tmp_path):
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    cache.set(
        hotel_search_cache_key("PMI", _CHECK_IN.isoformat(), _CHECK_OUT.isoformat(), "EUR"),
        [{"destination": "PMI", "check_in": "2026-10-02", "check_out": "2026-10-07", "total_price": 205.0,
          "currency": "EUR", "name": "Old Entry", "rating": 4.0, "provider": "serpapi_google_hotels"}],
    )
    (offer,) = _provider(_FakeClient(), cache).search_accommodations("PMI", _CHECK_IN, _CHECK_OUT)
    assert offer.name == "Old Entry" and offer.description is None
