"""Transparent statistics over our own observed price history.

No machine learning - every number here is a plain, explainable statistic
(count, min/max, mean, median, quartiles, stdev). See "Historical Price
Intelligence" in docs/PRODUCT_SPEC.md.
"""

from __future__ import annotations

import statistics as stats
from datetime import date

from trip_hunter.models import (
    HistoricalBaseline,
    HistoricalPosition,
    PriceStatistics,
    TripType,
)
from trip_hunter.price_history_repository import PriceHistoryRepository

# We refuse to call anything a "normal price" from too few data points.
# Five is a small, deliberately conservative number for MVP 0.3 - easy to
# retune later once we know how much real history we actually accumulate.
# See "Minimum Data Requirement" in docs/PRODUCT_SPEC.md.
MIN_HISTORY_OBSERVATIONS = 5


def compute_statistics(prices: list[float]) -> PriceStatistics:
    """Summarize a list of observed prices. Raises ValueError on an empty
    list - callers with a minimum-data policy should check that first.

    Median (not mean) is what MIN_HISTORY_OBSERVATIONS-gated baselines are
    built on elsewhere (see get_historical_baseline) - flight prices have
    occasional extreme outliers (e.g. a single first-class fare mixed into
    otherwise-economy observations) that would drag a mean far from what
    "normal" actually looks like, while the median stays robust. Example:
    observations 170/180/175/185/800 -> mean is pulled way up by the 800
    outlier, but the median (180) still reflects the typical price.
    """
    if not prices:
        raise ValueError("compute_statistics requires at least one price")

    sorted_prices = sorted(prices)
    n = len(sorted_prices)

    if n >= 2:
        p25, _, p75 = stats.quantiles(sorted_prices, n=4, method="inclusive")
        stdev = stats.stdev(sorted_prices)
    else:
        p25 = p75 = sorted_prices[0]
        stdev = None

    return PriceStatistics(
        observation_count=n,
        minimum=sorted_prices[0],
        maximum=sorted_prices[-1],
        mean=stats.fmean(sorted_prices),
        median=stats.median(sorted_prices),
        p25=p25,
        p75=p75,
        stdev=stdev,
    )


def classify_position(current_price: float, statistics_: PriceStatistics) -> HistoricalPosition:
    """Where does `current_price` sit relative to our own observed
    interquartile range (p25-p75)? Deliberately simple, not a score."""
    if current_price < statistics_.p25:
        return HistoricalPosition.BELOW_HISTORY
    if current_price > statistics_.p75:
        return HistoricalPosition.ABOVE_HISTORY
    return HistoricalPosition.WITHIN_HISTORY


def percent_diff_from_median(current_price: float, statistics_: PriceStatistics) -> float:
    """Negative = current_price is cheaper than our median."""
    if statistics_.median == 0:
        return 0.0
    return (current_price - statistics_.median) / statistics_.median * 100


def get_historical_baseline(
    repository: PriceHistoryRepository,
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date,
    trip_type: TripType,
    currency: str,
    current_price: float,
) -> HistoricalBaseline | None:
    """Our own historical baseline for one exact route/date/trip-type/
    currency group (see "Route Baseline" in docs/PRODUCT_SPEC.md for why
    this grouping is deliberately strict for MVP 0.3), or None if we don't
    have enough of our own data (< MIN_HISTORY_OBSERVATIONS) to responsibly
    call anything a "normal price" - never guessed.
    """
    route_statistics = repository.get_route_statistics(
        origin, destination, departure_date, return_date, trip_type, currency
    )
    if route_statistics is None or route_statistics.observation_count < MIN_HISTORY_OBSERVATIONS:
        return None

    return HistoricalBaseline(
        statistics=route_statistics,
        position=classify_position(current_price, route_statistics),
        percent_diff_from_median=percent_diff_from_median(current_price, route_statistics),
    )
