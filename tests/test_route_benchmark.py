"""engine/route_benchmark.py: route-level "normal market price" benchmarks
and the generic percentage-discount gate against them - the replacement
for feed_radar.py's old static per-destination-tier price caps."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trip_hunter.engine.route_benchmark import (
    BUSINESS_EUROPE_SHORT_HAUL_BENCHMARK_EUR,
    BUSINESS_FAR_EAST_SEA_BENCHMARK_EUR,
    BUSINESS_MIDHAUL_MENA_BENCHMARK_EUR,
    BUSINESS_OCEANIA_SOUTH_AMERICA_BENCHMARK_EUR,
    BUSINESS_TRANSATLANTIC_BENCHMARK_EUR,
    EUROPE_SHORT_HAUL_BENCHMARK_EUR,
    FAR_EAST_SEA_BENCHMARK_EUR,
    MIDHAUL_MENA_BENCHMARK_EUR,
    MIN_HISTORY_OBSERVATIONS_FOR_BENCHMARK,
    OCEANIA_SOUTH_AMERICA_BENCHMARK_EUR,
    TRANSATLANTIC_BENCHMARK_EUR,
    get_business_benchmark,
    get_economy_benchmark,
    get_route_benchmark,
    is_deal_price,
)
from trip_hunter.models import PriceObservation, TripType
from trip_hunter.price_history_repository import PriceHistoryRepository


def _empty_repo(tmp_path) -> PriceHistoryRepository:
    """No history at all for any route - forces the regional estimate,
    deterministically, regardless of whatever real price history happens
    to sit in this machine's own default database."""
    return PriceHistoryRepository(tmp_path / "empty.db")


# --- regional benchmark tiers --------------------------------------------------------


def test_europe_short_haul_benchmark_for_a_mediterranean_destination(tmp_path):
    assert get_economy_benchmark("HAM", "PMI", repo=_empty_repo(tmp_path)) == EUROPE_SHORT_HAUL_BENCHMARK_EUR == 140.0


def test_midhaul_mena_benchmark_for_a_gulf_destination(tmp_path):
    assert get_economy_benchmark("FRA", "DXB", repo=_empty_repo(tmp_path)) == MIDHAUL_MENA_BENCHMARK_EUR == 420.0


def test_transatlantic_benchmark_for_the_named_jfk_example(tmp_path):
    """The task's own worked example: JFK's benchmark is 530 €."""
    assert get_economy_benchmark("FRA", "JFK", repo=_empty_repo(tmp_path)) == TRANSATLANTIC_BENCHMARK_EUR == 530.0


def test_far_east_sea_benchmark_for_a_southeast_asian_destination(tmp_path):
    assert get_economy_benchmark("MUC", "DPS", repo=_empty_repo(tmp_path)) == FAR_EAST_SEA_BENCHMARK_EUR == 820.0


def test_oceania_south_america_benchmark_for_an_oceania_destination(tmp_path):
    assert get_economy_benchmark("FRA", "SYD", repo=_empty_repo(tmp_path)) == OCEANIA_SOUTH_AMERICA_BENCHMARK_EUR == 950.0


def test_an_unresolved_or_uncurated_destination_defaults_to_the_cheapest_closest_tier(tmp_path):
    """Same "when in doubt, don't assume further" convention as every
    other such fallback in this project (flight_price_guide_for,
    hotel_nightly_guide_price, ...) - never a guessed, more expensive tier."""
    assert get_economy_benchmark("FRA", "ZZZ", repo=_empty_repo(tmp_path)) == EUROPE_SHORT_HAUL_BENCHMARK_EUR
    assert get_economy_benchmark("FRA", None, repo=_empty_repo(tmp_path)) == EUROPE_SHORT_HAUL_BENCHMARK_EUR


# --- is_deal_price ------------------------------------------------------------------


def test_jfk_350_against_its_530_benchmark_is_a_deal_at_roughly_34_percent():
    """The task's own worked example."""
    is_deal, discount_percent = is_deal_price(350.0, TRANSATLANTIC_BENCHMARK_EUR)
    assert is_deal is True
    assert discount_percent == pytest.approx(33.96, abs=0.01)


def test_jfk_480_against_its_530_benchmark_is_not_a_deal_at_roughly_9_percent():
    """The task's own worked example."""
    is_deal, discount_percent = is_deal_price(480.0, TRANSATLANTIC_BENCHMARK_EUR)
    assert is_deal is False
    assert discount_percent == pytest.approx(9.43, abs=0.01)


def test_exactly_the_minimum_discount_percent_counts_as_a_deal():
    is_deal, discount_percent = is_deal_price(70.0, 100.0, min_discount_percent=30.0)
    assert is_deal is True and discount_percent == 30.0


def test_just_under_the_minimum_discount_percent_is_not_a_deal():
    is_deal, discount_percent = is_deal_price(70.01, 100.0, min_discount_percent=30.0)
    assert is_deal is False and discount_percent == pytest.approx(29.99, abs=0.01)


def test_a_price_above_the_benchmark_is_never_a_deal_and_discount_is_negative():
    is_deal, discount_percent = is_deal_price(150.0, 100.0)
    assert is_deal is False and discount_percent == -50.0


def test_a_zero_or_negative_benchmark_can_never_produce_a_deal():
    assert is_deal_price(10.0, 0.0) == (False, 0.0)
    assert is_deal_price(10.0, -5.0) == (False, 0.0)


def test_a_custom_min_discount_percent_is_respected():
    # 60 vs a 100 benchmark is exactly 40% off.
    is_deal, _discount_percent = is_deal_price(60.0, 100.0, min_discount_percent=30.0)
    assert is_deal is True
    is_deal, _discount_percent = is_deal_price(60.0, 100.0, min_discount_percent=40.0)
    assert is_deal is True
    is_deal, _discount_percent = is_deal_price(60.0, 100.0, min_discount_percent=40.01)
    assert is_deal is False


# --- real own-history median fallback -----------------------------------------------


def _observation(origin: str, destination: str, price: float, *, currency: str = "EUR") -> PriceObservation:
    return PriceObservation(
        origin=origin, destination=destination,
        departure_date=date(2026, 11, 10), return_date=date(2026, 11, 17),
        trip_type=TripType.ROUND_TRIP, price=price, currency=currency, provider="test",
        stops=0, airline=None, cabin_class=None, observed_at=datetime.now(timezone.utc),
    )


def test_with_at_least_three_own_observations_the_median_replaces_the_regional_estimate(tmp_path):
    repo = PriceHistoryRepository(tmp_path / "history.db")
    assert MIN_HISTORY_OBSERVATIONS_FOR_BENCHMARK == 3
    # Three real HAM -> PMI observations, median 180 - far from the 140 €
    # regional Europe estimate this route would otherwise fall back to.
    for price in (160.0, 180.0, 220.0):
        repo.add_observation(_observation("HAM", "PMI", price))

    assert get_economy_benchmark("HAM", "PMI", repo=repo) == 180.0


def test_fewer_than_three_own_observations_still_falls_back_to_the_regional_estimate(tmp_path):
    repo = PriceHistoryRepository(tmp_path / "history.db")
    for price in (160.0, 180.0):  # only 2 - below MIN_HISTORY_OBSERVATIONS_FOR_BENCHMARK
        repo.add_observation(_observation("HAM", "PMI", price))

    assert get_economy_benchmark("HAM", "PMI", repo=repo) == EUROPE_SHORT_HAUL_BENCHMARK_EUR


def test_history_for_a_different_route_never_leaks_into_this_ones_benchmark(tmp_path):
    repo = PriceHistoryRepository(tmp_path / "history.db")
    for price in (900.0, 950.0, 1000.0):
        repo.add_observation(_observation("HAM", "JFK", price))  # a different route

    assert get_economy_benchmark("HAM", "PMI", repo=repo) == EUROPE_SHORT_HAUL_BENCHMARK_EUR


# --- business class --------------------------------------------------------------


def test_business_benchmark_for_a_mediterranean_destination(tmp_path):
    assert get_business_benchmark("HAM", "PMI", repo=_empty_repo(tmp_path)) == BUSINESS_EUROPE_SHORT_HAUL_BENCHMARK_EUR == 350.0


def test_business_benchmark_for_a_gulf_destination(tmp_path):
    assert get_business_benchmark("FRA", "DXB", repo=_empty_repo(tmp_path)) == BUSINESS_MIDHAUL_MENA_BENCHMARK_EUR == 950.0


def test_business_benchmark_for_the_named_new_york_example(tmp_path):
    """The task's own worked example: JFK's Business benchmark is 1.850 €."""
    assert get_business_benchmark("FRA", "JFK", repo=_empty_repo(tmp_path)) == BUSINESS_TRANSATLANTIC_BENCHMARK_EUR == 1850.0


def test_business_benchmark_for_a_southeast_asian_destination(tmp_path):
    assert get_business_benchmark("MUC", "DPS", repo=_empty_repo(tmp_path)) == BUSINESS_FAR_EAST_SEA_BENCHMARK_EUR == 2200.0


def test_business_benchmark_for_an_oceania_destination(tmp_path):
    assert get_business_benchmark("FRA", "SYD", repo=_empty_repo(tmp_path)) == BUSINESS_OCEANIA_SOUTH_AMERICA_BENCHMARK_EUR == 2600.0


def test_business_benchmark_never_uses_own_history_even_when_plenty_exists(tmp_path):
    """No provider populates PriceObservation.cabin_class yet (see module
    docstring) - a route's ordinary (Economy) history must never be
    mistaken for Business-Class history, however much of it exists."""
    repo = PriceHistoryRepository(tmp_path / "history.db")
    for price in (900.0, 950.0, 1000.0):  # real HAM -> PMI Economy observations
        repo.add_observation(_observation("HAM", "PMI", price))

    assert get_business_benchmark("HAM", "PMI", repo=repo) == BUSINESS_EUROPE_SHORT_HAUL_BENCHMARK_EUR


def test_get_route_benchmark_dispatches_business_to_the_business_benchmark(tmp_path):
    repo = _empty_repo(tmp_path)
    assert get_route_benchmark("FRA", "JFK", cabin_class="business", repo=repo) == BUSINESS_TRANSATLANTIC_BENCHMARK_EUR


def test_get_route_benchmark_first_class_reuses_the_business_benchmark_as_a_conservative_floor(tmp_path):
    """No distinct First-Class figures were named by this task - a real
    First-Class fare realistically costs even more than Business, so
    reusing the Business benchmark only makes the discount gate STRICTER,
    never more permissive."""
    repo = _empty_repo(tmp_path)
    assert get_route_benchmark("FRA", "JFK", cabin_class="first", repo=repo) == BUSINESS_TRANSATLANTIC_BENCHMARK_EUR


def test_get_route_benchmark_premium_economy_reuses_the_economy_benchmark_as_a_conservative_floor(tmp_path):
    repo = _empty_repo(tmp_path)
    assert get_route_benchmark("FRA", "JFK", cabin_class="premium_economy", repo=repo) == TRANSATLANTIC_BENCHMARK_EUR


def test_get_route_benchmark_defaults_to_economy(tmp_path):
    repo = _empty_repo(tmp_path)
    assert get_route_benchmark("FRA", "JFK", repo=repo) == TRANSATLANTIC_BENCHMARK_EUR == get_route_benchmark(
        "FRA", "JFK", cabin_class="economy", repo=repo
    )


def test_new_york_business_class_for_890_against_its_1850_benchmark_is_a_deal():
    """The task's own worked example."""
    benchmark = BUSINESS_TRANSATLANTIC_BENCHMARK_EUR
    is_deal, discount_percent = is_deal_price(890.0, benchmark)
    assert is_deal is True
    assert discount_percent == pytest.approx(51.89, abs=0.01)


def test_long_haul_business_for_650_euro_passes_and_1900_euro_is_rejected():
    """The task's own second worked example, against the Far-East/SEA
    Business benchmark (2.200 €)."""
    benchmark = BUSINESS_FAR_EAST_SEA_BENCHMARK_EUR
    assert is_deal_price(650.0, benchmark)[0] is True
    assert is_deal_price(1900.0, benchmark)[0] is False
