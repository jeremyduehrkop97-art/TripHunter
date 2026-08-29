from __future__ import annotations

from datetime import date
from typing import Any

from vacation_hunter.caching import FileCache
from vacation_hunter.models import FlightOffer, PriceInsight
from vacation_hunter.providers.serpapi_flight_provider import SerpApiGoogleFlightsProvider


class _FakeClient:
    def __init__(self, response: dict[str, Any] | None = None):
        self.response = response if response is not None else {}
        self.call_count = 0

    def search_flights(self, **kwargs):
        self.call_count += 1
        return self.response


def _leg(dep_id: str, dep_time: str, arr_id: str, arr_time: str, airline: str = "Eurowings"):
    return {
        "departure_airport": {"id": dep_id, "time": dep_time},
        "arrival_airport": {"id": arr_id, "time": arr_time},
        "airline": airline,
    }


_RESPONSE_WITH_INSIGHT: dict[str, Any] = {
    "best_flights": [
        {
            "price": 89,
            "flights": [
                _leg("HAM", "2026-10-02 06:15", "PMI", "2026-10-02 09:05"),
                _leg("PMI", "2026-10-07 10:00", "HAM", "2026-10-07 12:50"),
            ],
        },
        {
            "price": 120,
            "flights": [
                _leg("HAM", "2026-10-02 08:00", "PMI", "2026-10-02 10:55", airline="Ryanair"),
                _leg("PMI", "2026-10-07 14:00", "HAM", "2026-10-07 16:55", airline="Ryanair"),
            ],
        },
    ],
    "other_flights": [],
    "price_insights": {
        "lowest_price": 89,
        "price_level": "low",
        "typical_price_range": [160, 220],
    },
}


def test_normalizes_valid_offers_into_flight_offers():
    client = _FakeClient(response=_RESPONSE_WITH_INSIGHT)
    provider = SerpApiGoogleFlightsProvider(client=client)

    offers = provider.search_flights(
        "HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7)
    )

    assert len(offers) == 2
    cheapest = min(offers, key=lambda o: o.price)
    assert isinstance(cheapest, FlightOffer)
    assert cheapest.price == 89.0
    assert cheapest.origin == "HAM"
    assert cheapest.destination == "PMI"
    assert cheapest.departure_date == date(2026, 10, 2)
    assert cheapest.return_date == date(2026, 10, 7)
    assert cheapest.departure_time == "06:15"
    assert cheapest.return_time == "12:50"
    assert cheapest.stops == 0
    assert cheapest.airline == "Eurowings"
    assert cheapest.provider == "serpapi_google_flights"
    assert cheapest.currency == "EUR"
    assert cheapest.booking_link is None


def test_price_insight_is_extracted_when_present():
    client = _FakeClient(response=_RESPONSE_WITH_INSIGHT)
    provider = SerpApiGoogleFlightsProvider(client=client)

    insight = provider.get_price_insight("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 7))

    assert isinstance(insight, PriceInsight)
    assert insight.current_price == 89.0
    assert insight.typical_price_low == 160.0
    assert insight.typical_price_high == 220.0
    assert insight.price_level == "low"
    assert insight.source == "google_flights"


def test_price_insight_is_none_when_missing_from_response():
    response = {"best_flights": _RESPONSE_WITH_INSIGHT["best_flights"], "other_flights": []}
    client = _FakeClient(response=response)
    provider = SerpApiGoogleFlightsProvider(client=client)

    insight = provider.get_price_insight("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 7))

    assert insight is None


def test_empty_results_return_empty_list():
    client = _FakeClient(response={"best_flights": [], "other_flights": []})
    provider = SerpApiGoogleFlightsProvider(client=client)

    offers = provider.search_flights("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2))

    assert offers == []


def test_malformed_single_offer_is_skipped_not_raised():
    response = {
        "best_flights": [
            {"price": "not-a-number", "flights": _RESPONSE_WITH_INSIGHT["best_flights"][0]["flights"]},
            _RESPONSE_WITH_INSIGHT["best_flights"][1],
        ],
        "other_flights": [],
    }
    client = _FakeClient(response=response)
    provider = SerpApiGoogleFlightsProvider(client=client)

    offers = provider.search_flights(
        "HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7)
    )

    assert len(offers) == 1
    assert offers[0].price == 120.0


def test_typical_price_is_always_none():
    provider = SerpApiGoogleFlightsProvider(client=_FakeClient())
    assert provider.get_typical_price("HAM", "PMI", 10) is None


def test_cache_hit_avoids_second_client_call(tmp_path):
    client = _FakeClient(response=_RESPONSE_WITH_INSIGHT)
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    provider = SerpApiGoogleFlightsProvider(client=client, cache=cache)

    first = provider.search_flights(
        "HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7)
    )
    second = provider.search_flights(
        "HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7)
    )

    assert client.call_count == 1
    assert len(first) == len(second) == 2


def test_get_price_insight_reuses_search_flights_cache_entry(tmp_path):
    """Credit safety: a demo run that calls search_flights() and then
    get_price_insight() for the same search must only hit the API once."""
    client = _FakeClient(response=_RESPONSE_WITH_INSIGHT)
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    provider = SerpApiGoogleFlightsProvider(client=client, cache=cache)

    provider.search_flights("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7))
    insight = provider.get_price_insight("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 7))

    assert client.call_count == 1
    assert insight is not None
    assert insight.typical_price_low == 160.0


def test_cache_key_differs_by_currency(tmp_path):
    client_eur = _FakeClient(response=_RESPONSE_WITH_INSIGHT)
    client_usd = _FakeClient(response=_RESPONSE_WITH_INSIGHT)
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)

    provider_eur = SerpApiGoogleFlightsProvider(client=client_eur, cache=cache, currency="EUR")
    provider_usd = SerpApiGoogleFlightsProvider(client=client_usd, cache=cache, currency="USD")

    provider_eur.search_flights("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7))
    provider_usd.search_flights("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7))

    assert client_eur.call_count == 1
    assert client_usd.call_count == 1
