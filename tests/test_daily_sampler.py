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
    _decide_flight,
    _decide_hotel,
    _parse_args,
    _process_flight_target,
    _process_hotel_target,
    run,
    run_sampler,
)
from trip_hunter.models import (
    AccommodationComparisonGroup,
    AccommodationOffer,
    FlightComparisonGroup,
    FlightOffer,
    TripType,
)
from trip_hunter.price_history_repository import PriceHistoryRepository
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

    # Exactly as many live calls as there are FLIGHT_TARGETS / HOTEL_TARGETS
    # entries - all of them are due on a brand-new tmp_path DB/cache.
    from trip_hunter.sampling_targets import FLIGHT_TARGETS, HOTEL_TARGETS

    assert flight_calls["count"] == len(FLIGHT_TARGETS)
    assert hotel_calls["count"] == len(HOTEL_TARGETS)

    captured = capsys.readouterr().out
    assert "TRIP HUNTER — DAILY SAMPLER" in captured
    assert "DUE -> running snapshot" in captured


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
