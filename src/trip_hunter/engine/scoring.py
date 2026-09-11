"""Rule-based, fully transparent trip score between 0 and 100.

No machine learning, no hidden weights - every point is traceable to a
named factor in `DealScore.breakdown`. See docs/PRODUCT_SPEC.md for the
rationale behind each weight.
"""

from __future__ import annotations

from trip_hunter.models import DealScore

TRIP_SAVINGS_PCT_MAX_POINTS = 50.0
TRIP_SAVINGS_PCT_SATURATION = 0.60  # 60%+ overall savings = full points

FLIGHT_SAVINGS_PCT_MAX_POINTS = 20.0
FLIGHT_SAVINGS_PCT_SATURATION = 0.60

HOTEL_SAVINGS_PCT_MAX_POINTS = 15.0
HOTEL_SAVINGS_PCT_SATURATION = 0.60

ABSOLUTE_SAVINGS_MAX_POINTS = 10.0
ABSOLUTE_SAVINGS_SATURATION = 300.0  # EUR saved for full points

DIRECT_FLIGHT_BONUS_POINTS = 5.0
ONE_STOP_BONUS_POINTS = 2.0


def _scaled(value: float, saturation: float, max_points: float) -> float:
    if saturation <= 0:
        return 0.0
    return min(max(value, 0.0) / saturation, 1.0) * max_points


def score_trip(
    *,
    trip_savings_percentage: float,
    flight_savings_percentage: float,
    hotel_savings_percentage: float,
    savings_absolute: float,
    flight_stops: int,
) -> DealScore:
    if flight_stops == 0:
        convenience_points = DIRECT_FLIGHT_BONUS_POINTS
    elif flight_stops == 1:
        convenience_points = ONE_STOP_BONUS_POINTS
    else:
        convenience_points = 0.0

    breakdown = {
        "trip_savings_pct": round(
            _scaled(trip_savings_percentage, TRIP_SAVINGS_PCT_SATURATION, TRIP_SAVINGS_PCT_MAX_POINTS),
            2,
        ),
        "flight_savings_pct": round(
            _scaled(
                flight_savings_percentage, FLIGHT_SAVINGS_PCT_SATURATION, FLIGHT_SAVINGS_PCT_MAX_POINTS
            ),
            2,
        ),
        "hotel_savings_pct": round(
            _scaled(
                hotel_savings_percentage, HOTEL_SAVINGS_PCT_SATURATION, HOTEL_SAVINGS_PCT_MAX_POINTS
            ),
            2,
        ),
        "absolute_savings": round(
            _scaled(savings_absolute, ABSOLUTE_SAVINGS_SATURATION, ABSOLUTE_SAVINGS_MAX_POINTS), 2
        ),
        "flight_convenience": convenience_points,
    }

    total = round(sum(breakdown.values()))
    total = max(0, min(100, total))

    return DealScore(total=total, breakdown=breakdown)
