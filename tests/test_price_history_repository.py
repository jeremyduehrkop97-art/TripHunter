from __future__ import annotations

from datetime import date, datetime, timezone

from trip_hunter.models import FlightOffer, PriceObservation, TripType
from trip_hunter.price_history_repository import (
    DEFAULT_DB_PATH,
    PriceHistoryRepository,
    observation_from_flight_offer,
)

_ORIGIN = "HAM"
_DESTINATION = "PMI"
_DEPARTURE = date(2026, 10, 2)
_RETURN = date(2026, 10, 7)
_CURRENCY = "EUR"


def _observation(
    price: float,
    observed_at: datetime,
    *,
    origin: str = _ORIGIN,
    destination: str = _DESTINATION,
    departure_date: date = _DEPARTURE,
    return_date: date = _RETURN,
    trip_type: TripType = TripType.ROUND_TRIP,
    currency: str = _CURRENCY,
    provider: str = "test",
):
    return PriceObservation(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        trip_type=trip_type,
        price=price,
        currency=currency,
        provider=provider,
        stops=0,
        airline="Testair",
        observed_at=observed_at,
    )


def test_db_file_is_created(tmp_path):
    db_path = tmp_path / "history.db"
    assert not db_path.exists()

    PriceHistoryRepository(db_path=db_path)

    assert db_path.exists()


def test_add_and_read_observation(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    observed_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    inserted = repo.add_observation(_observation(180.0, observed_at))
    assert inserted is True

    observations = repo.get_observations(
        _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )
    assert len(observations) == 1
    assert observations[0].price == 180.0
    assert observations[0].origin == _ORIGIN
    assert observations[0].trip_type == TripType.ROUND_TRIP
    assert observations[0].observed_at == observed_at


def test_dedup_same_price_same_day_is_not_duplicated(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    morning = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)

    first = repo.add_observation(_observation(180.0, morning))
    second = repo.add_observation(_observation(180.0, evening))  # same day, same price

    assert first is True
    assert second is False

    observations = repo.get_observations(
        _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )
    assert len(observations) == 1


def test_different_price_same_day_is_stored_separately(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    morning = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)

    repo.add_observation(_observation(180.0, morning))
    repo.add_observation(_observation(150.0, evening))  # price dropped same day

    observations = repo.get_observations(
        _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )
    assert len(observations) == 2


def test_same_price_different_day_is_stored_separately(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    day1 = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)

    repo.add_observation(_observation(180.0, day1))
    repo.add_observation(_observation(180.0, day2))

    observations = repo.get_observations(
        _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )
    assert len(observations) == 2


def test_different_routes_are_kept_separate(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    observed_at = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)

    repo.add_observation(_observation(180.0, observed_at, destination="PMI"))
    repo.add_observation(_observation(220.0, observed_at, destination="AGP"))

    pmi_observations = repo.get_observations(
        _ORIGIN, "PMI", _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )
    agp_observations = repo.get_observations(
        _ORIGIN, "AGP", _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )

    assert [o.price for o in pmi_observations] == [180.0]
    assert [o.price for o in agp_observations] == [220.0]


def test_different_travel_dates_are_kept_separate(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    observed_at = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)

    repo.add_observation(_observation(180.0, observed_at, departure_date=date(2026, 10, 2)))
    repo.add_observation(_observation(300.0, observed_at, departure_date=date(2026, 12, 24)))

    october_observations = repo.get_observations(
        _ORIGIN, _DESTINATION, date(2026, 10, 2), _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )
    december_observations = repo.get_observations(
        _ORIGIN, _DESTINATION, date(2026, 12, 24), _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )

    assert [o.price for o in october_observations] == [180.0]
    assert [o.price for o in december_observations] == [300.0]


def test_one_way_and_round_trip_are_kept_separate(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    observed_at = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)

    repo.add_observation(_observation(180.0, observed_at, trip_type=TripType.ROUND_TRIP))
    repo.add_observation(_observation(95.0, observed_at, trip_type=TripType.ONE_WAY))

    round_trip = repo.get_observations(
        _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )
    one_way = repo.get_observations(
        _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ONE_WAY, _CURRENCY
    )

    assert [o.price for o in round_trip] == [180.0]
    assert [o.price for o in one_way] == [95.0]


def test_get_route_statistics_none_when_no_observations(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")

    stats = repo.get_route_statistics(
        _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )

    assert stats is None


def test_get_route_statistics_summarizes_stored_prices(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "history.db")
    for i, price in enumerate([180.0, 175.0, 190.0]):
        repo.add_observation(
            _observation(price, datetime(2026, 8, 1 + i, 8, 0, tzinfo=timezone.utc))
        )

    stats = repo.get_route_statistics(
        _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
    )

    assert stats is not None
    assert stats.observation_count == 3
    assert stats.median == 180.0


def test_observation_from_flight_offer_hook():
    """The Schritt-12 hook: converts a normalized FlightOffer into a
    PriceObservation, not wired into any live search automatically."""
    offer = FlightOffer(
        origin=_ORIGIN,
        destination=_DESTINATION,
        departure_date=_DEPARTURE,
        return_date=_RETURN,
        price=184.0,
        currency=_CURRENCY,
        airline="Vueling",
        stops=1,
        provider="serpapi_google_flights",
    )
    observed_at = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)

    observation = observation_from_flight_offer(offer, TripType.ROUND_TRIP, observed_at=observed_at)

    assert observation.origin == _ORIGIN
    assert observation.destination == _DESTINATION
    assert observation.price == 184.0
    assert observation.trip_type == TripType.ROUND_TRIP
    assert observation.provider == "serpapi_google_flights"
    assert observation.airline == "Vueling"
    assert observation.observed_at == observed_at


def test_default_db_path_is_gitignored():
    """The default runtime database must never be accidentally committed -
    see docs/ARCHITECTURE.md."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    gitignore_content = (repo_root / ".gitignore").read_text(encoding="utf-8")

    db_filename = DEFAULT_DB_PATH.name
    assert db_filename in gitignore_content or "data/*.db" in gitignore_content
