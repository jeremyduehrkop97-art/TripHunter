"""Tests for the manual accommodation "Controlled Historical Sampling" CLI
(record_hotel_price_snapshot.py). No real HTTP calls anywhere in this file -
`_record_snapshot` is exercised directly with a fake AccommodationProvider,
and the one test that goes through the CLI's `run()` entry point
monkeypatches SerpApiHotelsClient.search_hotels so no network request is
ever made. Mirrors test_record_price_snapshot.py (flights) test-for-test
where the domain allows it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trip_hunter.accommodation_price_history_repository import (
    DEFAULT_DB_PATH,
    AccommodationPriceHistoryRepository,
)
from trip_hunter.engine.price_statistics import MIN_HISTORY_OBSERVATIONS
from trip_hunter.historical_price_demo import _DEMO_DB_PATH
from trip_hunter.models import AccommodationComparisonGroup, AccommodationOffer
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.record_hotel_price_snapshot import _build_parser, _parse_args, _record_snapshot, run

_DESTINATION = "PMI"
_CHECK_IN = date(2026, 10, 2)
_CHECK_OUT = date(2026, 10, 7)
_CURRENCY = "EUR"

_GROUP = AccommodationComparisonGroup(
    destination=_DESTINATION,
    check_in=_CHECK_IN,
    check_out=_CHECK_OUT,
    currency=_CURRENCY,
)


def _offer(
    price: float,
    *,
    destination: str = _DESTINATION,
    check_in: date = _CHECK_IN,
    check_out: date = _CHECK_OUT,
    currency: str = _CURRENCY,
    name: str = "Hotel Playa Sol",
) -> AccommodationOffer:
    return AccommodationOffer(
        destination=destination,
        check_in=check_in,
        check_out=check_out,
        total_price=price,
        currency=currency,
        name=name,
        rating=4.2,
        provider="serpapi_google_hotels",
    )


class _FakeProvider(AccommodationProvider):
    def __init__(self, offers: list[AccommodationOffer]):
        self._offers = offers

    def search_accommodations(self, destination, check_in, check_out):
        return self._offers

    def get_typical_total_price(self, destination, nights, month):
        return None


def _run_snapshot(tmp_path, offers, *, day: int = 1):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")
    observed_at = datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc)
    _record_snapshot(_FakeProvider(offers), repo, _GROUP, "LIVE RESPONSE", observed_at=observed_at)
    return repo


# A) CLI-Argumente korrekt
def test_cli_arguments_are_parsed_and_normalized():
    args = _parse_args(
        ["--destination", "pmi", "--check-in", "2026-10-02", "--check-out", "2026-10-07"]
    )
    assert args.destination == "pmi"  # normalization happens in run(), not parsing
    assert args.check_in == "2026-10-02"
    assert args.check_out == "2026-10-07"
    assert args.currency == "EUR"  # default


def test_cli_requires_all_mandatory_arguments():
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--destination", "PMI"])


# B) Comparison Group korrekt erzeugt - exercised through run() end to end,
# with SerpApiHotelsClient.search_hotels monkeypatched so no real HTTP happens.
def test_run_builds_comparison_group_from_cli_args_and_stores_matching_observation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "fake-test-key-not-real")

    raw_response = {
        "properties": [
            {
                "name": "Hotel Playa Sol",
                "total_rate": {"extracted_lowest": 205},
                "rate_per_night": {"extracted_lowest": 41},
                "overall_rating": 4.2,
            }
        ]
    }

    import trip_hunter.providers.serpapi_hotels_client as serpapi_hotels_client_module

    def fake_search_hotels(self, **kwargs):
        return raw_response

    monkeypatch.setattr(
        serpapi_hotels_client_module.SerpApiHotelsClient, "search_hotels", fake_search_hotels
    )

    run(["--destination", "pmi", "--check-in", "2026-10-02", "--check-out", "2026-10-07"])

    repo = AccommodationPriceHistoryRepository(db_path=DEFAULT_DB_PATH)
    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(stored) == 1
    assert stored[0].price == 205.0
    assert stored[0].destination == "PMI"


def test_run_prints_friendly_message_when_serpapi_key_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRIP_HUNTER_SERPAPI_KEY", raising=False)
    monkeypatch.delenv("VACATION_HUNTER_SERPAPI_KEY", raising=False)  # legacy fallback

    run(["--destination", "PMI", "--check-in", "2026-10-02", "--check-out", "2026-10-07"])

    captured = capsys.readouterr()
    assert "Configuration missing" in captured.out


# C) + D) mehrere AccommodationOffers -> genau eine Observation, cheapest gewinnt
def test_multiple_offers_produce_exactly_one_observation_the_cheapest(tmp_path):
    offers = [_offer(310.0), _offer(205.0), _offer(250.0)]
    repo = _run_snapshot(tmp_path, offers)

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(stored) == 1
    assert stored[0].price == 205.0


# F) falsche Gruppe wird ignoriert
def test_offers_outside_the_comparison_group_are_ignored(tmp_path):
    offers = [
        _offer(50.0, destination="AGP"),
        _offer(60.0, currency="USD"),
        _offer(70.0, check_in=date(2026, 11, 1), check_out=date(2026, 11, 8)),
        _offer(205.0),
    ]
    repo = _run_snapshot(tmp_path, offers)

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(stored) == 1
    assert stored[0].price == 205.0


# G) keine passenden Offers -> nichts speichern
def test_no_matching_offers_stores_nothing(tmp_path, capsys):
    offers = [_offer(50.0, destination="AGP")]
    repo = _run_snapshot(tmp_path, offers)

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert stored == []

    captured = capsys.readouterr()
    assert "Stored: NO" in captured.out


def test_empty_offer_list_stores_nothing(tmp_path):
    repo = _run_snapshot(tmp_path, [])
    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert stored == []


# H) + I) Dedup funktioniert, Observation count korrekt
def test_duplicate_same_day_same_price_is_not_stored_again(tmp_path, capsys):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")

    observed_at = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    _record_snapshot(_FakeProvider([_offer(205.0)]), repo, _GROUP, "LIVE RESPONSE", observed_at=observed_at)
    first_count = len(repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY))

    observed_at_later_same_day = datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc)
    _record_snapshot(
        _FakeProvider([_offer(205.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        observed_at=observed_at_later_same_day,
    )
    second_count = len(repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY))

    assert first_count == 1
    assert second_count == 1  # count did not increase

    captured = capsys.readouterr()
    assert "Stored: NO" in captured.out
    assert "duplicate observation" in captured.out


# J) 1-4 Beobachtungen -> keine Baseline
def test_below_minimum_observations_baseline_not_available(tmp_path, capsys):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")

    for day in range(1, MIN_HISTORY_OBSERVATIONS):  # 1..4 -> 4 total, one below minimum
        _record_snapshot(
            _FakeProvider([_offer(200.0 + day)]),
            repo,
            _GROUP,
            "LIVE RESPONSE",
            observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
        )

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(stored) == MIN_HISTORY_OBSERVATIONS - 1

    captured = capsys.readouterr()
    assert "NOT AVAILABLE YET" in captured.out


# K) ab 5 -> Baseline verfuegbar
def test_at_minimum_observations_baseline_becomes_available(tmp_path, capsys):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")

    for day in range(1, MIN_HISTORY_OBSERVATIONS + 1):
        _record_snapshot(
            _FakeProvider([_offer(200.0 + day)]),
            repo,
            _GROUP,
            "LIVE RESPONSE",
            observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
        )

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(stored) == MIN_HISTORY_OBSERVATIONS

    captured = capsys.readouterr()
    assert "Historical baseline:\nAVAILABLE" in captured.out
    assert "Median:" in captured.out


# L) Demo-DB wird niemals benutzt
def test_real_db_path_differs_from_demo_db_path():
    assert DEFAULT_DB_PATH != _DEMO_DB_PATH


def test_module_has_no_import_dependency_on_the_demo_module():
    """record_hotel_price_snapshot.py must not even import
    historical_price_demo - the module that owns _DEMO_DB_PATH."""
    import trip_hunter.record_hotel_price_snapshot as module

    source = open(module.__file__, encoding="utf-8").read()
    assert "import trip_hunter.historical_price_demo" not in source
    assert "from trip_hunter.historical_price_demo" not in source
    assert not hasattr(module, "_DEMO_DB_PATH")


def test_module_has_no_price_insight_section():
    """AccommodationProvider has no get_price_insight()-equivalent method -
    unlike record_price_snapshot.py, there must be no such section here.
    Checks for an actual print() of the section / a call to
    get_price_insight(, not just the phrase - the module docstring is
    still allowed to mention "PROVIDER PRICE INSIGHT" by name to explain
    why it's absent (same convention as
    test_module_has_no_import_dependency_on_the_demo_module for flights)."""
    import trip_hunter.record_hotel_price_snapshot as module

    source = open(module.__file__, encoding="utf-8").read()
    assert 'print("PROVIDER PRICE INSIGHT' not in source
    assert "get_price_insight(" not in source


# --- Cache Hit Rule: a cache hit is NOT a new market observation ----------


# A) LIVE RESPONSE -> Observation wird gespeichert
def test_live_response_stores_an_observation(tmp_path):
    repo = _run_snapshot(tmp_path, [_offer(205.0)])
    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(stored) == 1
    assert stored[0].price == 205.0


# B) CACHE HIT -> Observation wird NICHT gespeichert
def test_cache_hit_does_not_store_an_observation(tmp_path, capsys):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(310.0)]),
        repo,
        _GROUP,
        "CACHE HIT",
        observed_at=datetime(2026, 9, 10, 9, 20, tzinfo=timezone.utc),
    )

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert stored == []

    captured = capsys.readouterr()
    assert "Stored: NO" in captured.out
    assert "cache hit" in captured.out.lower()


# C) CACHE HIT -> Observation Count bleibt unverändert
def test_cache_hit_does_not_change_existing_observation_count(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(205.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        observed_at=datetime(2026, 8, 29, 21, 51, tzinfo=timezone.utc),
    )
    count_before = len(repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY))

    _record_snapshot(
        _FakeProvider([_offer(310.0)]),
        repo,
        _GROUP,
        "CACHE HIT",
        observed_at=datetime(2026, 9, 10, 9, 20, tzinfo=timezone.utc),
    )
    count_after = len(repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY))

    assert count_before == 1
    assert count_after == 1


# D) CACHE HIT mit anderem heutigen Datum -> trotzdem keine neue Observation
def test_cache_hit_on_a_different_day_still_stores_nothing(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(310.0)]),
        repo,
        _GROUP,
        "CACHE HIT",
        observed_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
    )
    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert stored == []


# E) CACHE HIT mit verändertem observed_at des Commands -> trotzdem keine Speicherung
@pytest.mark.parametrize(
    "observed_at",
    [
        datetime(2026, 9, 10, 0, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 10, 23, 59, 59, tzinfo=timezone.utc),
        datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc),
    ],
)
def test_cache_hit_with_varying_observed_at_never_stores(tmp_path, observed_at):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(_FakeProvider([_offer(310.0)]), repo, _GROUP, "CACHE HIT", observed_at=observed_at)
    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert stored == []


# F) LIVE RESPONSE mit gültigem neuen Preis -> neue Observation
def test_live_response_with_a_new_price_stores_a_new_observation(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(205.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        observed_at=datetime(2026, 8, 29, 21, 51, tzinfo=timezone.utc),
    )
    _record_snapshot(
        _FakeProvider([_offer(190.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        observed_at=datetime(2026, 9, 1, 8, 50, tzinfo=timezone.utc),
    )
    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(stored) == 2
    assert {o.price for o in stored} == {205.0, 190.0}


# G) 4 echte Observations + Cache Hit -> weiterhin 4, Baseline NICHT verfügbar
def test_four_real_observations_plus_cache_hit_stays_at_four_no_baseline(tmp_path, capsys):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")
    prices = [205.0, 195.0, 195.0, 230.0]
    for day, price in zip(range(1, 5), prices):
        _record_snapshot(
            _FakeProvider([_offer(price)]),
            repo,
            _GROUP,
            "LIVE RESPONSE",
            observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
        )

    _record_snapshot(
        _FakeProvider([_offer(230.0)]),
        repo,
        _GROUP,
        "CACHE HIT",
        observed_at=datetime(2026, 9, 10, 9, 20, tzinfo=timezone.utc),
    )

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(stored) == 4

    captured = capsys.readouterr()
    assert "4 / 5 required" in captured.out
    assert "NOT AVAILABLE YET" in captured.out


# H) 4 echte Observations + echter LIVE RESPONSE -> 5, Baseline verfügbar
def test_four_real_observations_plus_live_response_reaches_five_baseline_available(tmp_path, capsys):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "real.db")
    prices = [205.0, 195.0, 195.0, 230.0]
    for day, price in zip(range(1, 5), prices):
        _record_snapshot(
            _FakeProvider([_offer(price)]),
            repo,
            _GROUP,
            "LIVE RESPONSE",
            observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
        )

    _record_snapshot(
        _FakeProvider([_offer(230.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        observed_at=datetime(2026, 9, 10, 9, 20, tzinfo=timezone.utc),
    )

    stored = repo.get_observations(_DESTINATION, _CHECK_IN, _CHECK_OUT, _CURRENCY)
    assert len(stored) == 5

    captured = capsys.readouterr()
    assert "5 / 5 required" in captured.out
    assert "Historical baseline:\nAVAILABLE" in captured.out
