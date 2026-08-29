"""Tests for "Real vs Fixture Data Hygiene" (MVP 0.4.1).

Audit finding: PriceHistoryRepository.get_observations(...) filters by
route/dates/trip_type/currency only, NOT by provider. So if demo/fixture
observations and real observations ever end up in the same database file
under the same comparison group, get_historical_baseline(...) would
average them together - a demo_fixture price could silently distort a
real OWN_HISTORICAL_BASELINE. The chosen fix is physical separation: the
demo must never write into the real runtime database. See "Real vs
Fixture Data Hygiene" in docs/PRODUCT_SPEC.md.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from vacation_hunter.engine.price_statistics import get_historical_baseline
from vacation_hunter.historical_price_demo import _DEMO_DB_PATH
from vacation_hunter.historical_price_demo import run as run_demo
from vacation_hunter.models import PriceObservation, TripType
from vacation_hunter.price_history_repository import DEFAULT_DB_PATH, PriceHistoryRepository

_ORIGIN = "HAM"
_DESTINATION = "PMI"
_DEPARTURE = date(2026, 10, 2)
_RETURN = date(2026, 10, 7)
_CURRENCY = "EUR"


def _observation(price: float, provider: str, observed_at: datetime) -> PriceObservation:
    return PriceObservation(
        origin=_ORIGIN,
        destination=_DESTINATION,
        departure_date=_DEPARTURE,
        return_date=_RETURN,
        trip_type=TripType.ROUND_TRIP,
        price=price,
        currency=_CURRENCY,
        provider=provider,
        stops=0,
        airline="Testair",
        observed_at=observed_at,
    )


# A) Demo nutzt nicht DEFAULT_DB_PATH der echten Runtime-History.
def test_demo_db_path_differs_from_real_runtime_db_path():
    assert _DEMO_DB_PATH != DEFAULT_DB_PATH
    assert _DEMO_DB_PATH.name != DEFAULT_DB_PATH.name


# B) Demo-Ausfuehrung erzeugt keine demo_fixture-Zeilen in data/vacation_hunter.db.
def test_running_the_demo_writes_only_to_the_isolated_demo_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_demo()

    real_db_path = tmp_path / DEFAULT_DB_PATH
    demo_db_path = tmp_path / _DEMO_DB_PATH

    assert demo_db_path.exists()

    if real_db_path.exists():
        # Even if something else in this isolated cwd created the file,
        # the demo's own comparison group must have zero rows in it.
        real_repo = PriceHistoryRepository(db_path=real_db_path)
        leaked = real_repo.get_observations(
            _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY
        )
        assert leaked == []
    # If the real DB path was never even created, that's the strongest
    # possible proof nothing leaked into it.


# C) + D) + E) Contaminated-then-cleaned scenario, exactly as encountered
# in the real data/vacation_hunter.db: 6 demo_fixture + 1 real observation
# for the same comparison group.
def test_mixed_fixture_and_real_rows_would_corrupt_the_baseline_before_cleanup(tmp_path):
    """Regression/documentation test: proves the underlying repository
    query does NOT filter by provider - this is exactly why physical
    database separation (not a query-level filter) was chosen as the fix.
    See "Real vs Fixture Data Hygiene" in docs/PRODUCT_SPEC.md."""
    repo = PriceHistoryRepository(db_path=tmp_path / "mixed.db")
    base_time = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)

    for i, price in enumerate([180.0, 175.0, 190.0, 185.0, 178.0, 182.0]):
        repo.add_observation(_observation(price, "demo_fixture", base_time.replace(day=23 + i)))
    repo.add_observation(_observation(184.0, "serpapi_google_flights", datetime(2026, 8, 29, 21, 51, tzinfo=timezone.utc)))

    all_rows = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(all_rows) == 7  # NOT filtered by provider - the actual risk

    contaminated_baseline = get_historical_baseline(
        repo, _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY, current_price=119.0
    )
    assert contaminated_baseline is not None  # wrongly "available" - the bug
    assert contaminated_baseline.statistics.observation_count == 7


def test_after_removing_fixture_rows_only_real_observation_count_remains(tmp_path):
    """D) + E): after the kind of cleanup performed on data/vacation_hunter.db
    (removing only provider='demo_fixture' rows), exactly the real
    observation remains and OWN_HISTORICAL_BASELINE correctly becomes
    unavailable again (1 < MIN_HISTORY_OBSERVATIONS)."""
    import sqlite3

    db_path = tmp_path / "mixed.db"
    repo = PriceHistoryRepository(db_path=db_path)
    base_time = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)

    for i, price in enumerate([180.0, 175.0, 190.0, 185.0, 178.0, 182.0]):
        repo.add_observation(_observation(price, "demo_fixture", base_time.replace(day=23 + i)))
    repo.add_observation(_observation(184.0, "serpapi_google_flights", datetime(2026, 8, 29, 21, 51, tzinfo=timezone.utc)))

    # The same targeted cleanup performed on the real database: remove only
    # provider='demo_fixture' rows, never a full-database wipe.
    connection = sqlite3.connect(db_path)
    connection.execute("DELETE FROM price_observations WHERE provider = 'demo_fixture'")
    connection.commit()
    connection.close()

    remaining = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(remaining) == 1
    assert remaining[0].provider == "serpapi_google_flights"
    assert remaining[0].price == 184.0

    baseline = get_historical_baseline(
        repo, _ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY, current_price=119.0
    )
    assert baseline is None  # E) correctly unavailable with only 1 real observation
