"""Room-type / hostel filtering and best-hotel selection."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trip_hunter.accommodation_price_history_repository import observation_from_accommodation_search_results
from trip_hunter.engine.hotel_filter import MIN_HOTEL_RATING, best_accommodation, is_acceptable_stay, meets_min_rating
from trip_hunter.models import AccommodationComparisonGroup, AccommodationOffer

_IN, _OUT = date(2026, 10, 2), date(2026, 10, 4)


def _offer(price=100.0, *, name="Hotel X", rating=4.2, room_type="hotel", description=None) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI", check_in=_IN, check_out=_OUT, total_price=price, currency="EUR",
        name=name, rating=rating, provider="test", room_type=room_type, description=description,
    )


# --- shared sleeping is excluded -----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Cozy hostel with 8-bed dorm rooms",
        "Mixed Dormitory in the old town",
        "Female dormitories and private rooms",
        "Sleep in a bunk bed near the beach",
        "Bunk-beds for backpackers",
        "Modern capsule hotel",
        "Capsules with privacy curtain",
        "Bett im Schlafsaal ab 15 EUR",
        "Betten im Schlafsaal",
        "Schlafsaal mit 6 Betten",
        "Günstige Mehrbettzimmer",
        "Shared room in a flat",
        "Shared dorm with lockers",
        "The beds are shared",
    ],
)
def test_shared_sleeping_in_the_description_is_excluded(text):
    assert not is_acceptable_stay(_offer(description=text))


@pytest.mark.parametrize("room_type", ["dorm", "Dormitory", "shared room", "capsule"])
def test_shared_sleeping_in_the_room_type_is_excluded(room_type):
    assert not is_acceptable_stay(_offer(room_type=room_type))


@pytest.mark.parametrize(
    "text",
    [
        "Stylish rooms & suites in a relaxed adults-only hotel with a restaurant.",
        "Private double rooms with a shared lounge and shared kitchen.",  # 'shared' next to non-room nouns
        "Rooftop terrace, shared bathroom facilities on some floors",
        "Standard double room, breakfast included",
        "Modern rooms near the station",
        "Dormant volcano views",  # 'dorm' must be a whole word
    ],
)
def test_ordinary_descriptions_are_kept(text):
    assert is_acceptable_stay(_offer(description=text))


def test_missing_texts_are_kept():
    assert is_acceptable_stay(_offer(room_type=None, description=None))


# --- hybrid chains are NOT excluded by name --------------------------------------


@pytest.mark.parametrize(
    "name", ["a&o Hamburg Hauptbahnhof", "A&O Berlin Mitte", "Generator Barcelona", "MEININGER Hotel Rome", "Hostal Born Boutique"]
)
def test_hybrid_chains_are_kept_by_name(name):
    assert is_acceptable_stay(_offer(name=name, description="Private double rooms with ensuite bathroom"))


def test_hybrid_chain_is_still_dropped_if_its_text_lists_dorms():
    offer = _offer(name="a&o Hostel Prague", description="Private rooms and 6-bed dorm rooms")
    assert not is_acceptable_stay(offer)


# --- youth hostels ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Youth Hostel Berlin", "youth-hostel Mitte", "Jugendherberge Hamburg Auf dem Stintfang", "DJH Jugendherberge Köln", "YOUTH HOSTELS Rome"],
)
def test_youth_hostels_are_excluded_by_name(name):
    assert not is_acceptable_stay(_offer(name=name))


def test_youth_hostel_in_the_description_is_excluded():
    assert not is_acceptable_stay(_offer(description="Official Jugendherberge with breakfast"))


# --- rating ----------------------------------------------------------------------


def test_minimum_rating_is_3_8_stars():
    assert MIN_HOTEL_RATING == 3.8
    assert meets_min_rating(_offer(rating=3.8)) and meets_min_rating(_offer(rating=4.6))
    assert not meets_min_rating(_offer(rating=3.79))


def test_unrated_hotel_is_not_blocked():
    assert meets_min_rating(_offer(rating=None))


# --- best_accommodation ------------------------------------------------------------


def test_best_is_the_cheapest_acceptable_well_rated_offer():
    offers = [
        _offer(40.0, name="Dorm Hostel", description="8-bed dorm"),   # cheapest, but shared sleeping
        _offer(70.0, name="Low rated", rating=3.5),                   # cheap, but < 3.8
        _offer(120.0, name="Good B"), _offer(90.0, name="Good A"), _offer(200.0, name="Pricey"),
    ]
    assert best_accommodation(offers).name == "Good A"


def test_no_acceptable_offer_returns_none():
    assert best_accommodation([_offer(description="dorm")]) is None
    assert best_accommodation([]) is None


def test_low_rated_only_returns_none_by_default_but_a_fallback_for_error_fares():
    offers = [_offer(80.0, name="Meh", rating=3.0), _offer(60.0, name="Dorm", description="dormitory")]

    assert best_accommodation(offers) is None
    assert best_accommodation(offers, require_rating=False).name == "Meh"  # never the dorm


def test_well_rated_offer_beats_a_cheaper_low_rated_one_even_without_the_requirement():
    offers = [_offer(50.0, name="Cheap meh", rating=3.0), _offer(90.0, name="Fine", rating=4.0)]
    assert best_accommodation(offers, require_rating=False).name == "Fine"


# --- baseline observations use only suggestible stays -----------------------------


def test_observation_ignores_dorms_and_low_rated_hotels():
    group = AccommodationComparisonGroup("PMI", _IN, _OUT, "EUR")
    offers = [_offer(20.0, description="dorm beds"), _offer(50.0, rating=3.2), _offer(110.0, name="Real hotel")]

    observation = observation_from_accommodation_search_results(
        offers, group, datetime(2026, 9, 25, tzinfo=timezone.utc)
    )

    assert observation.price == 110.0


def test_observation_is_none_when_nothing_is_suggestible():
    group = AccommodationComparisonGroup("PMI", _IN, _OUT, "EUR")
    offers = [_offer(20.0, description="dorm beds"), _offer(50.0, rating=3.2)]
    assert observation_from_accommodation_search_results(offers, group, datetime(2026, 9, 25, tzinfo=timezone.utc)) is None
