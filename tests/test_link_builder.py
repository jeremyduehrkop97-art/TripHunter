"""monetization/link_builder.py - deeplinks with optional affiliate IDs."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from trip_hunter.monetization.link_builder import (
    build_flight_link,
    build_hotel_link,
    build_kiwi_flight_link,
)

_OUT, _BACK = date(2026, 10, 2), date(2026, 10, 7)
_ENV_VARS = (
    "DEAL_SHEET_URL",
    "BOOKING_AFFILIATE_ID", "TRAVELPAYOUTS_MARKER", "TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ID",
    "TRAVELPAYOUTS_CAMPAIGN_ID", "FLIGHT_LINK_PROVIDER", "HOTEL_LINK_PROVIDER", "KIWI_AFFILIATE_ID",
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
    for provider in (None, "google", "aviasales", "skyscanner", "kiwi", "nonsense"):
        assert build_flight_link("HAM", "PMI", _OUT, _BACK, provider=provider).startswith("https://")
    assert build_hotel_link("Hotel X", "Palma", _OUT, _BACK).startswith("https://")


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_or_blank_ids_count_as_unset(monkeypatch, value):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", value)
    monkeypatch.setenv("BOOKING_AFFILIATE_ID", value)

    assert "google.com" in build_flight_link("HAM", "PMI", _OUT, _BACK)
    assert "aid=" not in build_hotel_link("Hotel X", "Palma", _OUT, _BACK)


# --- flights: no exact date at all - never a blank, routeless homepage ---------
# The actual reported bug: a generic free-text Google Flights query used to
# land on an empty "Wohin?" page with neither route nor date pre-filled.


def test_dateless_google_flights_link_still_names_the_route():
    url = build_flight_link("HAM", "PMI", None)
    assert urlsplit(url).netloc == "www.google.com"
    assert _query(url)["q"] == ["Flights from HAM to PMI"]
    assert "on " not in _query(url)["q"][0]


def test_dateless_flight_link_defaults_to_no_departure_date_argument_at_all():
    """departure_date is now optional - omitting it entirely behaves the
    same as passing None explicitly."""
    assert build_flight_link("HAM", "PMI") == build_flight_link("HAM", "PMI", None)


def test_dateless_aviasales_link_is_the_prefilled_search_form_not_the_dated_results_page(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    url = build_flight_link("HAM", "PMI", None, provider="aviasales")

    parts = urlsplit(url)
    assert parts.netloc == "www.aviasales.com" and parts.path == "/"  # the form, never /search/
    query = _query(url)
    assert query["params"] == ["HAMPMI1"]
    assert query["marker"] == ["781828"]


def test_dateless_aviasales_link_without_a_marker_is_still_a_real_route_search():
    url = build_flight_link("HAM", "PMI", None, provider="aviasales")
    assert _query(url)["params"] == ["HAMPMI1"]
    assert "marker" not in _query(url)


def test_dateless_skyscanner_link_has_the_route_but_no_date_segment():
    url = build_flight_link("HAM", "PMI", None, provider="skyscanner")
    assert url == "https://www.skyscanner.de/transport/fluge/ham/pmi/"


def test_dateless_kiwi_link_still_names_the_route():
    url = build_flight_link("HAM", "PMI", None, provider="kiwi")
    query = _query(url)
    assert query["from"] == ["HAM"] and query["to"] == ["PMI"]
    assert "departure" not in query and "return" not in query


@pytest.mark.parametrize("provider", ["google", "aviasales", "skyscanner", "kiwi"])
def test_no_provider_ever_produces_a_routeless_link_without_a_date(provider, monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    monkeypatch.setenv("TRAVELPAYOUTS_SKYSCANNER_PROGRAM_ID", "4114")
    url = build_flight_link("FRA", "ZNZ", None, provider=provider)
    assert "FRA" in url.upper() and "ZNZ" in url.upper()


# --- flights: with a Travelpayouts marker ---------------------------------------


def test_marker_switches_the_default_to_aviasales_with_the_tracking_parameter(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "123456")

    url = build_flight_link("HAM", "PMI", _OUT, _BACK)

    assert url == "https://www.aviasales.com/search/HAM0210PMI07101?currency=eur&marker=123456"


def test_aviasales_without_marker_still_carries_the_eur_currency():
    """REPORTED BUG, verified live 2026-10: without this, Aviasales opened
    in USD ($589) for a DACH user - currency=eur is unconditional, with
    or without a marker."""
    assert build_flight_link("HAM", "PMI", _OUT, _BACK, provider="aviasales") == (
        "https://www.aviasales.com/search/HAM0210PMI07101?currency=eur"
    )


def test_aviasales_one_way_path():
    url = build_flight_link("BER", "LIS", date(2026, 11, 5), provider="aviasales")
    assert url == "https://www.aviasales.com/search/BER0511LIS1?currency=eur"


def test_marker_is_url_encoded(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "a b&c=d")
    query = _query(build_flight_link("HAM", "PMI", _OUT, _BACK))
    assert query["marker"] == ["a b&c=d"] and query["currency"] == ["eur"]
    assert "&c=d" not in build_flight_link("HAM", "PMI", _OUT, _BACK)


def test_explicit_google_provider_ignores_the_marker(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "123456")
    url = build_flight_link("HAM", "PMI", _OUT, _BACK, provider="google")
    assert "google.com" in url and "123456" not in url


# --- currency=eur: REPORTED BUG, Aviasales opened in USD (2026-10) ---------------


def test_aviasales_dated_search_always_carries_currency_eur():
    assert _query(build_flight_link("HAM", "PMI", _OUT, _BACK, provider="aviasales"))["currency"] == ["eur"]


def test_aviasales_dateless_search_also_carries_currency_eur():
    url = build_flight_link("HAM", "PMI", provider="aviasales")
    assert _query(url)["currency"] == ["eur"]


def test_aviasales_currency_eur_is_present_with_and_without_a_marker(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    with_marker = _query(build_flight_link("HAM", "PMI", _OUT, _BACK, provider="aviasales"))
    monkeypatch.delenv("TRAVELPAYOUTS_MARKER", raising=False)
    without_marker = _query(build_flight_link("HAM", "PMI", _OUT, _BACK, provider="aviasales"))

    assert with_marker["currency"] == without_marker["currency"] == ["eur"]


def test_google_and_skyscanner_never_claim_the_aviasales_currency_param():
    """Google already pins its own `curr=EUR` (a different parameter
    name, unaffected); Skyscanner has no currency param at all here -
    this is specifically Aviasales' own fix, not a blanket rename."""
    google_url = build_flight_link("HAM", "PMI", _OUT, _BACK, provider="google")
    assert "currency" not in _query(google_url) and _query(google_url)["curr"] == ["EUR"]


def test_aviasales_never_claims_an_invented_sort_or_stops_parameter():
    """2026-10: researched (no confirmed, documented sort/layover-filter
    parameter exists for Aviasales' own human search-results page URL -
    see _aviasales_link's own docstring) and deliberately NOT fabricated
    - unlike build_kiwi_flight_link's `sort`/`max_stopovers`, which ARE
    Kiwi's own real, documented parameter vocabulary."""
    dated = _query(build_flight_link("HAM", "PMI", _OUT, _BACK, provider="aviasales"))
    dateless = _query(build_flight_link("HAM", "PMI", provider="aviasales"))
    for query in (dated, dateless):
        assert "sort" not in query and "sorting" not in query and "max_stopovers" not in query
        assert "direct" not in query and "transfers" not in query


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


# --- flights: Kiwi.com (a separate, non-Travelpayouts provider) -----------------


def test_kiwi_link_without_affiliate_id_is_untracked():
    url = build_kiwi_flight_link("HAM", "PMI", _OUT, _BACK)

    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "www.kiwi.com", "/deep")
    query = _query(url)
    assert query["from"] == ["HAM"] and query["to"] == ["PMI"]
    assert query["departure"] == ["2026-10-02"] and query["return"] == ["2026-10-07"]
    assert "affilid" not in query


# --- sort=quality / max_stopovers=1: REPORTED BUG, a 33h/13h-layover default (2026-10) ---


def test_kiwi_link_always_sorts_by_quality_and_caps_stopovers_at_one():
    query = _query(build_kiwi_flight_link("HAM", "PMI", _OUT, _BACK))
    assert query["sort"] == ["quality"] and query["max_stopovers"] == ["1"]


def test_kiwi_link_quality_filter_is_present_dateless_too():
    query = _query(build_kiwi_flight_link("HAM", "PMI"))
    assert query["sort"] == ["quality"] and query["max_stopovers"] == ["1"]


def test_kiwi_link_with_dates_and_an_explicit_affiliate_id():
    url = build_kiwi_flight_link("HAM", "PMI", _OUT, _BACK, affiliate_id="my_kiwi_id")
    query = _query(url)
    assert query["affilid"] == ["my_kiwi_id"]
    assert query["from"] == ["HAM"] and query["to"] == ["PMI"]
    assert query["departure"] == ["2026-10-02"] and query["return"] == ["2026-10-07"]


def test_kiwi_link_reads_the_affiliate_id_from_the_environment_by_default(monkeypatch):
    monkeypatch.setenv("KIWI_AFFILIATE_ID", "env_kiwi_id")
    assert _query(build_kiwi_flight_link("HAM", "PMI", _OUT, _BACK))["affilid"] == ["env_kiwi_id"]


def test_kiwi_link_explicit_affiliate_id_overrides_the_environment(monkeypatch):
    monkeypatch.setenv("KIWI_AFFILIATE_ID", "env_kiwi_id")
    url = build_kiwi_flight_link("HAM", "PMI", _OUT, _BACK, affiliate_id="explicit_id")
    assert _query(url)["affilid"] == ["explicit_id"]


def test_kiwi_link_without_any_dates_is_still_a_valid_dateless_search():
    url = build_kiwi_flight_link("HAM", "PMI")
    query = _query(url)
    assert query["from"] == ["HAM"] and query["to"] == ["PMI"]
    assert "departure" not in query and "return" not in query


def test_kiwi_link_one_way_has_no_return_date():
    query = _query(build_kiwi_flight_link("HAM", "PMI", _OUT))
    assert query["departure"] == ["2026-10-02"] and "return" not in query


# --- Kiwi.com: ALWAYS direct, never wrapped via Travelpayouts ------------------
# REMOVED (verified live, 2026-10): a real click on the generic Travelpayouts
# "customlink" click-redirect (c111.travelpayouts.com/click) failed with
# "promo_id is empty" - that endpoint needs a promo_id specific to a
# confirmed program/campaign, and Kiwi.com is in fact explicitly NOT
# available through Travelpayouts' own partner-link API at all. Kiwi.com
# links are therefore always the plain, direct link, tracked only via
# KIWI_AFFILIATE_ID (Kiwi's own `affilid`), regardless of TRAVELPAYOUTS_MARKER.


def test_kiwi_link_stays_direct_even_with_a_travelpayouts_marker_set(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    url = build_kiwi_flight_link("HAM", "PMI", _OUT, _BACK)

    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "www.kiwi.com", "/deep")
    assert "c111.travelpayouts.com" not in url and "781828" not in url
    query = _query(url)
    assert query["from"] == ["HAM"] and query["to"] == ["PMI"]


def test_kiwi_link_without_a_marker_stays_a_direct_kiwi_link():
    url = build_kiwi_flight_link("HAM", "PMI", _OUT, _BACK)
    assert urlsplit(url).netloc == "www.kiwi.com"


def test_kiwi_link_combines_an_explicit_affiliate_id_with_an_unrelated_travelpayouts_marker(monkeypatch):
    """KIWI_AFFILIATE_ID (a direct Kiwi partnership) still works exactly
    as before - entirely independent of whatever TRAVELPAYOUTS_MARKER is
    or isn't set, since that marker never applies to Kiwi.com here."""
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    url = build_kiwi_flight_link("HAM", "PMI", _OUT, _BACK, affiliate_id="my_kiwi_id")

    assert urlsplit(url).netloc == "www.kiwi.com"
    assert _query(url)["affilid"] == ["my_kiwi_id"]


def test_flight_link_provider_kiwi_never_gets_wrapped_via_travelpayouts_even_with_a_marker_set(monkeypatch):
    monkeypatch.setenv("FLIGHT_LINK_PROVIDER", "kiwi")
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")

    url = build_flight_link("HAM", "PMI", _OUT, _BACK)

    assert urlsplit(url).netloc == "www.kiwi.com"
    assert "c111.travelpayouts.com" not in url


def test_flight_link_provider_kiwi_switches_build_flight_link_to_kiwi(monkeypatch):
    monkeypatch.setenv("FLIGHT_LINK_PROVIDER", "kiwi")
    monkeypatch.setenv("KIWI_AFFILIATE_ID", "env_kiwi_id")

    url = build_flight_link("HAM", "PMI", _OUT, _BACK)

    assert urlsplit(url).netloc == "www.kiwi.com"
    query = _query(url)
    assert query["from"] == ["HAM"] and query["to"] == ["PMI"] and query["affilid"] == ["env_kiwi_id"]


def test_flight_link_explicit_provider_kiwi_argument_also_works():
    url = build_flight_link("HAM", "PMI", _OUT, _BACK, provider="kiwi")
    assert urlsplit(url).netloc == "www.kiwi.com"


def test_flight_link_falls_back_to_aviasales_when_marker_is_set_and_provider_is_unset(monkeypatch):
    """Explicit regression guard: Kiwi must never silently become the
    default just because it exists - with no FLIGHT_LINK_PROVIDER set,
    a configured Travelpayouts marker still means Aviasales, exactly as
    before this feature was added."""
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "123456")
    url = build_flight_link("HAM", "PMI", _OUT, _BACK)
    assert "aviasales.com" in url and "kiwi.com" not in url


def test_flight_link_falls_back_to_google_when_nothing_is_set():
    url = build_flight_link("HAM", "PMI", _OUT, _BACK)
    assert "google.com" in url and "kiwi.com" not in url


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


def test_google_hotels_provider_works_with_no_dates_at_all():
    """A dateless search (feed-radar signal with no known stay) - only
    valid for provider="google", which names no dates anyway; see the
    hotel guide-price combo teaser's dateless case."""
    url = build_hotel_link("4-Sterne Hotel", "Bangkok", provider="google")
    assert urlsplit(url).path == "/travel/search"
    assert _query(url)["q"] == ["4-Sterne Hotel, Bangkok"]


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


# --- generic=True: REPORTED BUG, "Hotel FREIGOLD" (2026-10) ------------------------
#
# Booking.com's `ss` is a literal text search - "4-Sterne Hotel, Bangkok"
# matched a REAL property named "Hotel FREIGOLD 4-Sterne-Superior" in
# Freistadt, Austria, 8000km from Bangkok. generic=True fixes this by
# searching the city ONLY, using `stars` as Booking's own class filter
# instead of baking a fake name into the search text.


def test_generic_hotel_link_for_an_uncurated_destination_searches_the_city_only():
    """"Palermo" is not in monetization/curated_hotels.py - the generic
    CITY search fallback, never the placeholder text itself."""
    url = build_hotel_link("4-Sterne Hotel", "Palermo", _OUT, _BACK, generic=True, destination_iata="PMO")
    query = _query(url)

    assert query["ss"] == ["Palermo"]
    assert "4-Sterne" not in url and "4-Sterne" not in query["ss"][0]


def test_generic_hotel_link_always_adds_the_mandatory_quality_filter_and_sort():
    """2026-10: "ZWINGENDE Qualitätsfilter" for any uncurated destination -
    4-5 stars, guest score >= 8.0, sorted best price/review first. Always
    present for generic=True, never conditional on a star count the
    caller happens to know."""
    url = build_hotel_link("4-Sterne Hotel", "Palermo", _OUT, _BACK, generic=True, destination_iata="PMO")
    query = _query(url)

    assert query["nflt"] == ["class=4;class=5;review_score=80"]
    assert query["order"] == ["review_score_and_price"]


def test_generic_hotel_link_with_no_destination_iata_at_all_still_gets_the_filter():
    url = build_hotel_link("Resort", "Bali", _OUT, _BACK, generic=True)
    query = _query(url)

    assert query["ss"] == ["Bali"]
    assert query["nflt"] == ["class=4;class=5;review_score=80"]


def test_non_generic_hotel_link_is_unaffected_the_real_name_stays_in_ss():
    """A REAL property name (Deal.accommodation.name) - generic=False is
    the default, so existing callers with a genuine SerpApi-priced hotel
    keep searching for exactly that property, never just the city."""
    url = build_hotel_link("Grand Hyatt Bangkok", "Bangkok", _OUT, _BACK)
    query = _query(url)

    assert query["ss"] == ["Grand Hyatt Bangkok, Bangkok"]
    assert "nflt" not in query and "order" not in query


def test_generic_hotel_link_via_google_provider_also_drops_the_placeholder_text():
    url = build_hotel_link("4-Sterne Hotel", "Palermo", provider="google", generic=True, destination_iata="PMO")
    assert _query(url)["q"] == ["Palermo"]


# --- generic=True + a curated destination: direct property link, no search ----


def test_generic_hotel_link_for_a_curated_destination_goes_direct_to_that_property():
    """2026-10: "der Nutzer will kein Suchportal" - Bangkok (BKK) is
    curated, so this is a direct Booking.com PROPERTY page, never a
    searchresults.de.html search at all."""
    url = build_hotel_link("4-Sterne Hotel", "Bangkok", _OUT, _BACK, generic=True, destination_iata="BKK")

    assert urlsplit(url).path == "/hotel/th/column-bangkok.de.html"
    assert "searchresults" not in url and "ss=" not in url
    query = _query(url)
    assert query["checkin"] == ["2026-10-02"] and query["checkout"] == ["2026-10-07"]
    assert "nflt" not in query and "order" not in query  # no filter needed - it's already THE pick


def test_curated_hotel_link_carries_the_affiliate_id_when_configured(monkeypatch):
    monkeypatch.setenv("BOOKING_AFFILIATE_ID", "998877")
    url = build_hotel_link("4-Sterne Hotel", "Bangkok", _OUT, _BACK, generic=True, destination_iata="BKK")
    assert _query(url)["aid"] == ["998877"]


def test_curated_hotel_link_works_dateless_too():
    url = build_hotel_link("4-Sterne Hotel", "Bangkok", generic=True, destination_iata="BKK")
    assert urlsplit(url).path == "/hotel/th/column-bangkok.de.html"
    assert "checkin" not in _query(url) and "checkout" not in _query(url)


def test_a_non_generic_call_never_triggers_the_curated_direct_link():
    """generic=False (the default) is a REAL property search
    (Deal.accommodation.name) - destination_iata alone must never
    redirect it to an unrelated curated hotel."""
    url = build_hotel_link("Some Real Hotel", "Bangkok", _OUT, _BACK, destination_iata="BKK")
    assert "column-bangkok" not in url
    assert _query(url)["ss"] == ["Some Real Hotel, Bangkok"]


def test_an_uncurated_destination_with_generic_true_falls_back_to_the_city_search():
    url = build_hotel_link("4-Sterne Hotel", "Palermo", _OUT, _BACK, generic=True, destination_iata="PMO")
    assert urlsplit(url).netloc == "www.booking.com" and urlsplit(url).path == "/searchresults.de.html"


def test_iata_codes_are_sanitised_in_path_segments():
    url = build_flight_link("h a/m", "p?m#i", _OUT, _BACK, provider="aviasales")
    assert url == "https://www.aviasales.com/search/HAM0210PMI07101?currency=eur"


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
    assert DEFAULT_DEAL_SHEET_URL == "https://trip-hunter.de/deal.html"


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


# --- "windows" (flexible-date combo teaser example-date matrix) ------------------

import json  # noqa: E402


def test_windows_param_is_json_encoded_and_round_trips(monkeypatch):
    monkeypatch.delenv("DEAL_SHEET_URL", raising=False)
    windows = [
        {"dep": "2026-11-07", "ret": "2026-11-10", "tp": 250, "fl": _FLIGHT, "hl": _HOTEL},
        {"dep": "2026-11-28", "ret": "2026-12-02", "tp": 270, "fl": _FLIGHT, "hl": _HOTEL},
    ]
    query = _query(_sheet(windows=windows))
    assert json.loads(query["windows"][0]) == windows


def test_no_windows_argument_leaves_the_parameter_out():
    assert "windows" not in _query(_sheet())


def test_empty_windows_list_also_leaves_the_parameter_out():
    assert "windows" not in _query(_sheet(windows=[]))


def test_an_oversized_windows_list_is_dropped_so_the_button_still_works():
    """A URL that would come out longer than _MAX_DEAL_SHEET_URL_LENGTH must
    still build successfully - just without the "windows" extra, never a
    None/broken result."""
    huge_link = _FLIGHT + "&pad=" + "x" * 2000
    windows = [{"dep": "2026-11-07", "ret": "2026-11-10", "tp": 250, "fl": huge_link, "hl": huge_link}]

    url = _sheet(windows=windows)

    assert url is not None
    query = _query(url)
    assert "windows" not in query
    assert query["fl"] == [_FLIGHT]  # the main (non-oversized) links are untouched


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
