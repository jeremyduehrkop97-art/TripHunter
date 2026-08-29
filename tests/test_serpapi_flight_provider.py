from __future__ import annotations

from datetime import date
from typing import Any

from vacation_hunter.caching import FileCache
from vacation_hunter.engine.deal_engine import DealEngine
from vacation_hunter.models import FlightOffer, PriceInsight
from vacation_hunter.providers.null_accommodation_provider import NullAccommodationProvider
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


# Regression fixture, shaped after a real SerpApi response observed on
# 2026-08 for a HAM -> PMI round trip (route/times/airline anonymized -
# structure and rough magnitudes are what matters here): for a round-trip
# search, `flights` contained only the outbound leg's segment(s) - no
# return leg to be found. See the note in serpapi_flight_provider.py.
_REAL_SHAPE_ROUND_TRIP_RESPONSE: dict[str, Any] = {
    "best_flights": [
        {
            "price": 184,
            "flights": [
                _leg("HAM", "2026-10-02 21:50", "BCN", "2026-10-03 00:05", airline="Vueling"),
                _leg("BCN", "2026-10-03 08:30", "PMI", "2026-10-03 09:35", airline="Vueling"),
            ],
        },
    ],
    "other_flights": [],
    "price_insights": {
        "lowest_price": 232,
        "price_level": "typical",
        "typical_price_range": [205, 385],
    },
}


def test_round_trip_response_with_outbound_only_flights_uses_requested_return_date():
    """Regression test for the real-world response shape: when SerpApi's
    `flights` array doesn't include a detectable return leg, return_date
    must fall back to the date the caller actually asked for - never to the
    departure date, which would silently imply a same-day return."""
    client = _FakeClient(response=_REAL_SHAPE_ROUND_TRIP_RESPONSE)
    provider = SerpApiGoogleFlightsProvider(client=client)

    offers = provider.search_flights(
        "HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), date(2026, 10, 7)
    )

    assert len(offers) == 1
    offer = offers[0]
    assert offer.price == 184.0
    assert offer.departure_date == date(2026, 10, 2)
    assert offer.departure_time == "21:50"
    # The requested return date, NOT the departure date and NOT a guessed split.
    assert offer.return_date == date(2026, 10, 7)
    assert offer.return_time is None
    # Both legs in `flights` belong to the (1-stop) outbound journey.
    assert offer.stops == 1


def test_one_way_search_still_falls_back_to_departure_date():
    """A genuine one-way search (no return_date requested) keeps the old,
    correct fallback: return_date == departure_date, return_time is None."""
    response = {
        "best_flights": [
            {
                "price": 99,
                "flights": [_leg("HAM", "2026-10-02 06:15", "PMI", "2026-10-02 09:05")],
            }
        ],
        "other_flights": [],
    }
    client = _FakeClient(response=response)
    provider = SerpApiGoogleFlightsProvider(client=client)

    offers = provider.search_flights("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2))

    assert len(offers) == 1
    assert offers[0].return_date == date(2026, 10, 2)
    assert offers[0].return_time is None
    assert offers[0].stops == 0


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


def test_deal_engine_run_makes_only_one_live_call_end_to_end(tmp_path):
    """Regression test for a real credit-safety bug: before the return_date
    fix, DealEngine's price-insight fallback used flight.return_date (which
    had collapsed to the departure date) to look up the price insight,
    producing a DIFFERENT cache key than search_flights() used and
    triggering a second, unintended live SerpApi request - confirmed via a
    real run on 2026-08 (two cache files written ~3s apart for one demo
    invocation). With the fix, both calls resolve to the same cache key."""
    client = _FakeClient(response=_REAL_SHAPE_ROUND_TRIP_RESPONSE)
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=3600)
    provider = SerpApiGoogleFlightsProvider(client=client, cache=cache)
    engine = DealEngine(
        flight_provider=provider, accommodation_provider=NullAccommodationProvider()
    )

    deals = engine.find_trip_deals(
        origin="HAM",
        destination="PMI",
        earliest_departure=date(2026, 10, 2),
        latest_departure=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
    )

    assert client.call_count == 1
    assert len(deals) == 1
    assert deals[0].flight.return_date == date(2026, 10, 7)


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
