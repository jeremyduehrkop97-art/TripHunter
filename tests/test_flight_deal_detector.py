from __future__ import annotations

from datetime import date

from trip_hunter.engine.flight_deal_detector import assess_flight, assess_flight_price_insight
from trip_hunter.models import DealType, FlightOffer, PriceInsight


def _flight(price: float) -> FlightOffer:
    return FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=date(2026, 10, 2),
        return_date=date(2026, 10, 7),
        price=price,
        currency="EUR",
        airline="Testair",
        stops=0,
        provider="test",
    )


def test_error_fare_detected_above_70_percent_off():
    result = assess_flight(_flight(50.0), typical_price=200.0)
    assert result.deal_type == DealType.ERROR_FARE


def test_flight_drop_detected_between_30_and_70_percent_off():
    result = assess_flight(_flight(79.0), typical_price=180.0)
    assert result.deal_type == DealType.FLIGHT_DROP


def test_unusually_low_detected_between_15_and_30_percent_off():
    result = assess_flight(_flight(150.0), typical_price=180.0)
    assert result.deal_type == DealType.UNUSUALLY_LOW


def test_no_deal_below_threshold():
    result = assess_flight(_flight(175.0), typical_price=180.0)
    assert result.deal_type is None


def test_savings_are_computed_correctly():
    result = assess_flight(_flight(79.0), typical_price=180.0)
    assert result.savings_absolute == 101.0
    assert round(result.savings_percentage, 4) == round(101 / 180, 4)


def test_baseline_unavailable_when_no_typical_price():
    """A real search API that only returns current prices must never be
    silently treated as "no deal" or, worse, scored as a drop - see the
    Baseline Problem in docs/PRODUCT_SPEC.md."""
    result = assess_flight(_flight(89.0), typical_price=None)
    assert result.deal_type == DealType.BASELINE_UNAVAILABLE
    assert result.savings_absolute is None
    assert result.savings_percentage is None


def _insight(low: float | None, high: float | None, price_level: str | None = "low") -> PriceInsight:
    return PriceInsight(
        provider_lowest_price=89.0,
        typical_price_low=low,
        typical_price_high=high,
        price_level=price_level,
        source="google_flights",
    )


def test_price_insight_none_returns_none():
    assert assess_flight_price_insight(_flight(89.0), None) is None


def test_price_insight_missing_typical_range_returns_none():
    insight = _insight(low=None, high=None)
    assert assess_flight_price_insight(_flight(89.0), insight) is None


def test_price_insight_flight_drop_when_well_below_typical_range():
    # Typical 160-220 (midpoint 190), current 89 -> ~53% below -> FLIGHT_DROP.
    insight = _insight(low=160.0, high=220.0)
    result = assess_flight_price_insight(_flight(89.0), insight)
    assert result.deal_type == DealType.FLIGHT_DROP


def test_price_insight_unusually_low_for_a_smaller_gap():
    # Typical 100-110 (midpoint 105), current 89 -> ~15.2% below -> UNUSUALLY_LOW.
    insight = _insight(low=100.0, high=110.0)
    result = assess_flight_price_insight(_flight(89.0), insight)
    assert result.deal_type == DealType.UNUSUALLY_LOW


def test_price_insight_no_deal_when_close_to_typical():
    # Typical 90-95 (midpoint 92.5), current 89 -> ~3.8% below -> no deal.
    insight = _insight(low=90.0, high=95.0)
    result = assess_flight_price_insight(_flight(89.0), insight)
    assert result.deal_type is None


def test_price_insight_never_yields_error_fare_even_for_extreme_savings():
    """Price Insights alone must never trigger ERROR_FARE - that needs its
    own, stricter heuristic which does not exist yet. See docs/PRODUCT_SPEC.md."""
    # Typical 800-1000 (midpoint 900), current 89 -> ~90% below.
    insight = _insight(low=800.0, high=1000.0)
    result = assess_flight_price_insight(_flight(89.0), insight)
    assert result.deal_type == DealType.FLIGHT_DROP
    assert result.deal_type != DealType.ERROR_FARE
