"""Strict "one alert per flight connection" (alert_history_repository.py)
and best-hotel selection, exercised through the sampler's alert check and
run_sampler."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from trip_hunter.accommodation_price_history_repository import (
    AccommodationPriceHistoryRepository,
    observation_from_accommodation_offer,
)
from trip_hunter.alert_history_repository import (
    AlertHistoryRepository,
    InMemoryAlertHistory,
    flight_key,
)
from trip_hunter.caching import FileCache
from trip_hunter.daily_sampler import _check_and_dispatch_alert, run_sampler
from trip_hunter.build_newsletter import DEFAULT_INSTANT_ALERT_CRITERIA, DEFAULT_TIER_3_CRITERIA
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.models import (
    AccommodationOffer,
    Deal,
    DealScore,
    DealType,
    FlightComparisonGroup,
    FlightOffer,
    TripType,
)
from trip_hunter.price_history_repository import PriceHistoryRepository, observation_from_flight_offer
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.flight_provider import FlightProvider

_DEP, _RET = date(2026, 10, 2), date(2026, 10, 7)
_GROUP = FlightComparisonGroup("HAM", "PMI", _DEP, _RET, TripType.ROUND_TRIP, "EUR")


def _flight(price: float, *, dep=_DEP, ret=_RET) -> FlightOffer:
    return FlightOffer(
        origin="HAM", destination="PMI", departure_date=dep, return_date=ret, price=price,
        currency="EUR", airline="Vueling", stops=0, provider="test",
    )


def _hotel(price: float, name: str, *, rating: float | None = 4.2, description=None) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI", check_in=_DEP, check_out=_RET, total_price=price, currency="EUR",
        name=name, rating=rating, provider="test", description=description,
    )


def _deal(hotel: AccommodationOffer | None = None) -> Deal:
    return Deal(
        deal_type=DealType.FLIGHT_DROP, flight=_flight(100.0), accommodation=hotel,
        expected_flight_price=300.0, expected_accommodation_price=None,
        score=DealScore(total=80, breakdown={}), savings_absolute=200.0, savings_percentage=0.67,
    )


class _Dispatch:
    def __init__(self, result=True):
        self.deals, self._result = [], result

    def __call__(self, deal) -> bool:
        self.deals.append(deal)
        return self._result


def _seed(flight_repo, hotel_repo, *, hotel: AccommodationOffer, latest_flight=100.0, day=1):
    """Baseline 300 EUR x4, then today's much cheaper flight + given hotel."""
    for d in range(1, 5):
        flight_repo.add_observation(
            observation_from_flight_offer(_flight(300.0), TripType.ROUND_TRIP, observed_at=datetime(2026, 8, d, 10, tzinfo=timezone.utc))
        )
    flight_repo.add_observation(
        observation_from_flight_offer(_flight(latest_flight), TripType.ROUND_TRIP, observed_at=datetime(2026, 9, day, 10, tzinfo=timezone.utc))
    )
    hotel_repo.add_observation(
        observation_from_accommodation_offer(hotel, observed_at=datetime(2026, 9, day, 10, tzinfo=timezone.utc))
    )


def _check(flight_repo, hotel_repo, dispatch, history):
    return _check_and_dispatch_alert(
        _GROUP, flight_repo, hotel_repo, alert_criteria=DEFAULT_INSTANT_ALERT_CRITERIA,
        tier3_criteria=DEFAULT_TIER_3_CRITERIA, dispatch_fn=dispatch, alert_history=history,
    )


# --- repository -----------------------------------------------------------------


def test_flight_key_is_origin_destination_and_both_dates():
    assert flight_key(_deal()) == ("HAM", "PMI", _DEP, _RET)
    assert flight_key(_GROUP) == flight_key(_deal())


def test_history_persists_across_instances_and_ignores_repeats(tmp_path):
    path = tmp_path / "t.db"
    first = AlertHistoryRepository(db_path=path)
    assert not first.has_alerted(flight_key(_deal()))

    first.record(_deal(_hotel(100.0, "A")))
    first.record(_deal(_hotel(90.0, "B")))  # same flight, other hotel: first record stands

    second = AlertHistoryRepository(db_path=path)
    assert second.has_alerted(flight_key(_deal()))


def test_different_dates_or_route_are_different_connections(tmp_path):
    history = AlertHistoryRepository(db_path=tmp_path / "t.db")
    history.record(_deal())

    other_dates = FlightComparisonGroup("HAM", "PMI", date(2026, 10, 9), date(2026, 10, 11), TripType.ROUND_TRIP, "EUR")
    other_origin = FlightComparisonGroup("BER", "PMI", _DEP, _RET, TripType.ROUND_TRIP, "EUR")
    assert not history.has_alerted(flight_key(other_dates))
    assert not history.has_alerted(flight_key(other_origin))


def test_in_memory_history_behaves_the_same():
    history = InMemoryAlertHistory()
    assert not history.has_alerted(flight_key(_GROUP))
    history.record(_deal())
    assert history.has_alerted(flight_key(_GROUP))


# --- through the alert check -----------------------------------------------------


def test_second_check_of_the_same_flight_sends_nothing(tmp_path):
    flights, hotels = PriceHistoryRepository(tmp_path / "f.db"), AccommodationPriceHistoryRepository(tmp_path / "h.db")
    _seed(flights, hotels, hotel=_hotel(150.0, "Hotel A"))
    history, dispatch = AlertHistoryRepository(tmp_path / "a.db"), _Dispatch()

    assert _check(flights, hotels, dispatch, history) is True
    assert _check(flights, hotels, dispatch, history) is False

    assert len(dispatch.deals) == 1


def test_same_flight_is_not_posted_again_with_a_different_hotel(tmp_path):
    flights, hotels = PriceHistoryRepository(tmp_path / "f.db"), AccommodationPriceHistoryRepository(tmp_path / "h.db")
    _seed(flights, hotels, hotel=_hotel(150.0, "Hotel A"))
    history, dispatch = AlertHistoryRepository(tmp_path / "a.db"), _Dispatch()
    _check(flights, hotels, dispatch, history)

    # Next day: a new, better hotel observation for the very same flight.
    hotels.add_observation(
        observation_from_accommodation_offer(_hotel(90.0, "Hotel B"), observed_at=datetime(2026, 9, 2, 10, tzinfo=timezone.utc))
    )
    assert _check(flights, hotels, dispatch, history) is False

    assert [d.accommodation.name for d in dispatch.deals] == ["Hotel A"]


def test_failed_dispatch_is_not_recorded_and_retried_later(tmp_path):
    flights, hotels = PriceHistoryRepository(tmp_path / "f.db"), AccommodationPriceHistoryRepository(tmp_path / "h.db")
    _seed(flights, hotels, hotel=_hotel(150.0, "Hotel A"))
    history = AlertHistoryRepository(tmp_path / "a.db")

    assert _check(flights, hotels, _Dispatch(result=False), history) is False
    assert not history.has_alerted(flight_key(_GROUP))

    retry = _Dispatch(result=True)
    assert _check(flights, hotels, retry, history) is True
    assert len(retry.deals) == 1 and history.has_alerted(flight_key(_GROUP))


def test_other_flight_connections_still_alert(tmp_path):
    flights, hotels = PriceHistoryRepository(tmp_path / "f.db"), AccommodationPriceHistoryRepository(tmp_path / "h.db")
    _seed(flights, hotels, hotel=_hotel(150.0, "Hotel A"))
    history = AlertHistoryRepository(tmp_path / "a.db")
    history.record(_deal())  # HAM->PMI on these dates already posted...

    other = FlightComparisonGroup("BER", "PMI", _DEP, _RET, TripType.ROUND_TRIP, "EUR")
    assert history.has_alerted(flight_key(_GROUP)) and not history.has_alerted(flight_key(other))


def test_no_history_means_no_dedup_in_the_low_level_check(tmp_path):
    flights, hotels = PriceHistoryRepository(tmp_path / "f.db"), AccommodationPriceHistoryRepository(tmp_path / "h.db")
    _seed(flights, hotels, hotel=_hotel(150.0, "Hotel A"))
    dispatch = _Dispatch()

    _check(flights, hotels, dispatch, None)
    _check(flights, hotels, dispatch, None)

    assert len(dispatch.deals) == 2


# --- run_sampler: dedup within a run and across runs -------------------------------


class _Flights(FlightProvider):
    def __init__(self, price):
        self.price = price

    def search_flights(self, origin, destination, earliest_departure, latest_departure, return_date=None):
        return [_flight(self.price)]

    def get_typical_price(self, *a):
        return None

    def get_price_insight(self, *a):
        return None


class _Hotels(AccommodationProvider):
    def __init__(self, offers):
        self.offers = offers

    def search_accommodations(self, destination, check_in, check_out):
        return self.offers

    def get_typical_total_price(self, *a):
        return None


def _run(tmp_path, dispatch, history, *, today, price=100.0, hotels=None):
    flights_repo = PriceHistoryRepository(tmp_path / "f.db")
    hotels_repo = AccommodationPriceHistoryRepository(tmp_path / "h.db")
    if not list(flights_repo.get_observations("HAM", "PMI", _DEP, _RET, TripType.ROUND_TRIP, "EUR")):
        _seed(flights_repo, hotels_repo, hotel=_hotel(150.0, "Seed"), latest_flight=300.0, day=1)
    from trip_hunter.models import AccommodationComparisonGroup

    run_sampler(
        _Flights(price), _Hotels(hotels or [_hotel(150.0, "Hotel A")]), flights_repo, hotels_repo,
        FileCache(cache_dir=tmp_path / "cache"),
        flight_targets=[_GROUP],
        hotel_targets=[AccommodationComparisonGroup("PMI", _DEP, _RET, "EUR")],
        today=today, observed_at=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
        dispatch_fn=dispatch, alert_history=history,
    )


def test_flight_and_hotel_targets_of_one_trip_alert_once_per_run(tmp_path):
    dispatch = _Dispatch()
    _run(tmp_path, dispatch, None, today=date(2026, 9, 10))
    assert len(dispatch.deals) == 1


def test_consecutive_daily_runs_alert_the_same_flight_only_once(tmp_path):
    history, dispatch = AlertHistoryRepository(tmp_path / "a.db"), _Dispatch()

    _run(tmp_path, dispatch, history, today=date(2026, 9, 10), hotels=[_hotel(150.0, "Hotel A")])
    _run(tmp_path, dispatch, history, today=date(2026, 9, 11), price=95.0, hotels=[_hotel(80.0, "Hotel B")])
    _run(tmp_path, dispatch, history, today=date(2026, 9, 12), price=90.0, hotels=[_hotel(70.0, "Hotel C")])

    assert len(dispatch.deals) == 1
    assert dispatch.deals[0].accommodation.name == "Hotel A"


# --- best hotel selection in the engine ---------------------------------------------


class _FixedHotels(AccommodationProvider):
    def __init__(self, offers):
        self._offers = offers

    def search_accommodations(self, destination, check_in, check_out):
        return self._offers

    def get_typical_total_price(self, *a):
        return None


def _engine(offers):
    return DealEngine(flight_provider=_Flights(100.0), accommodation_provider=_FixedHotels(offers))


def test_engine_picks_the_cheapest_well_rated_non_dorm_hotel():
    offers = [
        _hotel(40.0, "Dorm Bed", description="8-bed dormitory"),
        _hotel(60.0, "Low Rated", rating=3.4),
        _hotel(180.0, "Pricey"), _hotel(120.0, "Best Match"), _hotel(150.0, "Middle"),
    ]
    chosen, _, _ = _engine(offers)._best_accommodation_for(_flight(100.0))
    assert chosen.name == "Best Match"


def test_engine_attaches_a_low_rated_hotel_only_as_error_fare_fallback():
    chosen, _, _ = _engine([_hotel(60.0, "Low Rated", rating=3.4)])._best_accommodation_for(_flight(100.0))
    assert chosen.name == "Low Rated"  # quality_gate drops it for Tier 2/3 deals; Tier 1 ignores the rating


def test_engine_never_attaches_a_dorm():
    chosen, expected, assessment = _engine([_hotel(40.0, "Dorm", description="dorm")])._best_accommodation_for(_flight(100.0))
    assert (chosen, expected, assessment) == (None, None, None)
