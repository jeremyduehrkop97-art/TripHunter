from __future__ import annotations

from datetime import date

import pytest

from trip_hunter.engine.deal_filters import DealFilterCriteria, filter_deals, matches
from trip_hunter.models import AccommodationOffer, Deal, DealScore, DealType, FlightOffer

_FRI = date(2026, 10, 2)  # a real Friday
_SUN = date(2026, 10, 4)  # the following Sunday (2 nights)
_TUE = date(2026, 10, 6)  # a real Tuesday
_THU = date(2026, 10, 8)  # the following Thursday (2 nights)


def _flight(
    price: float = 100.0,
    *,
    departure_date: date = _FRI,
    return_date: date = _SUN,
    stops: int = 0,
) -> FlightOffer:
    return FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=departure_date,
        return_date=return_date,
        price=price,
        currency="EUR",
        airline="Testair",
        stops=stops,
        provider="test",
    )


def _accommodation(total_price: float = 50.0, *, check_in=_FRI, check_out=_SUN) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI",
        check_in=check_in,
        check_out=check_out,
        total_price=total_price,
        currency="EUR",
        name="Test Hotel",
        rating=4.0,
        provider="test",
    )


def _deal(
    *,
    flight_price: float = 100.0,
    accommodation: AccommodationOffer | None = None,
    departure_date: date = _FRI,
    return_date: date = _SUN,
    score: int | None = 70,
    deal_type: DealType = DealType.FLIGHT_DROP,
) -> Deal:
    flight = _flight(flight_price, departure_date=departure_date, return_date=return_date)
    deal_score = DealScore(total=score, breakdown={}) if score is not None else None
    return Deal(
        deal_type=deal_type,
        flight=flight,
        accommodation=accommodation,
        expected_flight_price=200.0,
        expected_accommodation_price=100.0 if accommodation else None,
        score=deal_score,
        savings_absolute=100.0,
        savings_percentage=0.5,
    )


# --- max_total_price --------------------------------------------------------


def test_max_total_price_accepts_flight_only_deal_under_budget():
    deal = _deal(flight_price=200.0)
    criteria = DealFilterCriteria(max_total_price=250.0)

    assert matches(deal, criteria) is True


def test_max_total_price_rejects_flight_only_deal_over_budget():
    deal = _deal(flight_price=260.0)
    criteria = DealFilterCriteria(max_total_price=250.0)

    assert matches(deal, criteria) is False


def test_max_total_price_uses_combined_flight_and_accommodation_price():
    deal = _deal(flight_price=150.0, accommodation=_accommodation(150.0))  # total 300
    criteria = DealFilterCriteria(max_total_price=250.0)

    assert matches(deal, criteria) is False


def test_max_total_price_none_means_no_constraint():
    deal = _deal(flight_price=10_000.0)
    criteria = DealFilterCriteria(max_total_price=None)

    assert matches(deal, criteria) is True


# --- duration-aware cap: weekend_max_total / max_price_per_night -------------


def test_weekend_shaped_trip_uses_the_flat_weekend_total():
    deal = _deal(  # 2 nights <= default weekend_max_nights (3)
        flight_price=100.0, accommodation=_accommodation(100.0), departure_date=_FRI, return_date=_SUN
    )
    under = DealFilterCriteria(weekend_max_total=250.0)
    over = DealFilterCriteria(weekend_max_total=150.0)

    assert matches(deal, under) is True
    assert matches(deal, over) is False


def test_long_trip_is_never_judged_against_weekend_max_total():
    """The whole point of the duration-aware cap: a week-long trip must
    not be rejected by a weekend-sized flat total, even a wildly cheap
    flight would blow way past it on nights alone."""
    week = date(2026, 10, 9)  # 7 nights from _FRI
    deal = _deal(
        flight_price=50.0, accommodation=_accommodation(7 * 60.0, check_out=week),
        departure_date=_FRI, return_date=week,
    )
    criteria = DealFilterCriteria(weekend_max_total=250.0, max_price_per_night=60.0)

    assert matches(deal, criteria) is True


def test_long_trip_is_judged_on_accommodation_price_per_night():
    week = date(2026, 10, 9)  # 7 nights from _FRI

    fair = _deal(accommodation=_accommodation(7 * 60.0, check_out=week), departure_date=_FRI, return_date=week)
    expensive = _deal(accommodation=_accommodation(7 * 90.0, check_out=week), departure_date=_FRI, return_date=week)

    criteria = DealFilterCriteria(max_price_per_night=60.0)

    assert matches(fair, criteria) is True
    assert matches(expensive, criteria) is False


def test_long_trip_with_no_accommodation_is_not_constrained_by_price_per_night():
    """max_price_per_night can only judge what it can actually see - a
    flight-only Deal (no accommodation attached) is never guessed at."""
    week = date(2026, 10, 9)  # 7 nights from _FRI
    deal = _deal(accommodation=None, departure_date=_FRI, return_date=week)
    criteria = DealFilterCriteria(max_price_per_night=60.0)

    assert matches(deal, criteria) is True


def test_weekend_max_nights_boundary_is_inclusive():
    three_nights = date(2026, 10, 5)  # exactly weekend_max_nights (default 3)
    deal = _deal(accommodation=_accommodation(400.0), departure_date=_FRI, return_date=three_nights)

    at_boundary = DealFilterCriteria(weekend_max_total=250.0)  # 3 nights -> weekend branch, 400 > 250
    assert matches(deal, at_boundary) is False

    four_nights = date(2026, 10, 6)  # one night past the boundary -> long-trip branch
    deal_long = _deal(accommodation=_accommodation(400.0), departure_date=_FRI, return_date=four_nights)
    long_branch = DealFilterCriteria(weekend_max_total=250.0, max_price_per_night=200.0)  # 400/4=100 <= 200
    assert matches(deal_long, long_branch) is True


def test_weekend_max_nights_is_configurable():
    four_nights = date(2026, 10, 6)
    deal = _deal(accommodation=_accommodation(400.0), departure_date=_FRI, return_date=four_nights)

    criteria = DealFilterCriteria(weekend_max_total=250.0, weekend_max_nights=4)

    assert matches(deal, criteria) is False  # now treated as "weekend-shaped", 400 > 250


def test_both_weekend_max_total_and_max_price_per_night_none_imposes_no_constraint():
    deal = _deal(flight_price=1.0, accommodation=_accommodation(1_000_000.0))

    criteria = DealFilterCriteria(weekend_max_total=None, max_price_per_night=None)

    assert matches(deal, criteria) is True


def test_duration_aware_cap_composes_with_max_total_price_when_both_are_set():
    """Both fields can coexist (AND) - a caller migrating gradually isn't
    forced to choose exactly one mechanism."""
    deal = _deal(flight_price=1000.0, accommodation=_accommodation(50.0), departure_date=_FRI, return_date=_SUN)

    criteria = DealFilterCriteria(max_total_price=250.0, weekend_max_total=10_000.0)

    assert matches(deal, criteria) is False  # fails max_total_price even though weekend_max_total is huge


# --- nights ------------------------------------------------------------------


def test_min_nights_rejects_too_short_trip():
    deal = _deal(departure_date=_FRI, return_date=_SUN)  # 2 nights
    criteria = DealFilterCriteria(min_nights=3)

    assert matches(deal, criteria) is False


def test_max_nights_rejects_too_long_trip():
    long_return = date(2026, 10, 10)  # 8 nights from _FRI
    deal = _deal(departure_date=_FRI, return_date=long_return)
    criteria = DealFilterCriteria(max_nights=4)

    assert matches(deal, criteria) is False


def test_nights_within_range_is_accepted():
    deal = _deal(departure_date=_FRI, return_date=_SUN)  # 2 nights
    criteria = DealFilterCriteria(min_nights=2, max_nights=4)

    assert matches(deal, criteria) is True


def test_min_nights_greater_than_max_nights_is_rejected_at_construction():
    with pytest.raises(ValueError):
        DealFilterCriteria(min_nights=5, max_nights=2)


# --- weekend_trip_only ---------------------------------------------------------


def test_weekend_trip_only_accepts_friday_to_sunday():
    deal = _deal(departure_date=_FRI, return_date=_SUN)
    criteria = DealFilterCriteria(weekend_trip_only=True)

    assert matches(deal, criteria) is True


def test_weekend_trip_only_rejects_tuesday_to_thursday():
    """Same 2-night duration as the Fri->Sun case, but the wrong weekdays -
    proves weekend_trip_only checks actual weekdays, not just a 2-night
    duration."""
    deal = _deal(departure_date=_TUE, return_date=_THU)
    criteria = DealFilterCriteria(weekend_trip_only=True)

    assert matches(deal, criteria) is False


def test_weekend_trip_only_rejects_friday_to_monday():
    """Right start day, wrong end day (3 nights, not the strict weekend
    shape) - departure Friday must pair with a Sunday return, not any
    Friday start."""
    monday = date(2026, 10, 5)
    deal = _deal(departure_date=_FRI, return_date=monday)
    criteria = DealFilterCriteria(weekend_trip_only=True)

    assert matches(deal, criteria) is False


def test_weekend_trip_only_false_imposes_no_weekday_constraint():
    deal = _deal(departure_date=_TUE, return_date=_THU)
    criteria = DealFilterCriteria(weekend_trip_only=False)

    assert matches(deal, criteria) is True


# --- min_score -----------------------------------------------------------------


def test_min_score_accepts_deal_at_or_above_threshold():
    deal = _deal(score=60)
    criteria = DealFilterCriteria(min_score=60)

    assert matches(deal, criteria) is True


def test_min_score_rejects_deal_below_threshold():
    deal = _deal(score=59)
    criteria = DealFilterCriteria(min_score=60)

    assert matches(deal, criteria) is False


def test_min_score_rejects_deal_with_no_score_at_all():
    """A Deal with score=None (e.g. BASELINE_UNAVAILABLE/PRICE_INCOMPLETE)
    must never be silently let through a min_score filter."""
    deal = _deal(score=None)
    criteria = DealFilterCriteria(min_score=1)

    assert matches(deal, criteria) is False


# --- allowed_deal_types ----------------------------------------------------------


def test_allowed_deal_types_accepts_listed_type():
    deal = _deal(deal_type=DealType.COMBINED_TRIP_DROP)
    criteria = DealFilterCriteria(allowed_deal_types=frozenset({DealType.COMBINED_TRIP_DROP}))

    assert matches(deal, criteria) is True


def test_allowed_deal_types_rejects_unlisted_type():
    deal = _deal(deal_type=DealType.UNUSUALLY_LOW)
    criteria = DealFilterCriteria(allowed_deal_types=frozenset({DealType.COMBINED_TRIP_DROP}))

    assert matches(deal, criteria) is False


def test_allowed_deal_types_none_means_any_type():
    deal = _deal(deal_type=DealType.HOTEL_DROP)
    criteria = DealFilterCriteria(allowed_deal_types=None)

    assert matches(deal, criteria) is True


def test_allowed_deal_types_can_include_multiple_types():
    criteria = DealFilterCriteria(
        allowed_deal_types=frozenset({DealType.COMBINED_TRIP_DROP, DealType.UNUSUALLY_LOW})
    )

    assert matches(_deal(deal_type=DealType.COMBINED_TRIP_DROP), criteria) is True
    assert matches(_deal(deal_type=DealType.UNUSUALLY_LOW), criteria) is True
    assert matches(_deal(deal_type=DealType.FLIGHT_DROP), criteria) is False


# --- combined criteria / filter_deals ----------------------------------------------


def test_all_criteria_compose_with_and():
    good = _deal(flight_price=100.0, departure_date=_FRI, return_date=_SUN, score=80)
    bad_price = _deal(flight_price=500.0, departure_date=_FRI, return_date=_SUN, score=80)
    bad_weekday = _deal(flight_price=100.0, departure_date=_TUE, return_date=_THU, score=80)
    bad_score = _deal(flight_price=100.0, departure_date=_FRI, return_date=_SUN, score=10)

    criteria = DealFilterCriteria(max_total_price=250.0, weekend_trip_only=True, min_score=60)

    assert filter_deals([good, bad_price, bad_weekday, bad_score], criteria) == [good]


def test_filter_deals_preserves_order():
    first = _deal(flight_price=50.0, score=80)
    second = _deal(flight_price=60.0, score=80)
    third = _deal(flight_price=70.0, score=80)

    result = filter_deals([third, first, second], DealFilterCriteria(max_total_price=250.0))

    assert result == [third, first, second]


def test_filter_deals_returns_empty_list_when_nothing_matches():
    deal = _deal(flight_price=1000.0)

    assert filter_deals([deal], DealFilterCriteria(max_total_price=10.0)) == []


# --- presets -----------------------------------------------------------------


def test_weekend_getaway_preset_shape():
    criteria = DealFilterCriteria.weekend_getaway(max_total_price=250.0)

    assert criteria.max_total_price == 250.0
    assert criteria.weekend_trip_only is True
    assert criteria.min_score == 60
    assert criteria.min_nights is None
    assert criteria.max_nights is None


def test_spontaneous_trip_preset_shape():
    criteria = DealFilterCriteria.spontaneous_trip(max_total_price=300.0)

    assert criteria.max_total_price == 300.0
    assert criteria.weekend_trip_only is False
    assert criteria.min_nights == 2
    assert criteria.max_nights == 4
    assert criteria.min_score == 60
