"""Tests for the manual "Controlled Historical Sampling" CLI
(record_price_snapshot.py). No real HTTP calls anywhere in this file -
`_record_snapshot` is exercised directly with a fake FlightProvider, and
the one test that goes through the CLI's `run()` entry point monkeypatches
SerpApiClient.search_flights so no network request is ever made.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trip_hunter.engine.price_statistics import MIN_HISTORY_OBSERVATIONS
from trip_hunter.historical_price_demo import _DEMO_DB_PATH
from trip_hunter.models import FlightComparisonGroup, FlightOffer, PriceInsight, TripType
from trip_hunter.price_history_repository import DEFAULT_DB_PATH, PriceHistoryRepository
from trip_hunter.providers.flight_provider import FlightProvider
from trip_hunter.record_price_snapshot import _build_parser, _parse_args, _record_snapshot, run

_ORIGIN = "HAM"
_DESTINATION = "PMI"
_DEPARTURE = date(2026, 10, 2)
_RETURN = date(2026, 10, 7)
_CURRENCY = "EUR"

_GROUP = FlightComparisonGroup(
    origin=_ORIGIN,
    destination=_DESTINATION,
    departure_date=_DEPARTURE,
    return_date=_RETURN,
    trip_type=TripType.ROUND_TRIP,
    currency=_CURRENCY,
)


def _offer(
    price: float,
    *,
    origin: str = _ORIGIN,
    destination: str = _DESTINATION,
    departure_date: date = _DEPARTURE,
    return_date: date = _RETURN,
    currency: str = _CURRENCY,
    stops: int = 1,
    airline: str = "Vueling",
    price_confirmed_complete: bool = True,
) -> FlightOffer:
    return FlightOffer(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        price=price,
        currency=currency,
        airline=airline,
        stops=stops,
        provider="serpapi_google_flights",
        price_confirmed_complete=price_confirmed_complete,
    )


class _FakeProvider(FlightProvider):
    def __init__(self, offers: list[FlightOffer], insight: PriceInsight | None = None):
        self._offers = offers
        self._insight = insight

    def search_flights(self, origin, destination, earliest_departure, latest_departure, return_date=None):
        return self._offers

    def get_typical_price(self, origin, destination, month):
        return None

    def get_price_insight(self, origin, destination, departure_date, return_date):
        return self._insight


def _run_snapshot(tmp_path, offers, *, day: int = 1, insight=None):
    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    observed_at = datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc)
    _record_snapshot(
        _FakeProvider(offers, insight=insight),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        _CURRENCY,
        observed_at=observed_at,
    )
    return repo


# A) CLI-Argumente korrekt
def test_cli_arguments_are_parsed_and_normalized():
    args = _parse_args(
        ["--origin", "ham", "--destination", "pmi", "--departure", "2026-10-02", "--return", "2026-10-07"]
    )
    assert args.origin == "ham"  # normalization happens in run(), not parsing
    assert args.destination == "pmi"
    assert args.departure == "2026-10-02"
    assert args.return_date == "2026-10-07"
    assert args.currency == "EUR"  # default


def test_cli_requires_all_mandatory_arguments():
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--origin", "HAM"])


# B) Comparison Group korrekt erzeugt - exercised through run() end to end,
# with SerpApiClient.search_flights monkeypatched so no real HTTP happens.
def test_run_builds_comparison_group_from_cli_args_and_stores_matching_observation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "fake-test-key-not-real")

    raw_response = {
        "best_flights": [
            {
                "price": 184,
                "flights": [
                    {
                        "departure_airport": {"id": "HAM", "time": "2026-10-02 21:50"},
                        "arrival_airport": {"id": "PMI", "time": "2026-10-02 23:20"},
                        "airline": "Vueling",
                    }
                ],
            }
        ],
        "other_flights": [],
    }

    import trip_hunter.providers.serpapi_client as serpapi_client_module

    def fake_search_flights(self, **kwargs):
        return raw_response

    monkeypatch.setattr(serpapi_client_module.SerpApiClient, "search_flights", fake_search_flights)

    run(["--origin", "ham", "--destination", "pmi", "--departure", "2026-10-02", "--return", "2026-10-07"])

    repo = PriceHistoryRepository(db_path=DEFAULT_DB_PATH)
    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == 1
    assert stored[0].price == 184.0
    assert stored[0].origin == "HAM"
    assert stored[0].destination == "PMI"


def test_run_prints_friendly_message_when_serpapi_key_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRIP_HUNTER_SERPAPI_KEY", raising=False)
    monkeypatch.delenv("VACATION_HUNTER_SERPAPI_KEY", raising=False)  # legacy fallback

    run(["--origin", "HAM", "--destination", "PMI", "--departure", "2026-10-02", "--return", "2026-10-07"])

    captured = capsys.readouterr()
    assert "Configuration missing" in captured.out


# C) + D) mehrere FlightOffers -> genau eine Observation, cheapest valid complete gewinnt
def test_multiple_offers_produce_exactly_one_observation_the_cheapest(tmp_path):
    offers = [_offer(227.0), _offer(184.0), _offer(205.0)]
    repo = _run_snapshot(tmp_path, offers)

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == 1
    assert stored[0].price == 184.0


# E) incomplete wird ignoriert
def test_incomplete_cheapest_offer_is_ignored(tmp_path):
    offers = [
        _offer(99.0, price_confirmed_complete=False),
        _offer(184.0),
        _offer(205.0),
    ]
    repo = _run_snapshot(tmp_path, offers)

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == 1
    assert stored[0].price == 184.0


# F) falsche Gruppe wird ignoriert
def test_offers_outside_the_comparison_group_are_ignored(tmp_path):
    offers = [
        _offer(50.0, destination="AGP"),
        _offer(60.0, currency="USD"),
        _offer(70.0, departure_date=date(2026, 11, 1), return_date=date(2026, 11, 8)),
        _offer(184.0),
    ]
    repo = _run_snapshot(tmp_path, offers)

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == 1
    assert stored[0].price == 184.0


# G) keine passenden Offers -> nichts speichern
def test_no_matching_offers_stores_nothing(tmp_path, capsys):
    offers = [_offer(50.0, destination="AGP")]
    repo = _run_snapshot(tmp_path, offers)

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert stored == []

    captured = capsys.readouterr()
    assert "Stored: NO" in captured.out


def test_empty_offer_list_stores_nothing(tmp_path):
    repo = _run_snapshot(tmp_path, [])
    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert stored == []


# H) + I) Dedup funktioniert, Observation count korrekt
def test_duplicate_same_day_same_price_is_not_stored_again(tmp_path, capsys):
    repo_path = tmp_path / "real.db"
    repo = PriceHistoryRepository(db_path=repo_path)

    observed_at = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    _record_snapshot(
        _FakeProvider([_offer(184.0)]), repo, _GROUP, "LIVE RESPONSE", _CURRENCY, observed_at=observed_at
    )
    first_count = len(
        repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    )

    observed_at_later_same_day = datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc)
    _record_snapshot(
        _FakeProvider([_offer(184.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        _CURRENCY,
        observed_at=observed_at_later_same_day,
    )
    second_count = len(
        repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    )

    assert first_count == 1
    assert second_count == 1  # I) count did not increase

    captured = capsys.readouterr()
    assert "Stored: NO" in captured.out
    assert "duplicate observation" in captured.out


# J) 1-4 Beobachtungen -> keine OWN_HISTORICAL_BASELINE
def test_below_minimum_observations_baseline_not_available(tmp_path, capsys):
    repo_path = tmp_path / "real.db"
    repo = PriceHistoryRepository(db_path=repo_path)

    for day in range(1, MIN_HISTORY_OBSERVATIONS):  # 1..4 -> 4 total, one below minimum
        _record_snapshot(
            _FakeProvider([_offer(180.0 + day)]),
            repo,
            _GROUP,
            "LIVE RESPONSE",
            _CURRENCY,
            observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
        )

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == MIN_HISTORY_OBSERVATIONS - 1

    captured = capsys.readouterr()
    assert "NOT AVAILABLE YET" in captured.out


# K) ab 5 -> OWN_HISTORICAL_BASELINE verfuegbar
def test_at_minimum_observations_baseline_becomes_available(tmp_path, capsys):
    repo_path = tmp_path / "real.db"
    repo = PriceHistoryRepository(db_path=repo_path)

    for day in range(1, MIN_HISTORY_OBSERVATIONS + 1):  # exactly MIN_HISTORY_OBSERVATIONS
        _record_snapshot(
            _FakeProvider([_offer(180.0 + day)]),
            repo,
            _GROUP,
            "LIVE RESPONSE",
            _CURRENCY,
            observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
        )

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == MIN_HISTORY_OBSERVATIONS

    captured = capsys.readouterr()
    assert "Historical baseline:\nAVAILABLE" in captured.out
    assert "Median:" in captured.out


# L) Demo-DB wird niemals benutzt
def test_real_db_path_differs_from_demo_db_path():
    assert DEFAULT_DB_PATH != _DEMO_DB_PATH


def test_module_has_no_import_dependency_on_the_demo_module():
    """record_price_snapshot.py must not even import historical_price_demo -
    the module that owns _DEMO_DB_PATH. This is a stronger guarantee than
    checking DEFAULT_DB_PATH is used at the call site: it proves the demo
    database path isn't reachable from this module's code at all (the
    module docstring is still allowed - and expected - to mention
    data/demo_trip_hunter.db by name as a warning)."""
    import trip_hunter.record_price_snapshot as module

    source = open(module.__file__, encoding="utf-8").read()
    assert "import trip_hunter.historical_price_demo" not in source
    assert "from trip_hunter.historical_price_demo" not in source
    assert not hasattr(module, "_DEMO_DB_PATH")


# M) Provider Price Insight wird nicht als Observation gespeichert
def test_price_insight_is_shown_but_never_stored_as_an_observation(tmp_path, capsys):
    insight = PriceInsight(
        provider_lowest_price=180.0,
        typical_price_low=160.0,
        typical_price_high=220.0,
        price_level="typical",
        source="google_flights",
    )
    repo = _run_snapshot(tmp_path, [_offer(184.0)], insight=insight)

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == 1  # only the real offer-derived observation
    assert stored[0].price == 184.0  # not the insight's provider_lowest_price

    captured = capsys.readouterr()
    assert "PROVIDER PRICE INSIGHT" in captured.out
    assert "not stored as our data" in captured.out


# --- Cache Hit Rule: a cache hit is NOT a new market observation ----------
# A cache hit re-reads a response fetched at an earlier point in time, so it
# must never call repository.add_observation(...) and must never increase
# the historical observation count, regardless of what observed_at the
# command would otherwise compute. Only "LIVE RESPONSE" may create a new
# Historical Measurement Snapshot.


# A) LIVE RESPONSE -> Observation wird gespeichert
def test_live_response_stores_an_observation(tmp_path):
    repo = _run_snapshot(tmp_path, [_offer(184.0)])
    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == 1
    assert stored[0].price == 184.0


# B) CACHE HIT -> Observation wird NICHT gespeichert
def test_cache_hit_does_not_store_an_observation(tmp_path, capsys):
    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(213.0)]),
        repo,
        _GROUP,
        "CACHE HIT",
        _CURRENCY,
        observed_at=datetime(2026, 9, 10, 9, 20, tzinfo=timezone.utc),
    )

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert stored == []

    captured = capsys.readouterr()
    assert "Stored: NO" in captured.out
    assert "cache hit" in captured.out.lower()


# C) CACHE HIT -> Observation Count bleibt unverändert
def test_cache_hit_does_not_change_existing_observation_count(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(184.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        _CURRENCY,
        observed_at=datetime(2026, 8, 29, 21, 51, tzinfo=timezone.utc),
    )
    count_before = len(
        repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    )

    _record_snapshot(
        _FakeProvider([_offer(213.0)]),
        repo,
        _GROUP,
        "CACHE HIT",
        _CURRENCY,
        observed_at=datetime(2026, 9, 10, 9, 20, tzinfo=timezone.utc),
    )
    count_after = len(
        repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    )

    assert count_before == 1
    assert count_after == 1


# D) CACHE HIT mit anderem heutigen Datum -> trotzdem keine neue Observation
def test_cache_hit_on_a_different_day_still_stores_nothing(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(213.0)]),
        repo,
        _GROUP,
        "CACHE HIT",
        _CURRENCY,
        observed_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
    )
    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
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
    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(213.0)]), repo, _GROUP, "CACHE HIT", _CURRENCY, observed_at=observed_at
    )
    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert stored == []


# F) LIVE RESPONSE mit gültigem neuen Preis -> neue Observation
def test_live_response_with_a_new_price_stores_a_new_observation(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(184.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        _CURRENCY,
        observed_at=datetime(2026, 8, 29, 21, 51, tzinfo=timezone.utc),
    )
    _record_snapshot(
        _FakeProvider([_offer(176.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        _CURRENCY,
        observed_at=datetime(2026, 9, 1, 8, 50, tzinfo=timezone.utc),
    )
    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == 2
    assert {o.price for o in stored} == {184.0, 176.0}


# G) 4 echte Observations + Cache Hit -> weiterhin 4, Baseline NICHT verfügbar
def test_four_real_observations_plus_cache_hit_stays_at_four_no_baseline(tmp_path, capsys):
    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    prices = [184.0, 176.0, 176.0, 213.0]
    for day, price in zip(range(1, 5), prices):
        _record_snapshot(
            _FakeProvider([_offer(price)]),
            repo,
            _GROUP,
            "LIVE RESPONSE",
            _CURRENCY,
            observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
        )

    _record_snapshot(
        _FakeProvider([_offer(213.0)]),
        repo,
        _GROUP,
        "CACHE HIT",
        _CURRENCY,
        observed_at=datetime(2026, 9, 10, 9, 20, tzinfo=timezone.utc),
    )

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == 4

    captured = capsys.readouterr()
    assert "4 / 5 required" in captured.out
    assert "NOT AVAILABLE YET" in captured.out


# H) 4 echte Observations + echter LIVE RESPONSE -> 5, Baseline verfügbar
def test_four_real_observations_plus_live_response_reaches_five_baseline_available(tmp_path, capsys):
    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    prices = [184.0, 176.0, 176.0, 213.0]
    for day, price in zip(range(1, 5), prices):
        _record_snapshot(
            _FakeProvider([_offer(price)]),
            repo,
            _GROUP,
            "LIVE RESPONSE",
            _CURRENCY,
            observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
        )

    _record_snapshot(
        _FakeProvider([_offer(213.0)]),
        repo,
        _GROUP,
        "LIVE RESPONSE",
        _CURRENCY,
        observed_at=datetime(2026, 9, 10, 9, 20, tzinfo=timezone.utc),
    )

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert len(stored) == 5

    captured = capsys.readouterr()
    assert "5 / 5 required" in captured.out
    assert "Historical baseline:\nAVAILABLE" in captured.out


# I) Provider Price Insight bei CACHE HIT weiterhin angezeigt, aber nicht gespeichert
def test_cache_hit_still_shows_price_insight_but_never_stores_it(tmp_path, capsys):
    insight = PriceInsight(
        provider_lowest_price=213.0,
        typical_price_low=130.0,
        typical_price_high=315.0,
        price_level="typical",
        source="google_flights",
    )
    repo = PriceHistoryRepository(db_path=tmp_path / "real.db")
    _record_snapshot(
        _FakeProvider([_offer(213.0)], insight=insight),
        repo,
        _GROUP,
        "CACHE HIT",
        _CURRENCY,
        observed_at=datetime(2026, 9, 10, 9, 20, tzinfo=timezone.utc),
    )

    stored = repo.get_observations(_ORIGIN, _DESTINATION, _DEPARTURE, _RETURN, TripType.ROUND_TRIP, _CURRENCY)
    assert stored == []

    captured = capsys.readouterr()
    assert "PROVIDER PRICE INSIGHT" in captured.out
    assert "not stored as our data" in captured.out
