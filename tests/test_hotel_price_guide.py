"""monetization/hotel_price_guide.py: the destination -> nightly hotel
guide-price table for the feed-radar flexible-date combo teaser."""

from __future__ import annotations

import pytest

from trip_hunter.monetization.hotel_price_guide import (
    _DESTINATION_TIER,
    _TIER_NIGHTLY_EUR,
    hotel_nightly_guide_price,
)


@pytest.mark.parametrize("code", ["LIS", "BKK", "DPS", "MLE", "SEZ", "DXB", "JFK", "SYD"])
def test_every_covered_destination_returns_a_positive_int_price(code):
    price = hotel_nightly_guide_price(code)
    assert isinstance(price, int) and price > 0


@pytest.mark.parametrize("code", ["ZZZ", "", None, "FAE", "FRU"])
def test_uncovered_or_missing_codes_return_none_never_a_guessed_number(code):
    assert hotel_nightly_guide_price(code) is None


def test_every_destination_tier_reference_actually_exists():
    """A typo in _DESTINATION_TIER's value must fail loudly, not silently
    fall back to None for every destination in that (mistyped) tier."""
    for code, tier in _DESTINATION_TIER.items():
        assert tier in _TIER_NIGHTLY_EUR, f"{code} references unknown tier {tier!r}"


def test_every_tier_is_actually_used_by_at_least_one_destination():
    used_tiers = set(_DESTINATION_TIER.values())
    assert used_tiers == set(_TIER_NIGHTLY_EUR)


def test_all_guide_prices_are_in_a_plausible_range():
    """A sanity ceiling/floor - this is a coarse estimate, not a live
    quote, but it should never be absurd (e.g. 3 €/night or 3000 €/night)."""
    for price in _TIER_NIGHTLY_EUR.values():
        assert 20 <= price <= 400


def test_maldives_and_seychelles_are_pricier_than_southeast_asia_value_destinations():
    """A loose sanity check that the tiers are ordered sensibly relative
    to each other, not just individually plausible."""
    assert hotel_nightly_guide_price("MLE") > hotel_nightly_guide_price("BKK")
    assert hotel_nightly_guide_price("SEZ") > hotel_nightly_guide_price("DPS")
