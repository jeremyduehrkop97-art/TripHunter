"""monetization/link_builder.py - deeplinks with optional affiliate IDs."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from trip_hunter.monetization.link_builder import build_flight_link, build_hotel_link

_OUT, _BACK = date(2026, 10, 2), date(2026, 10, 7)
_ENV_VARS = (
    "DEAL_SHEET_URL",
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


# --- deal sheet URL (Telegram Mini App page) --------------------------------------

from trip_hunter.monetization.link_builder import (  # noqa: E402
    DEFAULT_DEAL_SHEET_URL,
    build_deal_sheet_url,
    deal_sheet_base_url,
)

_FLIGHT = "https://www.google.com/travel/flights?q=Flights%20from%20HAM%20to%20PMI&hl=de"
_HOTEL = "https://www.booking.com/searchresults.de.html?ss=Hotel%20Playa%20Sol&aid=1"


def _sheet(**overrides):
    kwargs = dict(
        flight_link=_FLIGHT, hotel_link=_HOTEL, origin_city="Hamburg", destination_city="Palma de Mallorca",
        destination_code="PMI", flag="🇪🇸", departure_date=_OUT, return_date=_BACK, flight_price=79,
        hotel_price=103, total_price=182, hotel_name="Hotel Playa Sol", savings_percent=45,
        image_url="https://images.unsplash.com/photo-1566993850067-bb8df9c9807e?auto=format&w=1280",
    )
    kwargs.update(overrides)
    return build_deal_sheet_url(**kwargs)


def test_deal_sheet_url_uses_the_default_pages_url_and_the_query_string(monkeypatch):
    monkeypatch.delenv("DEAL_SHEET_URL", raising=False)
    url = _sheet()

    assert url.startswith(DEFAULT_DEAL_SHEET_URL + "?")
    assert urlsplit(url).fragment == ""  # Telegram owns the hash (#tgWebAppData)
    assert DEFAULT_DEAL_SHEET_URL.endswith("/TripHunter/deal.html") and DEFAULT_DEAL_SHEET_URL.startswith("https://")


def test_deal_sheet_url_round_trips_every_parameter(monkeypatch):
    monkeypatch.delenv("DEAL_SHEET_URL", raising=False)
    query = {k: v[0] for k, v in _query(_sheet()).items()}

    assert query == {
        "from": "Hamburg", "to": "Palma de Mallorca", "code": "PMI", "flag": "🇪🇸",
        "dep": "2026-10-02", "ret": "2026-10-07", "fp": "79", "hp": "103", "tp": "182",
        "hn": "Hotel Playa Sol", "sv": "45", "fl": _FLIGHT, "hl": _HOTEL,
        "img": "https://images.unsplash.com/photo-1566993850067-bb8df9c9807e?auto=format&w=1280",
    }


def test_nested_links_are_percent_encoded_so_their_own_parameters_survive():
    url = _sheet()

    assert "%26hl%3Dde" in url  # the flight link's own "&hl=de", encoded
    assert _query(url)["fl"] == [_FLIGHT]


def test_deal_sheet_url_encodes_umlauts_and_special_characters():
    url = _sheet(destination_city="Düsseldorf", hotel_name="Café & Co <Inn> #1")

    assert "D%C3%BCsseldorf" in url and "Caf%C3%A9%20%26%20Co%20%3CInn%3E%20%231" in url
    assert "ü" not in url and " " not in url and "<" not in url and "#" not in url
    query = _query(url)
    assert query["to"] == ["Düsseldorf"] and query["hn"] == ["Café & Co <Inn> #1"]


def test_flight_only_deal_leaves_hotel_parameters_out():
    query = _query(_sheet(hotel_link=None, hotel_price=None, hotel_name="", total_price=79))

    assert "hp" not in query and "hl" not in query and "hn" not in query
    assert query["fp"] == ["79"] and query["fl"] == [_FLIGHT]


def test_empty_optional_values_are_left_out():
    query = _query(build_deal_sheet_url(flight_link=_FLIGHT))
    assert list(query) == ["fl"]


def test_prices_are_rounded_commercially():
    query = _query(_sheet(flight_price=78.5, hotel_price=102.5, total_price=181.0, savings_percent=44.5))
    assert (query["fp"], query["hp"], query["sv"]) == (["79"], ["103"], ["45"])


def test_base_url_from_the_environment(monkeypatch):
    monkeypatch.setenv("DEAL_SHEET_URL", "https://example.org/app/deal.html")
    assert _sheet().startswith("https://example.org/app/deal.html?")
    assert deal_sheet_base_url() == "https://example.org/app/deal.html"


def test_base_url_that_already_has_a_query_gets_an_ampersand(monkeypatch):
    monkeypatch.setenv("DEAL_SHEET_URL", "https://example.org/deal.html?v=2")
    assert _sheet().startswith("https://example.org/deal.html?v=2&from=")


@pytest.mark.parametrize("value", ["off", "OFF", "none", "0", "false", "disabled"])
def test_deal_sheet_can_be_switched_off(monkeypatch, value):
    monkeypatch.setenv("DEAL_SHEET_URL", value)
    assert deal_sheet_base_url() is None and _sheet() is None


def test_blank_env_means_the_default(monkeypatch):
    monkeypatch.setenv("DEAL_SHEET_URL", "  ")
    assert deal_sheet_base_url() == DEFAULT_DEAL_SHEET_URL


def test_explicit_base_url_overrides_everything():
    assert _sheet(base_url="https://x.test/d.html").startswith("https://x.test/d.html?")
    assert _sheet(base_url="") is None


# --- share links ---------------------------------------------------------------------

from trip_hunter.monetization.link_builder import build_share_url  # noqa: E402


def test_whatsapp_share_url_encodes_umlauts_emoji_and_reserved_characters():
    text = "Schau mal: München für 182 € p.P.! ✈️ a&b=c #1 100%"
    url = build_share_url(text)

    assert url.startswith("https://api.whatsapp.com/send?text=")
    assert _query(url) == {"text": [text]}
    assert " " not in url and "+" not in url.split("?", 1)[1] and all(ord(c) < 128 for c in url)
    assert "&b=c" not in url and "#1" not in url


def test_telegram_share_url_carries_link_and_text_separately():
    url = build_share_url("Trip Hunter: Palma für 182 €", provider="telegram", url="https://t.me/+Invite")

    assert urlsplit(url).netloc == "t.me" and urlsplit(url).path == "/share/url"
    assert _query(url) == {"url": ["https://t.me/+Invite"], "text": ["Trip Hunter: Palma für 182 €"]}


def test_unknown_share_provider_falls_back_to_whatsapp():
    assert build_share_url("hi", provider="fax").startswith("https://api.whatsapp.com/send?")
