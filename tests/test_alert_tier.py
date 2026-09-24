from __future__ import annotations

from datetime import date

from trip_hunter.engine.alert_tier import AlertTier, classify_alert_tier, is_free_channel_eligible
from trip_hunter.models import Deal, DealScore, DealType, FlightOffer

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 4)


def _flight() -> FlightOffer:
    return FlightOffer(
        origin="HAM", destination="PMI", departure_date=_FRI, return_date=_SUN,
        price=79.0, currency="EUR", airline="Testair", stops=0, provider="test",
    )


def _deal(
    *, deal_type: DealType, savings_percentage: float | None, score: int | None = 70
) -> Deal:
    return Deal(
        deal_type=deal_type,
        flight=_flight(),
        accommodation=None,
        expected_flight_price=140.0,
        expected_accommodation_price=None,
        score=DealScore(total=score, breakdown={}) if score is not None else None,
        savings_absolute=61.0,
        savings_percentage=savings_percentage,
    )


# --- classify_alert_tier -------------------------------------------------------


def test_error_fare_deal_type_is_always_tier_1():
    deal = _deal(deal_type=DealType.ERROR_FARE, savings_percentage=0.10)  # low % on purpose
    assert classify_alert_tier(deal) == AlertTier.TIER_1_ERROR_FARE


def test_high_savings_percentage_promotes_a_combined_trip_drop_to_tier_1():
    deal = _deal(deal_type=DealType.COMBINED_TRIP_DROP, savings_percentage=0.65)
    assert classify_alert_tier(deal) == AlertTier.TIER_1_ERROR_FARE


def test_savings_percentage_exactly_at_the_tier_1_threshold_is_tier_1():
    deal = _deal(deal_type=DealType.FLIGHT_DROP, savings_percentage=0.60)
    assert classify_alert_tier(deal) == AlertTier.TIER_1_ERROR_FARE


def test_savings_percentage_just_below_the_tier_1_threshold_falls_to_tier_2():
    deal = _deal(deal_type=DealType.FLIGHT_DROP, savings_percentage=0.5999)
    assert classify_alert_tier(deal) == AlertTier.TIER_2_COMBINED_DROP


def test_flight_drop_is_tier_2():
    deal = _deal(deal_type=DealType.FLIGHT_DROP, savings_percentage=0.35)
    assert classify_alert_tier(deal) == AlertTier.TIER_2_COMBINED_DROP


def test_combined_trip_drop_is_tier_2():
    deal = _deal(deal_type=DealType.COMBINED_TRIP_DROP, savings_percentage=0.40)
    assert classify_alert_tier(deal) == AlertTier.TIER_2_COMBINED_DROP


def test_unusually_low_is_tier_3():
    deal = _deal(deal_type=DealType.UNUSUALLY_LOW, savings_percentage=0.18)
    assert classify_alert_tier(deal) == AlertTier.TIER_3_GOOD_DEAL


def test_hotel_drop_is_tier_3():
    deal = _deal(deal_type=DealType.HOTEL_DROP, savings_percentage=0.28)
    assert classify_alert_tier(deal) == AlertTier.TIER_3_GOOD_DEAL


def test_baseline_unavailable_has_no_tier():
    deal = _deal(deal_type=DealType.BASELINE_UNAVAILABLE, savings_percentage=None, score=None)
    assert classify_alert_tier(deal) is None


def test_price_incomplete_has_no_tier():
    deal = _deal(deal_type=DealType.PRICE_INCOMPLETE, savings_percentage=None, score=None)
    assert classify_alert_tier(deal) is None


def test_none_savings_percentage_does_not_crash_the_tier_1_check():
    """A floor-triggered ERROR_FARE has savings_percentage=None - the >=
    threshold comparison must never raise on that, it should just fall
    through to the deal_type check (which already covers ERROR_FARE)."""
    deal = _deal(deal_type=DealType.ERROR_FARE, savings_percentage=None, score=92)
    assert classify_alert_tier(deal) == AlertTier.TIER_1_ERROR_FARE


# --- is_free_channel_eligible --------------------------------------------------


def test_tier_1_and_tier_2_are_free_channel_eligible():
    assert is_free_channel_eligible(AlertTier.TIER_1_ERROR_FARE) is True
    assert is_free_channel_eligible(AlertTier.TIER_2_COMBINED_DROP) is True


def test_tier_3_is_not_free_channel_eligible():
    assert is_free_channel_eligible(AlertTier.TIER_3_GOOD_DEAL) is False


def test_no_tier_is_not_free_channel_eligible():
    assert is_free_channel_eligible(None) is False
