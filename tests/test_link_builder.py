"""monetization/link_builder.py - deeplinks with optional affiliate IDs."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from trip_hunter.monetization.link_builder import build_flight_link, build_hotel_link

_OUT, _BACK = date(2026, 10, 2), date(2026, 10, 7)
_ENV_VARS = (
    "BOOKING_AFFILIATE_ID", "TRAVELPAYOUTS_MARKER", "TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ID",
    "TRAVELPAYOUTS_CAMPAIGN_ID", "FLIGHT_LINK_PROVIDER", "HOTEL_LINK_PROVIDER",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


# --- flights: without any partner ID --------------------------------------------


def test_flight_link_without_ids_is_a_clean_google_flights_search():
    url = build_flight_link("HAM", "PMI", _OUT, _BACK)

    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "www.google.com", "/travel/flights")
    assert _query(url)["q"] == ["Flights from HAM to PMI on 2026-10-02 through 2026-10-07"]
    assert "marker" not in url and "aid" not in url


def test_one_way_flight_link_has_no_return_date():
    url = build_flight_link("HAM", "PMI", _OUT)
    assert _query(url)["q"] == ["Flights from HAM to PMI on 2026-10-02"]


def test_missing_environment_never_raises():
    for provider in (None, "google", "aviasales", "skyscanner", "nonsense"):
        assert build_flight_link("HAM", "PMI", _OUT, _BACK, provider=provider).startswith("https://")
    assert build_hotel_link("Hotel X", "Palma", _OUT, _BACK).startswith("https://")


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_or_blank_ids_count_as_unset(monkeypatch, value):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", value)
    monkeypatch.setenv("BOOKING_AFFILIATE_ID", value)

    assert "google.com" in build_flight_link("HAM", "PMI", _OUT, _BACK)
    assert "aid=" not in build_hotel_link("Hotel X", "Palma", _OUT, _BACK)


# --- flights: with a Travelpayouts marker ---------------------------------------


def test_marker_switches_the_default_to_aviasales_with_the_tracking_parameter(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "123456")

    url = build_flight_link("HAM", "PMI", _OUT, _BACK)

    assert url == "https://www.aviasales.com/search/HAM0210PMI07101?marker=123456"


def test_aviasales_without_marker_is_a_neutral_search():
    assert build_flight_link("HAM", "PMI", _OUT, _BACK, provider="aviasales") == (
        "https://www.aviasales.com/search/HAM0210PMI07101"
    )


def test_aviasales_one_way_path():
    assert build_flight_link("BER", "LIS", date(2026, 11, 5), provider="aviasales").endswith("/BER0511LIS1")


def test_marker_is_url_encoded(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "a b&c=d")
    assert _query(build_flight_link("HAM", "PMI", _OUT, _BACK)) == {"marker": ["a b&c=d"]}
    assert "&c=d" not in build_flight_link("HAM", "PMI", _OUT, _BACK)


def test_explicit_google_provider_ignores_the_marker(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "123456")
    url = build_flight_link("HAM", "PMI", _OUT, _BACK, provider="google")
    assert "google.com" in url and "123456" not in url


def test_provider_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("FLIGHT_LINK_PROVIDER", "Skyscanner")
    assert build_flight_link("HAM", "PMI", _OUT, _BACK).startswith("https://www.skyscanner.de/transport/fluge/ham/pmi/")


def test_unknown_provider_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("FLIGHT_LINK_PROVIDER", "kayak")
    assert "google.com" in build_flight_link("HAM", "PMI", _OUT, _BACK)
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "1")
    assert "aviasales.com" in build_flight_link("HAM", "PMI", _OUT, _BACK)


# --- flights: Skyscanner via Travelpayouts ---------------------------------------


def test_skyscanner_without_ids_is_the_plain_search():
    assert build_flight_link("HAM", "PMI", _OUT, _BACK, provider="skyscanner") == (
        "https://www.skyscanner.de/transport/fluge/ham/pmi/261002/261007/"
    )


def test_skyscanner_with_marker_but_no_program_id_stays_untracked(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "123456")
    url = build_flight_link("HAM", "PMI", _OUT, _BACK, provider="skyscanner")
    assert url.startswith("https://www.skyscanner.de/") and "tp.media" not in url


def test_skyscanner_with_marker_and_program_id_goes_through_tp_media(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "123456")
    monkeypatch.setenv("TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ID", "4114")
    monkeypatch.setenv("TRAVELPAYOUTS_CAMPAIGN_ID", "100")

    url = build_flight_link("HAM", "PMI", _OUT, _BACK, provider="skyscanner")

    assert urlsplit(url).netloc == "tp.media"
    query = _query(url)
    assert query["marker"] == ["123456"] and query["p"] == ["4114"] and query["campaign_id"] == ["100"]
    assert query["u"] == ["https://www.skyscanner.de/transport/fluge/ham/pmi/261002/261007/"]


# --- hotels ----------------------------------------------------------------------


def test_hotel_link_without_aid_is_a_clean_booking_search():
    url = build_hotel_link("Hotel Playa Sol", "Palma", _OUT, _BACK)

    parts = urlsplit(url)
    assert (parts.netloc, parts.path) == ("www.booking.com", "/searchresults.de.html")
    query = _query(url)
    assert query["ss"] == ["Hotel Playa Sol, Palma"]
    assert query["checkin"] == ["2026-10-02"] and query["checkout"] == ["2026-10-07"]
    assert query["group_adults"] == ["2"] and query["no_rooms"] == ["1"]
    assert "aid" not in query


def test_hotel_link_with_aid_carries_the_tracking_parameter(monkeypatch):
    monkeypatch.setenv("BOOKING_AFFILIATE_ID", "998877")
    query = _query(build_hotel_link("Hotel Playa Sol", "Palma", _OUT, _BACK))
    assert query["aid"] == ["998877"]


def test_hotel_aid_is_url_encoded(monkeypatch):
    monkeypatch.setenv("BOOKING_AFFILIATE_ID", "12 3&x=y")
    url = build_hotel_link("Hotel", "Palma", _OUT, _BACK)
    assert _query(url)["aid"] == ["12 3&x=y"] and "&x=y" not in url


def test_google_hotels_provider_is_untracked(monkeypatch):
    monkeypatch.setenv("BOOKING_AFFILIATE_ID", "998877")
    url = build_hotel_link("Hotel Playa Sol", "Palma", _OUT, _BACK, provider="google")
    assert urlsplit(url).path == "/travel/search" and "998877" not in url
    assert _query(url)["q"] == ["Hotel Playa Sol, Palma"]


def test_hotel_provider_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("HOTEL_LINK_PROVIDER", "google")
    assert "google.com" in build_hotel_link("Hotel", "Palma", _OUT, _BACK)


# --- encoding of umlauts / special characters ---------------------------------------


def test_umlauts_are_utf8_percent_encoded_in_hotel_and_city():
    url = build_hotel_link("Hotel Café Müller", "Düsseldorf", _OUT, _BACK)

    assert "Caf%C3%A9%20M%C3%BCller%2C%20D%C3%BCsseldorf" in url
    assert "ü" not in url and "é" not in url and " " not in url
    assert _query(url)["ss"] == ["Hotel Café Müller, Düsseldorf"]  # round-trips


@pytest.mark.parametrize("name", ["Tom & Jerry's <Inn>", "Hotel #1 = Best?", "Ärzte/Ökö-Hotel 100%", "日本 ホテル", "  Hotel  "])
def test_special_characters_cannot_break_or_inject_parameters(name):
    url = build_hotel_link(name, "Köln", _OUT, _BACK)
    query = _query(url)

    assert query["ss"] == [f"{name.strip()}, Köln"]
    assert set(query) == {"ss", "checkin", "checkout", "group_adults", "no_rooms", "group_children"}
    assert "&" not in url.split("?", 1)[1].replace("&checkin", "").replace("&checkout", "").replace("&group_adults", "").replace("&no_rooms", "").replace("&group_children", "")
    assert " " not in url and "<" not in url and "#" not in url


def test_injection_attempt_via_hotel_name_stays_inside_the_ss_value():
    url = build_hotel_link("X&aid=evil", "Palma", _OUT, _BACK)
    assert "aid" not in _query(url)
    assert _query(url)["ss"] == ["X&aid=evil, Palma"]


def test_empty_city_or_name_still_gives_a_valid_link():
    assert _query(build_hotel_link("Hotel X", "", _OUT, _BACK))["ss"] == ["Hotel X"]
    assert _query(build_hotel_link("", "Palma", _OUT, _BACK))["ss"] == ["Palma"]


def test_iata_codes_are_sanitised_in_path_segments():
    url = build_flight_link("h a/m", "p?m#i", _OUT, _BACK, provider="aviasales")
    assert url == "https://www.aviasales.com/search/HAM0210PMI07101"
