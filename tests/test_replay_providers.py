from __future__ import annotations

from datetime import date, datetime, timezone

from trip_hunter.models import AccommodationObservation, PriceObservation, TripType
from trip_hunter.replay_providers import ReplayAccommodationProvider, ReplayFlightProvider

_FLIGHT_OBSERVATION = PriceObservation(
    origin="HAM", destination="PMI", departure_date=date(2026, 10, 2), return_date=date(2026, 10, 7),
    trip_type=TripType.ROUND_TRIP, price=184.0, currency="EUR", provider="serpapi_google_flights",
    stops=1, airline="Vueling", observed_at=datetime(2026, 9, 12, 20, 14, tzinfo=timezone.utc),
)

_HOTEL_OBSERVATION = AccommodationObservation(
    destination="PMI", check_in=date(2026, 10, 2), check_out=date(2026, 10, 7),
    price=327.0, currency="EUR", provider="serpapi_google_hotels", name="The Boc Hostels - City",
    observed_at=datetime(2026, 9, 17, 8, 53, tzinfo=timezone.utc),
)


def test_replay_flight_provider_returns_exactly_one_offer_matching_the_observation():
    provider = ReplayFlightProvider(_FLIGHT_OBSERVATION)

    offers = provider.search_flights("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 2), return_date=date(2026, 10, 7))

    assert len(offers) == 1
    offer = offers[0]
    assert offer.price == 184.0
    assert offer.airline == "Vueling"
    assert offer.stops == 1
    assert offer.provider == "serpapi_google_flights"
    assert offer.price_confirmed_complete is True


def test_replay_flight_provider_ignores_the_search_arguments():
    """It always replays the same stored observation regardless of what's
    asked for - callers are responsible for only using it for the matching
    route/dates."""
    provider = ReplayFlightProvider(_FLIGHT_OBSERVATION)

    offers = provider.search_flights("XXX", "YYY", date(2020, 1, 1), date(2020, 1, 1))

    assert len(offers) == 1
    assert offers[0].origin == "HAM"


def test_replay_flight_provider_has_no_typical_price_or_insight():
    provider = ReplayFlightProvider(_FLIGHT_OBSERVATION)

    assert provider.get_typical_price("HAM", "PMI", 10) is None
    assert provider.get_price_insight("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 7)) is None


def test_replay_accommodation_provider_returns_exactly_one_offer():
    provider = ReplayAccommodationProvider(_HOTEL_OBSERVATION)

    offers = provider.search_accommodations("PMI", date(2026, 10, 2), date(2026, 10, 7))

    assert len(offers) == 1
    offer = offers[0]
    assert offer.total_price == 327.0
    assert offer.name == "The Boc Hostels - City"
    assert offer.provider == "serpapi_google_hotels"


def test_replay_accommodation_provider_with_none_returns_empty_list():
    provider = ReplayAccommodationProvider(None)

    offers = provider.search_accommodations("PMI", date(2026, 10, 2), date(2026, 10, 7))

    assert offers == []


def test_replay_accommodation_provider_has_no_typical_total_price():
    provider = ReplayAccommodationProvider(_HOTEL_OBSERVATION)

    assert provider.get_typical_total_price("PMI", 5, 10) is None
