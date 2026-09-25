"""Tests for daily_sampler.py. No real HTTP calls anywhere in this file -
`run_sampler` and the `_decide_*`/`_process_*` helpers are exercised
directly with fake, call-counting providers and tmp_path-based real
repositories/cache. The one test that goes through `run()` monkeypatches
SerpApiClient.search_flights / SerpApiHotelsClient.search_hotels so no
network request is ever made.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from trip_hunter.accommodation_price_history_repository import AccommodationPriceHistoryRepository
from trip_hunter.caching import FileCache
from trip_hunter.daily_sampler import (
    SamplingStatus,
    _build_parser,
    _check_and_dispatch_alert,
    _decide_flight,
    _decide_hotel,
    _hotel_target_for_flight_template,
    _matching_flight_targets,
    _parse_args,
    _process_flight_target,
    _process_hotel_target,
    run,
    run_sampler,
)
from trip_hunter.engine.deal_filters import DealFilterCriteria
from trip_hunter.models import (
    AccommodationComparisonGroup,
    AccommodationOffer,
    DealType,
    FlightComparisonGroup,
    FlightOffer,
    TripType,
)
from trip_hunter.price_history_repository import PriceHistoryRepository, observation_from_flight_offer
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.flight_provider import FlightProvider

_FLIGHT_GROUP = FlightComparisonGroup(
    origin="HAM", destination="PMI",
    departure_date=date(2026, 10, 2), return_date=date(2026, 10, 7),
    trip_type=TripType.ROUND_TRIP, currency="EUR",
)
_HOTEL_GROUP = AccommodationComparisonGroup(
    destination="PMI", check_in=date(2026, 10, 2), check_out=date(2026, 10, 7), currency="EUR",
)
_TODAY = date(2026, 9, 14)
_OBSERVED_AT = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)


def _flight_offer(price: float = 184.0) -> FlightOffer:
    return FlightOffer(
        origin="HAM", destination="PMI",
        departure_date=date(2026, 10, 2), return_date=date(2026, 10, 7),
        price=price, currency="EUR", airline="Vueling", stops=1, provider="test",
    )


def _accommodation_offer(price: float = 205.0) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI", check_in=date(2026, 10, 2), check_out=date(2026, 10, 7),
        total_price=price, currency="EUR", name="Test Hotel", rating=4.0, provider="test",
    )


class _CountingFlightProvider(FlightProvider):
    def __init__(self, offers: list[FlightOffer]):
        self._offers = offers
        self.search_calls = 0

    def search_flights(self, origin, destination, earliest_departure, latest_departure, return_date=None):
        self.search_calls += 1
        return self._offers

    def get_typical_price(self, origin, destination, month):
        return None

    def get_price_insight(self, origin, destination, departure_date, return_date):
        return None


class _CountingAccommodationProvider(AccommodationProvider):
    def __init__(self, offers: list[AccommodationOffer]):
        self._offers = offers
        self.search_calls = 0

    def search_accommodations(self, destination, check_in, check_out):
        self.search_calls += 1
        return self._offers

    def get_typical_total_price(self, destination, nights, month):
        return None


# --- CLI parsing --------------------------------------------------------------


def test_dry_run_flag_defaults_to_false():
    args = _parse_args([])
    assert args.dry_run is False


def test_dry_run_flag_can_be_set():
    args = _parse_args(["--dry-run"])
    assert args.dry_run is True


def test_parser_has_a_prog_name():
    parser = _build_parser()
    assert "daily_sampler" in parser.prog


# --- _decide_flight / _decide_hotel -------------------------------------------


def test_decide_flight_is_due_with_no_history_and_no_cache(tmp_path):
    repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    cache = FileCache(cache_dir=tmp_path / "cache")

    assert _decide_flight(repo, cache, _FLIGHT_GROUP, _TODAY) == SamplingStatus.DUE


def test_decide_flight_is_already_observed_today(tmp_path):
    from trip_hunter.price_history_repository import observation_from_flight_offer

    repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    observation = observation_from_flight_offer(
        _flight_offer(), TripType.ROUND_TRIP, observed_at=_OBSERVED_AT
    )
    repo.add_observation(observation)

    assert _decide_flight(repo, cache, _FLIGHT_GROUP, _TODAY) == SamplingStatus.ALREADY_OBSERVED_TODAY


def test_decide_flight_ignores_observations_from_a_different_day(tmp_path):
    from trip_hunter.price_history_repository import observation_from_flight_offer

    repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    yesterday = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
    repo.add_observation(
        observation_from_flight_offer(_flight_offer(), TripType.ROUND_TRIP, observed_at=yesterday)
    )

    assert _decide_flight(repo, cache, _FLIGHT_GROUP, _TODAY) == SamplingStatus.DUE


def test_decide_flight_is_cache_active_when_no_observation_today_but_cached(tmp_path):
    from trip_hunter.caching import flight_search_cache_key

    repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    key = flight_search_cache_key("HAM", "PMI", "2026-10-02", "2026-10-07", "EUR")
    cache.set(key, {"offers": [], "price_insight": None})

    assert _decide_flight(repo, cache, _FLIGHT_GROUP, _TODAY) == SamplingStatus.CACHE_ACTIVE


def test_decide_hotel_is_due_with_no_history_and_no_cache(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")

    assert _decide_hotel(repo, cache, _HOTEL_GROUP, _TODAY) == SamplingStatus.DUE


def test_decide_hotel_is_already_observed_today(tmp_path):
    from trip_hunter.accommodation_price_history_repository import observation_from_accommodation_offer

    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    repo.add_observation(observation_from_accommodation_offer(_accommodation_offer(), observed_at=_OBSERVED_AT))

    assert _decide_hotel(repo, cache, _HOTEL_GROUP, _TODAY) == SamplingStatus.ALREADY_OBSERVED_TODAY


def test_decide_hotel_is_cache_active_when_no_observation_today_but_cached(tmp_path):
    from trip_hunter.caching import hotel_search_cache_key

    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    key = hotel_search_cache_key("PMI", "2026-10-02", "2026-10-07", "EUR")
    cache.set(key, [])

    assert _decide_hotel(repo, cache, _HOTEL_GROUP, _TODAY) == SamplingStatus.CACHE_ACTIVE


# --- _process_flight_target / _process_hotel_target ---------------------------


def test_process_flight_target_due_stores_observation_and_calls_provider_once(tmp_path, capsys):
    repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    provider = _CountingFlightProvider([_flight_offer()])

    status = _process_flight_target(
        _FLIGHT_GROUP, provider, repo, cache, dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT
    )

    assert status == SamplingStatus.DUE
    assert provider.search_calls == 1
    stored = repo.get_observations(
        "HAM", "PMI", date(2026, 10, 2), date(2026, 10, 7), TripType.ROUND_TRIP, "EUR"
    )
    assert len(stored) == 1
    assert stored[0].price == 184.0
    assert "DUE -> running snapshot" in capsys.readouterr().out


def test_process_flight_target_dry_run_never_calls_provider(tmp_path, capsys):
    repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    provider = _CountingFlightProvider([_flight_offer()])

    status = _process_flight_target(
        _FLIGHT_GROUP, provider, repo, cache, dry_run=True, today=_TODAY, observed_at=_OBSERVED_AT
    )

    assert status == SamplingStatus.DUE
    assert provider.search_calls == 0
    assert repo.get_observations("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 7), TripType.ROUND_TRIP, "EUR") == []
    assert "dry-run" in capsys.readouterr().out


def test_process_flight_target_already_observed_today_never_calls_provider(tmp_path, capsys):
    from trip_hunter.price_history_repository import observation_from_flight_offer

    repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    repo.add_observation(
        observation_from_flight_offer(_flight_offer(), TripType.ROUND_TRIP, observed_at=_OBSERVED_AT)
    )
    cache = FileCache(cache_dir=tmp_path / "cache")
    provider = _CountingFlightProvider([_flight_offer(999.0)])

    status = _process_flight_target(
        _FLIGHT_GROUP, provider, repo, cache, dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT
    )

    assert status == SamplingStatus.ALREADY_OBSERVED_TODAY
    assert provider.search_calls == 0
    captured = capsys.readouterr().out
    assert "Already observed today -> skipped" in captured


def test_process_flight_target_cache_active_never_calls_provider(tmp_path, capsys):
    from trip_hunter.caching import flight_search_cache_key

    repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    cache.set(
        flight_search_cache_key("HAM", "PMI", "2026-10-02", "2026-10-07", "EUR"),
        {"offers": [], "price_insight": None},
    )
    provider = _CountingFlightProvider([_flight_offer()])

    status = _process_flight_target(
        _FLIGHT_GROUP, provider, repo, cache, dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT
    )

    assert status == SamplingStatus.CACHE_ACTIVE
    assert provider.search_calls == 0
    assert "Cache active -> skipped" in capsys.readouterr().out


def test_process_hotel_target_due_stores_observation_and_calls_provider_once(tmp_path, capsys):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    provider = _CountingAccommodationProvider([_accommodation_offer()])

    status = _process_hotel_target(
        _HOTEL_GROUP, provider, repo, cache, dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT
    )

    assert status == SamplingStatus.DUE
    assert provider.search_calls == 1
    stored = repo.get_observations("PMI", date(2026, 10, 2), date(2026, 10, 7), "EUR")
    assert len(stored) == 1
    assert stored[0].price == 205.0


def test_process_hotel_target_dry_run_never_calls_provider(tmp_path):
    repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    provider = _CountingAccommodationProvider([_accommodation_offer()])

    status = _process_hotel_target(
        _HOTEL_GROUP, provider, repo, cache, dry_run=True, today=_TODAY, observed_at=_OBSERVED_AT
    )

    assert status == SamplingStatus.DUE
    assert provider.search_calls == 0
    assert repo.get_observations("PMI", date(2026, 10, 2), date(2026, 10, 7), "EUR") == []


# --- run_sampler: the core credit-safety guarantee -----------------------------


def test_run_sampler_calls_provider_exactly_once_per_due_target(tmp_path, capsys):
    """THE central proof: with 2 flight targets (1 due, 1 already observed
    today) and 2 hotel targets (1 due, 1 cache-active), exactly 1 live
    flight call and exactly 1 live hotel call happen - never more, never
    fewer, regardless of target count."""
    from trip_hunter.caching import hotel_search_cache_key
    from trip_hunter.price_history_repository import observation_from_flight_offer

    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")

    due_flight_group = _FLIGHT_GROUP
    already_observed_flight_group = FlightComparisonGroup(
        origin="HAM", destination="BCN",
        departure_date=date(2026, 10, 9), return_date=date(2026, 10, 11),
        trip_type=TripType.ROUND_TRIP, currency="EUR",
    )
    flight_repo.add_observation(
        observation_from_flight_offer(
            FlightOffer(
                origin="HAM", destination="BCN",
                departure_date=date(2026, 10, 9), return_date=date(2026, 10, 11),
                price=120.0, currency="EUR", airline="Vueling", stops=0, provider="test",
            ),
            TripType.ROUND_TRIP,
            observed_at=_OBSERVED_AT,
        )
    )

    due_hotel_group = _HOTEL_GROUP
    cached_hotel_group = AccommodationComparisonGroup(
        destination="BCN", check_in=date(2026, 10, 9), check_out=date(2026, 10, 11), currency="EUR"
    )
    cache.set(hotel_search_cache_key("BCN", "2026-10-09", "2026-10-11", "EUR"), [])

    flight_provider = _CountingFlightProvider([_flight_offer()])
    accommodation_provider = _CountingAccommodationProvider([_accommodation_offer()])

    statuses = run_sampler(
        flight_provider,
        accommodation_provider,
        flight_repo,
        hotel_repo,
        cache,
        flight_targets=[due_flight_group, already_observed_flight_group],
        hotel_targets=[due_hotel_group, cached_hotel_group],
        dry_run=False,
        today=_TODAY,
        observed_at=_OBSERVED_AT,
    )

    assert flight_provider.search_calls == 1
    assert accommodation_provider.search_calls == 1
    assert statuses == [
        SamplingStatus.DUE,
        SamplingStatus.ALREADY_OBSERVED_TODAY,
        SamplingStatus.DUE,
        SamplingStatus.CACHE_ACTIVE,
    ]
    captured = capsys.readouterr().out
    assert "1 Snapshot(s) ausgeführt, 1 übersprungen" not in captured  # sanity: not the dry-run wording
    assert "2 Snapshot(s) ausgeführt, 2 übersprungen." in captured


def test_run_sampler_dry_run_never_calls_any_provider_even_when_all_due(tmp_path, capsys):
    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    flight_provider = _CountingFlightProvider([_flight_offer()])
    accommodation_provider = _CountingAccommodationProvider([_accommodation_offer()])

    statuses = run_sampler(
        flight_provider,
        accommodation_provider,
        flight_repo,
        hotel_repo,
        cache,
        flight_targets=[_FLIGHT_GROUP],
        hotel_targets=[_HOTEL_GROUP],
        dry_run=True,
        today=_TODAY,
        observed_at=_OBSERVED_AT,
    )

    assert flight_provider.search_calls == 0
    assert accommodation_provider.search_calls == 0
    assert statuses == [SamplingStatus.DUE, SamplingStatus.DUE]
    captured = capsys.readouterr().out
    assert "2 fällig (dry-run, kein Request gesendet), 0 übersprungen." in captured


def test_run_sampler_with_no_targets_due_makes_zero_calls(tmp_path):
    from trip_hunter.caching import hotel_search_cache_key
    from trip_hunter.price_history_repository import observation_from_flight_offer

    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")

    flight_repo.add_observation(
        observation_from_flight_offer(_flight_offer(), TripType.ROUND_TRIP, observed_at=_OBSERVED_AT)
    )
    cache.set(hotel_search_cache_key("PMI", "2026-10-02", "2026-10-07", "EUR"), [])

    flight_provider = _CountingFlightProvider([_flight_offer()])
    accommodation_provider = _CountingAccommodationProvider([_accommodation_offer()])

    statuses = run_sampler(
        flight_provider,
        accommodation_provider,
        flight_repo,
        hotel_repo,
        cache,
        flight_targets=[_FLIGHT_GROUP],
        hotel_targets=[_HOTEL_GROUP],
        dry_run=False,
        today=_TODAY,
        observed_at=_OBSERVED_AT,
    )

    assert flight_provider.search_calls == 0
    assert accommodation_provider.search_calls == 0
    assert statuses == [SamplingStatus.ALREADY_OBSERVED_TODAY, SamplingStatus.CACHE_ACTIVE]


def test_run_sampler_preserves_target_processing_order(tmp_path):
    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    flight_provider = _CountingFlightProvider([_flight_offer()])
    accommodation_provider = _CountingAccommodationProvider([_accommodation_offer()])

    second_flight_group = FlightComparisonGroup(
        origin="HAM", destination="BCN",
        departure_date=date(2026, 10, 9), return_date=date(2026, 10, 11),
        trip_type=TripType.ROUND_TRIP, currency="EUR",
    )

    statuses = run_sampler(
        flight_provider,
        accommodation_provider,
        flight_repo,
        hotel_repo,
        cache,
        flight_targets=[_FLIGHT_GROUP, second_flight_group],
        hotel_targets=[],
        dry_run=True,
        today=_TODAY,
        observed_at=_OBSERVED_AT,
    )

    assert statuses == [SamplingStatus.DUE, SamplingStatus.DUE]
    assert flight_provider.search_calls == 0


# --- run(): CLI wiring, no real network ----------------------------------------


def test_run_prints_friendly_message_when_serpapi_key_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRIP_HUNTER_SERPAPI_KEY", raising=False)
    monkeypatch.delenv("VACATION_HUNTER_SERPAPI_KEY", raising=False)  # legacy fallback

    run([])

    assert "Configuration missing" in capsys.readouterr().out


def test_run_end_to_end_with_monkeypatched_clients_makes_no_real_network_call(tmp_path, monkeypatch, capsys):
    """Full run() through the real wiring, with both SerpApi HTTP clients
    monkeypatched - proves the CLI entry point itself never bypasses the
    pre-check (e.g. by accidentally using a different cache/repository
    instance than run_sampler expects)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "fake-test-key-not-real")

    import trip_hunter.providers.serpapi_client as serpapi_client_module
    import trip_hunter.providers.serpapi_hotels_client as serpapi_hotels_client_module

    flight_calls = {"count": 0}
    hotel_calls = {"count": 0}

    def fake_search_flights(self, **kwargs):
        flight_calls["count"] += 1
        return {
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

    def fake_search_hotels(self, **kwargs):
        hotel_calls["count"] += 1
        return {"properties": [{"name": "Test Hotel", "total_rate": {"extracted_lowest": 205}}]}

    monkeypatch.setattr(serpapi_client_module.SerpApiClient, "search_flights", fake_search_flights)
    monkeypatch.setattr(
        serpapi_hotels_client_module.SerpApiHotelsClient, "search_hotels", fake_search_hotels
    )

    run([])

    # Per run()'s "CREDIT BUDGET" wiring: every FLIGHT_TARGETS entry, PLUS
    # one rotating-origin flight target for today's featured trip only -
    # all due on a brand-new tmp_path DB/cache. A set() is required, not
    # len(FLIGHT_TARGETS) + 1: on a day whose rotating origin happens to
    # be HAM, the rotating target is an exact duplicate of the static HAM
    # one for that route and legitimately collapses to a single live
    # call, via the normal "already observed today" dedup - not a bug.
    from trip_hunter.sampling_targets import (
        FLIGHT_TARGETS,
        build_rotating_flight_targets,
        featured_rotation_of_the_day,
    )

    today = datetime.now(timezone.utc).date()
    featured_trip, rotation_origin = featured_rotation_of_the_day(today=today)
    expected_flight_targets = {
        *FLIGHT_TARGETS,
        *build_rotating_flight_targets(today=today, origins=[rotation_origin], base_targets=[featured_trip]),
    }

    assert flight_calls["count"] == len(expected_flight_targets)
    # Only today's featured trip's hotel target is sampled (budget cap) -
    # not the full HOTEL_TARGETS list.
    assert hotel_calls["count"] == 1

    captured = capsys.readouterr().out
    assert "TRIP HUNTER — DAILY SAMPLER" in captured
    assert "DUE -> running snapshot" in captured
    assert "Featured Trip heute" in captured


def test_worst_case_live_calls_per_run_stays_at_six():
    """Regression guard for the "CREDIT BUDGET" math in this module's
    docstring: FLIGHT_TARGETS (unthrottled) + exactly 1 rotating flight +
    exactly 1 hotel. On the daily schedule that is at most 6 x 31 = 186
    credits/month - a deliberate decision (see the workflow's budget
    note), so growth of FLIGHT_TARGETS must be a conscious one: if it
    grows, this fails and the plan/cadence need revisiting together."""
    from trip_hunter.sampling_targets import FLIGHT_TARGETS

    worst_case_calls_per_run = len(FLIGHT_TARGETS) + 1 + 1  # static flights + 1 rotating + 1 hotel

    assert worst_case_calls_per_run == 6


def test_workflow_runs_daily_at_0630_utc():
    from pathlib import Path

    workflow = (Path(__file__).parent.parent / ".github" / "workflows" / "daily_sample.yml").read_text(encoding="utf-8")

    assert '- cron: "30 6 * * *"' in workflow
    assert "* * 0,2,4" not in workflow


def test_run_dry_run_end_to_end_makes_zero_real_calls(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "fake-test-key-not-real")

    import trip_hunter.providers.serpapi_client as serpapi_client_module
    import trip_hunter.providers.serpapi_hotels_client as serpapi_hotels_client_module

    def fail_if_called(self, **kwargs):
        raise AssertionError("dry-run must never call the real client")

    monkeypatch.setattr(serpapi_client_module.SerpApiClient, "search_flights", fail_if_called)
    monkeypatch.setattr(serpapi_hotels_client_module.SerpApiHotelsClient, "search_hotels", fail_if_called)

    run(["--dry-run"])

    captured = capsys.readouterr().out
    assert "DRY RUN" in captured
    assert "dry-run, kein Request" in captured


# --- Automatic alert check + Telegram dispatch ---------------------------------


def _seed_high_baseline_flight_history(repo: PriceHistoryRepository) -> None:
    """4 prior observations at 300 EUR - a baseline any later, much
    cheaper replay will clearly beat (FLIGHT_DROP territory, well above
    DEFAULT_INSTANT_ALERT_CRITERIA's score bar)."""
    for day, price in enumerate([300.0, 300.0, 300.0, 300.0], start=1):
        repo.add_observation(
            observation_from_flight_offer(
                _flight_offer(price), TripType.ROUND_TRIP,
                observed_at=datetime(2026, 8, day, 10, 0, tzinfo=timezone.utc),
            )
        )


class _RecordingDispatch:
    """A fake dispatch_fn that records every Deal it was called with."""

    def __init__(self, result: bool = True):
        self.calls: list = []
        self._result = result

    def __call__(self, deal) -> bool:
        self.calls.append(deal)
        return self._result


def test_matching_flight_targets_finds_the_paired_route():
    assert _matching_flight_targets(_HOTEL_GROUP, [_FLIGHT_GROUP]) == [_FLIGHT_GROUP]


def test_matching_flight_targets_returns_empty_list_when_nothing_matches():
    unrelated_hotel = AccommodationComparisonGroup(
        destination="BCN", check_in=date(2026, 10, 9), check_out=date(2026, 10, 11), currency="EUR"
    )
    assert _matching_flight_targets(unrelated_hotel, [_FLIGHT_GROUP]) == []


def test_matching_flight_targets_finds_every_origin_sharing_the_same_destination_and_dates():
    """Multi-origin rotation (sampling_targets.build_rotating_flight_targets)
    can put several origins' flight targets on the same destination+dates -
    a hotel-triggered alert check must re-evaluate all of them, not just
    the first, since each origin has its own separate observation history."""
    ber_group = FlightComparisonGroup(
        origin="BER", destination=_FLIGHT_GROUP.destination,
        departure_date=_FLIGHT_GROUP.departure_date, return_date=_FLIGHT_GROUP.return_date,
        trip_type=_FLIGHT_GROUP.trip_type, currency=_FLIGHT_GROUP.currency,
    )

    matches = _matching_flight_targets(_HOTEL_GROUP, [_FLIGHT_GROUP, ber_group])

    assert matches == [_FLIGHT_GROUP, ber_group]


def test_hotel_target_for_flight_template_finds_the_paired_hotel():
    assert _hotel_target_for_flight_template(_FLIGHT_GROUP, [_HOTEL_GROUP]) == _HOTEL_GROUP


def test_hotel_target_for_flight_template_returns_none_when_nothing_matches():
    unrelated_flight = FlightComparisonGroup(
        origin="HAM", destination="BCN",
        departure_date=date(2026, 10, 9), return_date=date(2026, 10, 11),
        trip_type=_FLIGHT_GROUP.trip_type, currency="EUR",
    )
    assert _hotel_target_for_flight_template(unrelated_flight, [_HOTEL_GROUP]) is None


def test_check_and_dispatch_alert_returns_false_with_no_flight_history(tmp_path):
    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    dispatch = _RecordingDispatch()

    result = _check_and_dispatch_alert(
        _FLIGHT_GROUP, flight_repo, hotel_repo,
        alert_criteria=DealFilterCriteria(min_score=1),
        tier3_criteria=None,
        dispatch_fn=dispatch,
    )

    assert result is False
    assert dispatch.calls == []


def test_alert_dispatched_when_a_due_flight_snapshot_clears_the_criteria(tmp_path, capsys):
    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    _seed_high_baseline_flight_history(flight_repo)

    flight_provider = _CountingFlightProvider([_flight_offer(100.0)])  # clear FLIGHT_DROP vs. 300 EUR baseline
    accommodation_provider = _CountingAccommodationProvider([])
    dispatch = _RecordingDispatch()

    statuses = run_sampler(
        flight_provider, accommodation_provider, flight_repo, hotel_repo, cache,
        flight_targets=[_FLIGHT_GROUP], hotel_targets=[],
        dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT,
        dispatch_fn=dispatch,
    )

    assert statuses == [SamplingStatus.DUE]
    assert len(dispatch.calls) == 1
    dispatched_deal = dispatch.calls[0]
    assert dispatched_deal.deal_type == DealType.FLIGHT_DROP
    assert dispatched_deal.flight.price == 100.0

    captured = capsys.readouterr().out
    assert "alert-würdiger Deal" in captured


def test_no_alert_dispatched_when_price_is_not_a_deal(tmp_path):
    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    _seed_high_baseline_flight_history(flight_repo)

    # Same price as the baseline itself - no real saving, not a deal.
    flight_provider = _CountingFlightProvider([_flight_offer(300.0)])
    accommodation_provider = _CountingAccommodationProvider([])
    dispatch = _RecordingDispatch()

    run_sampler(
        flight_provider, accommodation_provider, flight_repo, hotel_repo, cache,
        flight_targets=[_FLIGHT_GROUP], hotel_targets=[],
        dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT,
        dispatch_fn=dispatch,
    )

    assert dispatch.calls == []


def test_no_alerts_flag_skips_dispatch_even_for_a_qualifying_deal(tmp_path):
    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    _seed_high_baseline_flight_history(flight_repo)

    flight_provider = _CountingFlightProvider([_flight_offer(100.0)])
    accommodation_provider = _CountingAccommodationProvider([])
    dispatch = _RecordingDispatch()

    run_sampler(
        flight_provider, accommodation_provider, flight_repo, hotel_repo, cache,
        flight_targets=[_FLIGHT_GROUP], hotel_targets=[],
        dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT,
        send_alerts=False,
        dispatch_fn=dispatch,
    )

    assert dispatch.calls == []


def test_dry_run_never_triggers_an_alert_check(tmp_path):
    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")

    flight_provider = _CountingFlightProvider([_flight_offer(100.0)])
    accommodation_provider = _CountingAccommodationProvider([])
    dispatch = _RecordingDispatch()

    statuses = run_sampler(
        flight_provider, accommodation_provider, flight_repo, hotel_repo, cache,
        flight_targets=[_FLIGHT_GROUP], hotel_targets=[],
        dry_run=True, today=_TODAY, observed_at=_OBSERVED_AT,
        dispatch_fn=dispatch,
    )

    assert statuses == [SamplingStatus.DUE]  # due, but dry-run stored nothing
    assert dispatch.calls == []


def test_hotel_only_trigger_still_checks_the_paired_flight_route_without_a_flight_call(tmp_path):
    """Updating only the hotel side must still re-check the paired route
    (using the flight's LAST stored price, not a fresh search) - and must
    prove it spends 0 additional flight credits doing so. Mirrors
    production exactly: run_sampler always receives the FULL
    FLIGHT_TARGETS/HOTEL_TARGETS lists (see run()) - the flight target is
    simply not due this run (already cache-active), not absent from the
    list, since _matching_flight_targets needs it present to pair with."""
    from trip_hunter.caching import flight_search_cache_key

    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    _seed_high_baseline_flight_history(flight_repo)
    # A cheap flight was already stored on a PREVIOUS day...
    flight_repo.add_observation(
        observation_from_flight_offer(
            _flight_offer(100.0), TripType.ROUND_TRIP,
            observed_at=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
        )
    )
    # ...and its cache is still active today, so the flight target itself
    # is CACHE_ACTIVE (skipped), not DUE, this run.
    cache.set(
        flight_search_cache_key("HAM", "PMI", "2026-10-02", "2026-10-07", "EUR"),
        {"offers": [], "price_insight": None},
    )

    flight_provider = _CountingFlightProvider([])  # never called - flight target is cache-active
    accommodation_provider = _CountingAccommodationProvider([_accommodation_offer(50.0)])
    dispatch = _RecordingDispatch()

    statuses = run_sampler(
        flight_provider, accommodation_provider, flight_repo, hotel_repo, cache,
        flight_targets=[_FLIGHT_GROUP], hotel_targets=[_HOTEL_GROUP],
        dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT,
        dispatch_fn=dispatch,
    )

    assert statuses == [SamplingStatus.CACHE_ACTIVE, SamplingStatus.DUE]
    assert flight_provider.search_calls == 0  # proves: no extra flight credit spent
    assert len(dispatch.calls) == 1


def test_flight_and_paired_hotel_both_due_only_triggers_one_alert_check(tmp_path):
    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    _seed_high_baseline_flight_history(flight_repo)

    flight_provider = _CountingFlightProvider([_flight_offer(100.0)])
    accommodation_provider = _CountingAccommodationProvider([_accommodation_offer(50.0)])
    dispatch = _RecordingDispatch()

    run_sampler(
        flight_provider, accommodation_provider, flight_repo, hotel_repo, cache,
        flight_targets=[_FLIGHT_GROUP], hotel_targets=[_HOTEL_GROUP],
        dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT,
        dispatch_fn=dispatch,
    )

    assert len(dispatch.calls) == 1  # not 2, even though both were due for the same route


def test_full_chain_with_real_send_telegram_alert_and_a_fake_session(tmp_path, monkeypatch):
    """Proves the whole real chain works end-to-end: run_sampler ->
    _check_and_dispatch_alert -> the REAL send_telegram_alert -> a fake
    requests.Session. No real network call anywhere."""
    from trip_hunter.dispatch.telegram import send_telegram_alert

    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TRIP_HUNTER_TELEGRAM_CHAT_ID", raising=False)

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"ok": True}

    class _FakeSession:
        def __init__(self):
            self.post_calls: list[dict] = []

        def post(self, url, data=None, timeout=None):
            self.post_calls.append({"url": url, "data": data})
            return _FakeResponse()

    fake_session = _FakeSession()

    def dispatch_via_real_telegram(deal) -> bool:
        return send_telegram_alert(deal, bot_token="123:ABC", chat_id="42", session=fake_session)

    flight_repo = PriceHistoryRepository(db_path=tmp_path / "flights.db")
    hotel_repo = AccommodationPriceHistoryRepository(db_path=tmp_path / "hotels.db")
    cache = FileCache(cache_dir=tmp_path / "cache")
    _seed_high_baseline_flight_history(flight_repo)

    flight_provider = _CountingFlightProvider([_flight_offer(100.0)])
    accommodation_provider = _CountingAccommodationProvider([])

    run_sampler(
        flight_provider, accommodation_provider, flight_repo, hotel_repo, cache,
        flight_targets=[_FLIGHT_GROUP], hotel_targets=[],
        dry_run=False, today=_TODAY, observed_at=_OBSERVED_AT,
        dispatch_fn=dispatch_via_real_telegram,
    )

    assert len(fake_session.post_calls) == 1
    call = fake_session.post_calls[0]
    assert call["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert call["data"]["chat_id"] == "42"
    assert "nach Palma de Mallorca" in call["data"]["text"]


def test_run_uses_default_instant_alert_criteria_and_real_dispatch_by_default():
    """Sanity check on run_sampler's defaults - min_score bar and the
    allowed deal types must match build_newsletter.py's
    DEFAULT_INSTANT_ALERT_CRITERIA exactly (single source of truth, not a
    second, possibly-drifting copy). dispatch_fn defaults to the real
    dual-channel dispatch_deal_alert (Free/VIP routing, see
    dispatch/telegram.py), not the older single-channel
    send_telegram_alert."""
    import inspect

    from trip_hunter.build_newsletter import DEFAULT_INSTANT_ALERT_CRITERIA, DEFAULT_TIER_3_CRITERIA
    from trip_hunter.dispatch.telegram import dispatch_deal_alert

    signature = inspect.signature(run_sampler)
    assert signature.parameters["alert_criteria"].default is DEFAULT_INSTANT_ALERT_CRITERIA
    assert signature.parameters["tier3_criteria"].default is DEFAULT_TIER_3_CRITERIA
    assert signature.parameters["dispatch_fn"].default is dispatch_deal_alert
