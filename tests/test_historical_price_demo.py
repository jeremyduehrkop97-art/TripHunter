from datetime import date

from trip_hunter.historical_price_demo import _CURRENT_PRICE, run
from trip_hunter.models import BaselineSource, DealType


def test_run_produces_flight_drop_from_seeded_history(tmp_path, monkeypatch):
    # Run against an isolated DB so this test never touches the real
    # data/trip_hunter.db used by manual demo runs.
    monkeypatch.chdir(tmp_path)

    deal = run()

    assert deal.baseline_source == BaselineSource.OWN_HISTORICAL_BASELINE
    assert deal.deal_type == DealType.FLIGHT_DROP
    assert deal.flight.price == _CURRENT_PRICE
    assert deal.historical_baseline is not None
    assert deal.historical_baseline.statistics.observation_count == 6
    assert deal.flight.departure_date == date(2026, 10, 2)


def test_run_is_idempotent_and_does_not_duplicate_observations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    run()
    deal = run()

    assert deal.historical_baseline is not None
    assert deal.historical_baseline.statistics.observation_count == 6
