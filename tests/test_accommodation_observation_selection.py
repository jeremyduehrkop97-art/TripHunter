"""Tests for observation_from_accommodation_search_results: the safe,
snapshot-aware way to turn a real search result (many AccommodationOffers)
into history (at most one AccommodationObservation for an EXPLICITLY given
AccommodationComparisonGroup). Mirrors test_observation_selection.py
(flights) - see "Observation Semantics" and "Explicit Comparison Groups" in
docs/PRODUCT_SPEC.md.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from trip_hunter.accommodation_price_history_repository import (
    AccommodationPriceHistoryRepository,
    observation_from_accommodation_search_results,
)
from trip_hunter.engine.price_statistics import compute_statistics
from trip_hunter.models import AccommodationComparisonGroup, AccommodationOffer

_DESTINATION = "PMI"
_CHECK_IN = date(2026, 10, 2)
_CHECK_OUT = date(2026, 10, 7)
_CURRENCY = "EUR"

_GROUP = AccommodationComparisonGroup(
    destination=_DESTINATION,
    check_in=_CHECK_IN,
    check_out=_CHECK_OUT,
    currency=_CURRENCY,
)


def _offer(
    price: float,
    *,
    destination: str = _DESTINATION,
    check_in: date = _CHECK_IN,
    check_out: date = _CHECK_OUT,
    currency: str = _CURRENCY,
    name: str = "Test Hotel",
) -> AccommodationOffer:
    return AccommodationOffer(
        destination=destination,
        check_in=check_in,
        check_out=check_out,
        total_price=price,
        currency=currency,
        name=name,
        rating=4.0,
        provider="test",
    )


def test_multiple_offers_produce_exactly_one_observation():
    offers = [_offer(p) for p in [227.0, 205.0, 410.0, 310.0]]

    observation = observation_from_accommodation_search_results(offers, _GROUP, datetime.now(timezone.utc))

    assert observation is not None
    assert observation.price == 205.0


def test_empty_offer_list_yields_no_observation():
    assert observation_from_accommodation_search_results([], _GROUP, datetime.now(timezone.utc)) is None


def test_explicit_group_wins_over_larger_unrelated_group():
    target_group_offers = [_offer(205.0), _offer(230.0)]  # 2 offers, our group
    other_dates_offers = [
        _offer(p, check_in=date(2026, 11, 1), check_out=date(2026, 11, 8))
        for p in [100.0, 110.0, 120.0, 130.0, 140.0, 150.0]  # 6 offers, larger group!
    ]
    offers = other_dates_offers + target_group_offers

    observation = observation_from_accommodation_search_results(offers, _GROUP, datetime.now(timezone.utc))

    assert observation is not None
    assert observation.price == 205.0  # from the smaller, explicitly requested group
    assert observation.check_in == _CHECK_IN
    assert observation.check_out == _CHECK_OUT


def test_no_matching_group_yields_none():
    offers = [
        _offer(p, check_in=date(2026, 11, 1), check_out=date(2026, 11, 8)) for p in [100.0, 120.0]
    ]

    assert observation_from_accommodation_search_results(offers, _GROUP, datetime.now(timezone.utc)) is None


def test_different_currency_offers_are_not_mixed_in():
    offers = [
        _offer(50.0, currency="USD"),  # cheapest overall, wrong currency
        _offer(205.0, currency="EUR"),
        _offer(230.0, currency="EUR"),
    ]

    observation = observation_from_accommodation_search_results(offers, _GROUP, datetime.now(timezone.utc))

    assert observation is not None
    assert observation.currency == "EUR"
    assert observation.price == 205.0


def test_different_destination_offers_are_not_mixed_in():
    offers = [
        _offer(60.0, destination="AGP"),  # cheapest overall, wrong destination
        _offer(205.0, destination="PMI"),
        _offer(230.0, destination="PMI"),
    ]

    observation = observation_from_accommodation_search_results(offers, _GROUP, datetime.now(timezone.utc))

    assert observation is not None
    assert observation.destination == "PMI"
    assert observation.price == 205.0


def test_rating_and_name_do_not_fragment_the_comparison_group():
    offers = [_offer(230.0, name="Fancy Hotel"), _offer(205.0, name="Budget Hotel")]

    observation = observation_from_accommodation_search_results(offers, _GROUP, datetime.now(timezone.utc))

    assert observation is not None
    assert observation.price == 205.0
    assert observation.name == "Budget Hotel"


# Multiple search snapshots -> N observations, correct median.
def test_multiple_snapshots_produce_one_observation_each_and_correct_median(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")
    base_time = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)

    daily_snapshots = [
        [205.0, 230.0, 250.0],  # cheapest 205
        [212.0, 260.0],  # cheapest 212
        [198.0, 220.0, 240.0],  # cheapest 198
        [207.0, 280.0],  # cheapest 207
        [215.0, 260.0],  # cheapest 215
    ]

    for day_offset, prices in enumerate(daily_snapshots):
        offers = [_offer(p) for p in prices]
        observed_at = base_time + timedelta(days=day_offset)
        observation = observation_from_accommodation_search_results(offers, _GROUP, observed_at)
        assert observation is not None
        repo.add_observation(observation)

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)

    assert len(stored) == 5
    assert sorted(o.price for o in stored) == [198.0, 205.0, 207.0, 212.0, 215.0]

    stats = compute_statistics([o.price for o in stored])
    assert stats.median == 207.0


# 30 offers in one snapshot must NOT count as 30 independent time observations.
def test_large_single_snapshot_still_counts_as_one_observation(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")
    offers = [_offer(150.0 + i) for i in range(30)]

    observation = observation_from_accommodation_search_results(offers, _GROUP, datetime.now(timezone.utc))
    assert observation is not None
    repo.add_observation(observation)

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)

    assert len(stored) == 1
    assert stored[0].price == 150.0  # the cheapest of the 30
