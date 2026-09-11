from __future__ import annotations

from datetime import date, datetime, timezone

from trip_hunter.accommodation_price_history_repository import (
    AccommodationPriceHistoryRepository,
    DEFAULT_DB_PATH,
    observation_from_accommodation_offer,
)
from trip_hunter.models import AccommodationObservation, AccommodationOffer

_DESTINATION = "PMI"
_CHECK_IN = date(2026, 10, 2)
_CHECK_OUT = date(2026, 10, 7)
_CURRENCY = "EUR"


def _observation(
    price: float,
    observed_at: datetime,
    *,
    destination: str = _DESTINATION,
    check_in: date = _CHECK_IN,
    check_out: date = _CHECK_OUT,
    currency: str = _CURRENCY,
    provider: str = "test",
) -> AccommodationObservation:
    return AccommodationObservation(
        destination=destination,
        check_in=check_in,
        check_out=check_out,
        price=price,
        currency=currency,
        provider=provider,
        name="Test Hotel",
        observed_at=observed_at,
    )


def test_db_file_is_created(tmp_path):
    db_path = tmp_path / "history.db"
    assert not db_path.exists()

    AccommodationPriceHistoryRepository(db_path=db_path)

    assert db_path.exists()


def test_add_and_read_observation(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")
    observed_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    inserted = repo.add_observation(_observation(205.0, observed_at))
    assert inserted is True

    observations = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(observations) == 1
    assert observations[0].price == 205.0
    assert observations[0].destination == _DESTINATION
    assert observations[0].observed_at == observed_at
    assert observations[0].name == "Test Hotel"


def test_dedup_same_price_same_day_is_not_duplicated(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")
    morning = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)

    first = repo.add_observation(_observation(205.0, morning))
    second = repo.add_observation(_observation(205.0, evening))  # same day, same price

    assert first is True
    assert second is False

    observations = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(observations) == 1


def test_different_price_same_day_is_stored_separately(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")
    morning = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)

    repo.add_observation(_observation(205.0, morning))
    repo.add_observation(_observation(190.0, evening))  # price dropped same day

    observations = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(observations) == 2


def test_same_price_different_day_is_stored_separately(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")
    day1 = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)

    repo.add_observation(_observation(205.0, day1))
    repo.add_observation(_observation(205.0, day2))

    observations = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(observations) == 2


def test_different_destinations_are_kept_separate(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")
    observed_at = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)

    repo.add_observation(_observation(205.0, observed_at, destination="PMI"))
    repo.add_observation(_observation(380.0, observed_at, destination="AGP"))

    pmi_observations = repo.get_observations("PMI", _CHECK_IN, _CHECK_OUT, _CURRENCY)
    agp_observations = repo.get_observations("AGP", _CHECK_IN, _CHECK_OUT, _CURRENCY)

    assert [o.price for o in pmi_observations] == [205.0]
    assert [o.price for o in agp_observations] == [380.0]


def test_different_stay_dates_are_kept_separate(tmp_path):
    """Deliberately strict comparison group - same tradeoff already made
    for flights (see AccommodationComparisonGroup in models.py)."""
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")
    observed_at = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)

    repo.add_observation(_observation(205.0, observed_at, check_in=date(2026, 10, 2)))
    repo.add_observation(_observation(310.0, observed_at, check_in=date(2026, 12, 24)))

    october = repo.get_observations("PMI", date(2026, 10, 2), _CHECK_OUT, _CURRENCY)
    december = repo.get_observations("PMI", date(2026, 12, 24), _CHECK_OUT, _CURRENCY)

    assert [o.price for o in october] == [205.0]
    assert [o.price for o in december] == [310.0]


def test_get_route_statistics_none_when_no_observations(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")

    stats = repo.get_route_statistics(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)

    assert stats is None


def test_get_route_statistics_summarizes_stored_prices(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "history.db")
    for i, price in enumerate([205.0, 195.0, 220.0]):
        repo.add_observation(
            _observation(price, datetime(2026, 8, 1 + i, 8, 0, tzinfo=timezone.utc))
        )

    stats = repo.get_route_statistics(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)

    assert stats is not None
    assert stats.observation_count == 3
    assert stats.median == 205.0


def test_observation_from_accommodation_offer_hook():
    offer = AccommodationOffer(
        destination=_DESTINATION,
        check_in=_CHECK_IN,
        check_out=_CHECK_OUT,
        total_price=205.0,
        currency=_CURRENCY,
        name="Hotel Playa Sol",
        rating=4.2,
        provider="serpapi_google_hotels",
        booking_link="https://example.com/hotel",
    )
    observed_at = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)

    observation = observation_from_accommodation_offer(offer, observed_at=observed_at)

    assert observation.destination == _DESTINATION
    assert observation.price == 205.0
    assert observation.provider == "serpapi_google_hotels"
    assert observation.name == "Hotel Playa Sol"
    assert observation.observed_at == observed_at
    # Rating/booking_link are deliberately NOT carried into the observation
    # - see AccommodationObservation's docstring in models.py.
    assert not hasattr(observation, "rating")
    assert not hasattr(observation, "booking_link")


def test_default_db_path_is_gitignored():
    """The default runtime database must never be accidentally committed -
    see docs/ARCHITECTURE.md. Same physical file as the flight-side
    DEFAULT_DB_PATH (data/trip_hunter.db) - a different table, not a
    different file."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    gitignore_content = (repo_root / ".gitignore").read_text(encoding="utf-8")

    db_filename = DEFAULT_DB_PATH.name
    assert db_filename in gitignore_content or "data/*.db" in gitignore_content


def test_shares_the_same_default_db_path_as_flights():
    from trip_hunter.price_history_repository import DEFAULT_DB_PATH as FLIGHT_DB_PATH

    assert DEFAULT_DB_PATH == FLIGHT_DB_PATH
