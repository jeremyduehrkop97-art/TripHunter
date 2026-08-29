from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from vacation_hunter.engine.price_statistics import (
    MIN_HISTORY_OBSERVATIONS,
    classify_position,
    compute_statistics,
    get_historical_baseline,
    percent_diff_from_median,
)
from vacation_hunter.models import HistoricalPosition, PriceObservation, TripType
from vacation_hunter.price_history_repository import PriceHistoryRepository

_ORIGIN = "HAM"
_DESTINATION = "PMI"
_DEPARTURE = date(2026, 10, 2)
_RETURN = date(2026, 10, 7)
_CURRENCY = "EUR"


def test_compute_statistics_requires_at_least_one_price():
    with pytest.raises(ValueError):
        compute_statistics([])


def test_median_is_robust_to_outliers():
    """The exact example from docs/PRODUCT_SPEC.md: a single extreme
    outlier must not drag the baseline off of what's actually typical."""
    stats = compute_statistics([170.0, 180.0, 175.0, 185.0, 800.0])

    assert stats.median == 180.0
    assert stats.mean > 250.0  # heavily skewed by the outlier - median isn't


def test_percentiles_and_bounds():
    stats = compute_statistics([175.0, 178.0, 180.0, 182.0, 185.0, 190.0])

    assert stats.observation_count == 6
    assert stats.minimum == 175.0
    assert stats.maximum == 190.0
    assert stats.median == 181.0
    assert stats.p25 < stats.median < stats.p75
    assert stats.stdev is not None and stats.stdev > 0


def test_single_observation_has_no_stdev():
    stats = compute_statistics([200.0])

    assert stats.observation_count == 1
    assert stats.minimum == stats.maximum == stats.median == stats.p25 == stats.p75 == 200.0
    assert stats.stdev is None


def test_classify_position_below_within_above():
    stats = compute_statistics([175.0, 178.0, 180.0, 182.0, 185.0, 190.0])

    assert classify_position(100.0, stats) == HistoricalPosition.BELOW_HISTORY
    assert classify_position(stats.median, stats) == HistoricalPosition.WITHIN_HISTORY
    assert classify_position(1000.0, stats) == HistoricalPosition.ABOVE_HISTORY


def test_percent_diff_from_median_sign():
    stats = compute_statistics([180.0, 175.0, 190.0, 185.0, 178.0, 182.0])

    diff = percent_diff_from_median(119.0, stats)

    assert diff < 0  # cheaper than median
    assert round(diff) == -34  # matches the docs/PRODUCT_SPEC.md demo example


def _seed(repo: PriceHistoryRepository, prices: list[float]) -> None:
    for i, price in enumerate(prices):
        repo.add_observation(
            PriceObservation(
                origin=_ORIGIN,
                destination=_DESTINATION,
                departure_date=_DEPARTURE,
                return_date=_RETURN,
                trip_type=TripType.ROUND_TRIP,
                price=price,
                currency=_CURRENCY,
                provider="test",
                stops=0,
                airline="Testair",
                observed_at=datetime(2026, 8, 1 + i, 8, 0, tzinfo=timezone.utc),
            )
        )


def test_min_history_observations_is_a_small_positive_number():
    assert MIN_HISTORY_OBSERVATIONS >= 3


def test_no_baseline_below_minimum_observations(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    _seed(repo, [180.0, 175.0])  # fewer than MIN_HISTORY_OBSERVATIONS

    baseline = get_historical_baseline(
        repo, _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY, 119.0
    )

    assert baseline is None


def test_baseline_available_at_minimum_observations(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    _seed(repo, [180.0, 175.0, 190.0, 185.0, 178.0][:MIN_HISTORY_OBSERVATIONS])

    baseline = get_historical_baseline(
        repo, _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY, 119.0
    )

    assert baseline is not None
    assert baseline.statistics.observation_count == MIN_HISTORY_OBSERVATIONS
    assert baseline.position == HistoricalPosition.BELOW_HISTORY
    assert baseline.percent_diff_from_median < 0


def test_no_baseline_for_unrelated_route(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    _seed(repo, [180.0, 175.0, 190.0, 185.0, 178.0, 182.0])

    baseline = get_historical_baseline(
        repo, "XXX", "YYY", _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY, 119.0
    )

    assert baseline is None
