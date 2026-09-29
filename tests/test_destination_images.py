"""alerts/destination_images.py: the destination -> photo mapping."""

from __future__ import annotations

import re

import pytest

from trip_hunter.alerts.destination_images import (
    FALLBACK_IMAGE_URL,
    _DESTINATION_IMAGES,
    destination_image_url,
)

_URL_RE = re.compile(r"^https://images\.unsplash\.com/photo-[\w-]+\?auto=format&fit=crop&w=1280&q=80$")


@pytest.mark.parametrize("code", sorted(_DESTINATION_IMAGES))
def test_every_mapped_destination_returns_its_own_valid_unsplash_url(code):
    url = destination_image_url(code)
    assert _URL_RE.match(url), url
    assert url == _DESTINATION_IMAGES[code]


@pytest.mark.parametrize("code", ["ZZZ", "", "XYZ", "QQQ123", None])
def test_unknown_or_missing_codes_fall_back_and_never_return_none(code):
    url = destination_image_url(code)
    assert url == FALLBACK_IMAGE_URL
    assert url is not None
    assert _URL_RE.match(url)


def test_fallback_url_itself_is_valid_and_distinct_from_every_mapped_photo():
    assert _URL_RE.match(FALLBACK_IMAGE_URL)
    assert FALLBACK_IMAGE_URL not in _DESTINATION_IMAGES.values()


def test_the_table_covers_at_least_forty_five_destinations():
    """The point of this task: far fewer destinations should ever have to
    fall back to the generic photo than the original 10. (Growing this
    further towards 60+ needs unsplash.com itself reachable to source and
    visually verify new photos - see this module's docstring.)"""
    assert len(_DESTINATION_IMAGES) >= 45


def test_maldives_bali_and_seychelles_have_three_distinct_photos():
    """The reported bug: Bali, Seychellen and Malediven appeared to share
    one photo. This table already had three distinct entries for them -
    the real cause was upstream in feed_sensor.py (an unresolved "the
    Maldives"/"the Seychelles" left destination_iata empty, so
    destination_image_url was called with "" and every one of them fell
    back to the same generic photo; see test_feed_sensor.py's Maldives/
    Seychelles tests for that half of the fix). This test guards this
    file's table directly, regardless of upstream resolution."""
    urls = {destination_image_url("DPS"), destination_image_url("SEZ"), destination_image_url("MLE")}
    assert len(urls) == 3
    assert FALLBACK_IMAGE_URL not in urls


def test_every_photo_id_is_unique_no_destination_secretly_shares_another_ones_photo():
    """EWR intentionally reuses JFK's photo (same city, New York) - every
    other destination must have its own distinct image."""
    ids = list(_DESTINATION_IMAGES.values())
    duplicates = {url for url in ids if ids.count(url) > 1}
    assert duplicates <= {_DESTINATION_IMAGES["JFK"]}
    assert _DESTINATION_IMAGES["EWR"] == _DESTINATION_IMAGES["JFK"]


@pytest.mark.parametrize(
    "code",
    ["DPS", "SEZ", "MLE", "BKK", "HKT", "DXB", "JFK", "MIA", "PMI", "IBZ", "TFS", "HER", "RHO"],
)
def test_every_explicitly_requested_destination_is_covered(code):
    """The exact codes named in the task ("Bali, Seychellen, Malediven,
    Bangkok, Phuket, Dubai, New York, Miami, Mallorca, Ibiza, Teneriffa,
    Kreta, Rhodos usw.") must each have their own real photo, not the
    shared generic fallback."""
    assert destination_image_url(code) != FALLBACK_IMAGE_URL


def test_all_urls_share_the_same_telegram_friendly_query_parameters():
    """auto=format lets Unsplash serve whichever format the requesting
    client (Telegram) accepts; fit=crop/w/q keep every photo at a
    consistent, Telegram-cacheable size."""
    for url in [*_DESTINATION_IMAGES.values(), FALLBACK_IMAGE_URL]:
        assert "auto=format&fit=crop&w=1280&q=80" in url


def test_function_never_raises_for_any_unmapped_hashable_value():
    for value in (123, object(), "", "lowercase", "PMI "):
        assert destination_image_url(value) == FALLBACK_IMAGE_URL
