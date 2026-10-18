"""Tests for monetization/curated_hotels.py - the hand-picked 4-5★
reference-hotel registry, and its two downstream integrations:
monetization/link_builder.py (direct property deeplinks) and
monetization/hotel_price_guide.py (real nightly rates instead of a
coarse per-tier guess)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from trip_hunter.engine.daily_scanner import DESTINATIONS as SCANNER_DESTINATIONS
from trip_hunter.monetization.curated_hotels import (
    CURATED_HOTELS,
    MIN_REVIEW_SCORE,
    MIN_STARS,
    CuratedHotel,
    curated_hotel,
)
from trip_hunter.monetization.hotel_price_guide import hotel_nightly_guide_price
from trip_hunter.monetization.link_builder import build_hotel_link


# --- the registry itself - every entry clears this module's own bar ------------


def test_every_scanner_destination_is_curated():
    """engine/daily_scanner.py's own curated top-destination list (the
    project's existing "top destinations", never a second, drifting
    allowlist) is fully covered - "Lege ein Verzeichnis für ALLE
    Top-Destinationen an"."""
    for code in SCANNER_DESTINATIONS:
        assert code in CURATED_HOTELS, code


@pytest.mark.parametrize("code", sorted(CURATED_HOTELS))
def test_every_curated_hotel_clears_the_quality_bar(code):
    hotel = CURATED_HOTELS[code]
    assert hotel.stars >= MIN_STARS
    assert hotel.review_score >= MIN_REVIEW_SCORE


@pytest.mark.parametrize("code", sorted(CURATED_HOTELS))
def test_every_curated_hotel_name_carries_its_own_star_suffix(code):
    """"ibis Styles Nairobi Westlands 4★" - the task's own exact
    requirement: the display name always names its star rating, never a
    bare property name the reader has to trust blindly."""
    hotel = CURATED_HOTELS[code]
    assert "★" in hotel.hotel_name


@pytest.mark.parametrize("code", sorted(CURATED_HOTELS))
def test_every_curated_hotel_has_a_booking_com_property_path(code):
    """Never a search page (searchresults...) - a single property's own
    page, with a positive base rate."""
    hotel = CURATED_HOTELS[code]
    assert hotel.booking_hotel_path.startswith("hotel/")
    assert hotel.booking_hotel_path.endswith(".html")
    assert "searchresults" not in hotel.booking_hotel_path
    assert hotel.base_price_per_night_eur > 0


def test_curated_hotel_paths_are_all_unique():
    """Two different destinations must never point at the same property."""
    paths = [hotel.booking_hotel_path for hotel in CURATED_HOTELS.values()]
    assert len(paths) == len(set(paths))


# --- curated_hotel() lookup ------------------------------------------------------


def test_curated_hotel_returns_the_registered_entry():
    assert curated_hotel("BKK") == CURATED_HOTELS["BKK"]


def test_curated_hotel_is_none_for_an_uncurated_destination():
    assert curated_hotel("PMO") is None  # Palermo - not in this registry


@pytest.mark.parametrize("missing", [None, ""])
def test_curated_hotel_is_none_for_no_destination_at_all(missing):
    assert curated_hotel(missing) is None


# --- link_builder.py integration: direct property deeplink ----------------------


def test_build_hotel_link_for_a_curated_destination_is_the_direct_property_page():
    from datetime import date

    url = build_hotel_link(
        "4-Sterne Hotel", "Nairobi", date(2026, 11, 10), date(2026, 11, 17),
        generic=True, destination_iata="NBO",
    )
    hotel = CURATED_HOTELS["NBO"]

    assert urlsplit(url).netloc == "www.booking.com"
    assert urlsplit(url).path == f"/{hotel.booking_hotel_path}"
    assert "checkin=2026-11-10" in url and "checkout=2026-11-17" in url
    # Never a search - no "ss" (search string) parameter at all.
    assert "ss=" not in url


def test_build_hotel_link_never_builds_a_direct_link_without_generic_true():
    """A REAL, specific property name (Deal.accommodation.name) must
    never be silently redirected to an unrelated curated hotel just
    because destination_iata happens to be passed."""
    from datetime import date

    url = build_hotel_link(
        "Some Other Real Hotel", "Nairobi", date(2026, 11, 10), date(2026, 11, 17), destination_iata="NBO",
    )
    assert "movenpick" not in url.lower()
    assert parse_qs(urlsplit(url).query)["ss"] == ["Some Other Real Hotel, Nairobi"]


def test_build_hotel_link_for_an_uncurated_destination_falls_back_to_the_filtered_city_search():
    from datetime import date

    url = build_hotel_link(
        "4-Sterne Hotel", "Palermo", date(2026, 11, 10), date(2026, 11, 17), generic=True, destination_iata="PMO",
    )
    assert urlsplit(url).path == "/searchresults.de.html"
    assert parse_qs(urlsplit(url).query)["nflt"] == ["class=4;class=5;review_score=80"]


# --- hotel_price_guide.py integration: real rate beats the generic tier guess ----


def test_hotel_nightly_guide_price_prefers_the_curated_rate():
    assert hotel_nightly_guide_price("BKK") == CURATED_HOTELS["BKK"].base_price_per_night_eur


@pytest.mark.parametrize("code", sorted(CURATED_HOTELS))
def test_hotel_nightly_guide_price_matches_every_curated_entry(code):
    assert hotel_nightly_guide_price(code) == CURATED_HOTELS[code].base_price_per_night_eur


def test_hotel_nightly_guide_price_for_an_uncurated_destination_is_unaffected():
    """Still the generic per-tier estimate - this feature only ever ADDS
    precision for curated destinations, never removes the existing
    fallback for anywhere else."""
    from trip_hunter.monetization.hotel_price_guide import DEFAULT_NIGHTLY_EUR

    assert hotel_nightly_guide_price("PMO") == DEFAULT_NIGHTLY_EUR


def test_hotel_nightly_guide_price_still_returns_none_only_for_no_destination_at_all():
    assert hotel_nightly_guide_price(None) is None
    assert hotel_nightly_guide_price("") is None
