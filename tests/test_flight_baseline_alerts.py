"""Flight deals = discount against the route baseline (>= 30% AND >= 25 EUR),
never flight/landing times."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trip_hunter.accommodation_price_history_repository import (
    AccommodationPriceHistoryRepository,
    observation_from_accommodation_offer,
)
from trip_hunter.build_newsletter import DEFAULT_INSTANT_ALERT_CRITERIA, DEFAULT_TIER_3_CRITERIA
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.engine.deal_filters import filter_deals
from trip_hunter.engine.flight_deal_detector import (
    MIN_FLIGHT_DROP_SAVINGS_EUR,
    assess_flight,
    assess_flight_price_insight,
)
from trip_hunter.models import AccommodationOffer, DealType, FlightOffer, PriceInsight, TripType
from trip_hunter.price_history_repository import PriceHistoryRepository, observation_from_flight_offer
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.flight_provider import FlightProvider
from trip_hunter.providers.null_accommodation_provider import NullAccommodationProvider

_DEP, _RET = date(2026, 10, 2), date(2026, 10, 7)


def _flight(price: float, *, airline="Vueling", departure_time=None, return_time=None) -> FlightOffer:
    return FlightOffer(
        origin="HAM", destination="PMI", departure_date=_DEP, return_date=_RET, price=price,
        currency="EUR", airline=airline, stops=0, provider="test",
        departure_time=departure_time, return_time=return_time,
    )


# --- the rule itself: >= 30% AND >= 25 EUR -------------------------------------


def test_spot_price_equal_to_the_baseline_is_ignored():
    assert assess_flight(_flight(100.0), typical_price=100.0).deal_type is None


def test_a_big_drop_is_a_flight_drop():
    result = assess_flight(_flight(130.0), typical_price=200.0)  # -35%, -70 EUR
    assert result.deal_type is DealType.FLIGHT_DROP
    assert (result.savings_absolute, round(result.savings_percentage, 2)) == (70.0, 0.35)


@pytest.mark.parametrize(
    "typical, price, expected",
    [
        (100.0, 70.0, DealType.FLIGHT_DROP),      # exactly 30%, 30 EUR
        (80.0, 55.0, DealType.FLIGHT_DROP),       # 31.25%, exactly 25 EUR
        (200.0, 141.0, DealType.UNUSUALLY_LOW),   # 29.5%
        (80.0, 56.0, DealType.UNUSUALLY_LOW),     # exactly 30% but only 24 EUR
        (60.0, 42.0, DealType.UNUSUALLY_LOW),     # 30% but 18 EUR: noise
        (20.0, 10.0, DealType.UNUSUALLY_LOW),     # 50% off a 20 EUR flight: 10 EUR of noise
        (200.0, 142.0, DealType.UNUSUALLY_LOW),   # 29%
        (100.0, 20.0, DealType.ERROR_FARE),       # 80%, 80 EUR
        (30.0, 8.0, DealType.UNUSUALLY_LOW),      # 73% but 22 EUR: not an error fare either
        (100.0, 90.0, None),                      # 10%
    ],
)
def test_percentage_and_absolute_thresholds(typical, price, expected):
    assert assess_flight(_flight(price), typical_price=typical).deal_type is expected


def test_the_25_eur_floor_is_the_documented_constant():
    assert MIN_FLIGHT_DROP_SAVINGS_EUR == 25.0


def test_provider_insight_path_obeys_the_25_eur_floor_too():
    insight = PriceInsight(provider_lowest_price=40.0, typical_price_low=50.0, typical_price_high=70.0,
                           price_level="typical", source="google_flights")  # typical mid 60
    assert assess_flight_price_insight(_flight(40.0), insight).deal_type is DealType.UNUSUALLY_LOW  # -33%, 20 EUR
    assert assess_flight_price_insight(_flight(30.0), insight).deal_type is DealType.FLIGHT_DROP    # -50%, 30 EUR


def test_unusually_low_flights_are_no_longer_alert_worthy():
    assert DealType.UNUSUALLY_LOW not in DEFAULT_INSTANT_ALERT_CRITERIA.allowed_deal_types
    assert DealType.UNUSUALLY_LOW not in DEFAULT_TIER_3_CRITERIA.allowed_deal_types


# --- through the engine, against our own route history -------------------------


class _Flights(FlightProvider):
    def __init__(self, offer):
        self.offer = offer

    def search_flights(self, origin, destination, earliest_departure, latest_departure, return_date=None):
        return [self.offer]

    def get_typical_price(self, *a):
        return None

    def get_price_insight(self, *a):
        return None


class _Hotel(AccommodationProvider):
    def __init__(self, price):
        self.price = price

    def search_accommodations(self, destination, check_in, check_out):
        return [AccommodationOffer(destination="PMI", check_in=check_in, check_out=check_out, total_price=self.price,
                                   currency="EUR", name="Hotel", rating=4.2, provider="test")]

    def get_typical_total_price(self, *a):
        return None


def _history(tmp_path, prices, *, airlines=None) -> PriceHistoryRepository:
    repo = PriceHistoryRepository(tmp_path / "f.db")
    for i, price in enumerate(prices, start=1):
        airline = (airlines or ["Vueling"] * len(prices))[i - 1]
        repo.add_observation(
            observation_from_flight_offer(_flight(price, airline=airline), TripType.ROUND_TRIP,
                                          observed_at=datetime(2026, 8, i, 10, tzinfo=timezone.utc))
        )
    return repo


def _find(engine):
    return engine.find_trip_deals("HAM", "PMI", _DEP, _DEP, return_date=_RET)


def test_regular_price_equal_to_the_baseline_produces_no_deal_and_no_alert(tmp_path):
    repo = _history(tmp_path, [100.0] * 5)
    deals = _find(DealEngine(_Flights(_flight(100.0)), NullAccommodationProvider(), repo))

    assert deals == []


def test_a_route_price_drop_over_30_percent_triggers_an_alert_worthy_deal(tmp_path):
    repo = _history(tmp_path, [200.0] * 5)
    deals = _find(DealEngine(_Flights(_flight(130.0)), NullAccommodationProvider(), repo))

    assert [d.deal_type for d in deals] == [DealType.FLIGHT_DROP]
    assert deals[0].savings_absolute == 70.0
    # ...and it really reaches the alert stage: a 35% drop scores only ~48,
    # so the instant-alert criteria must not gate on the score.
    assert deals[0].score.total < 70
    assert filter_deals(deals, DEFAULT_INSTANT_ALERT_CRITERIA) == deals


def test_a_drop_below_the_absolute_floor_is_not_alert_worthy(tmp_path):
    repo = _history(tmp_path, [60.0] * 5)
    deals = _find(DealEngine(_Flights(_flight(42.0)), NullAccommodationProvider(), repo))  # -30%, -18 EUR

    assert all(d.deal_type is not DealType.FLIGHT_DROP for d in deals)
    assert filter_deals(deals, DEFAULT_INSTANT_ALERT_CRITERIA) == []


def test_baseline_ignores_the_airline(tmp_path):
    repo = _history(tmp_path, [200.0] * 5, airlines=["Vueling", "Ryanair", "Lufthansa", "easyJet", "Eurowings"])
    deals = _find(DealEngine(_Flights(_flight(120.0, airline="Iberia")), NullAccommodationProvider(), repo))

    assert [d.deal_type for d in deals] == [DealType.FLIGHT_DROP]


def test_baseline_is_the_median_so_one_outlier_does_not_move_it(tmp_path):
    repo = _history(tmp_path, [200.0, 200.0, 800.0, 200.0, 200.0])
    deals = _find(DealEngine(_Flights(_flight(130.0)), NullAccommodationProvider(), repo))

    assert deals[0].expected_flight_price == 200.0


def test_flight_and_landing_times_play_no_role(tmp_path):
    repo = _history(tmp_path, [200.0] * 5)
    for departure, arrival in [("03:10", "23:55"), ("12:00", "13:30"), (None, None)]:
        deals = _find(DealEngine(_Flights(_flight(130.0, departure_time=departure, return_time=arrival)),
                                 NullAccommodationProvider(), repo))
        assert [d.deal_type for d in deals] == [DealType.FLIGHT_DROP], (departure, arrival)


def test_too_little_history_gives_no_baseline_and_no_alert(tmp_path):
    repo = _history(tmp_path, [200.0] * 3)  # fewer than the minimum snapshots
    deals = _find(DealEngine(_Flights(_flight(100.0)), NullAccommodationProvider(), repo))

    assert [d.deal_type for d in deals] == [DealType.BASELINE_UNAVAILABLE]
    assert filter_deals(deals, DEFAULT_INSTANT_ALERT_CRITERIA) == []
    assert filter_deals(deals, DEFAULT_TIER_3_CRITERIA) == []


def test_a_great_hotel_does_not_turn_an_ordinary_flight_into_a_combined_alert(tmp_path):
    repo = _history(tmp_path, [100.0] * 5)
    hotels = AccommodationPriceHistoryRepository(tmp_path / "h.db")
    for i in range(1, 6):
        hotels.add_observation(observation_from_accommodation_offer(
            AccommodationOffer(destination="PMI", check_in=_DEP, check_out=_RET, total_price=400.0, currency="EUR",
                               name="H", rating=4.2, provider="test"),
            observed_at=datetime(2026, 8, i, 10, tzinfo=timezone.utc)))

    # Flight only 15% below baseline (UNUSUALLY_LOW), hotel 75% below its baseline.
    deals = _find(DealEngine(_Flights(_flight(85.0)), _Hotel(100.0), repo, hotels))

    assert [d.deal_type for d in deals] == [DealType.UNUSUALLY_LOW]
    assert filter_deals(deals, DEFAULT_INSTANT_ALERT_CRITERIA) == []


def test_a_real_flight_drop_with_a_great_hotel_is_still_a_combined_trip_drop(tmp_path):
    repo = _history(tmp_path, [300.0] * 5)
    hotels = AccommodationPriceHistoryRepository(tmp_path / "h.db")
    for i in range(1, 6):
        hotels.add_observation(observation_from_accommodation_offer(
            AccommodationOffer(destination="PMI", check_in=_DEP, check_out=_RET, total_price=400.0, currency="EUR",
                               name="H", rating=4.2, provider="test"),
            observed_at=datetime(2026, 8, i, 10, tzinfo=timezone.utc)))

    deals = _find(DealEngine(_Flights(_flight(150.0)), _Hotel(200.0), repo, hotels))

    assert [d.deal_type for d in deals] == [DealType.COMBINED_TRIP_DROP]
