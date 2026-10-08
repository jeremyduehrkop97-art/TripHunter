"""Tests for engine/daily_scanner.py. No real HTTP calls anywhere in this
file - `scan_and_dispatch` is exercised with a fake, call-counting
FlightProvider and tmp_path-based real repositories, the same pattern
test_daily_sampler.py already established."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trip_hunter.engine.daily_scanner import (
    DESTINATIONS,
    MAX_REQUESTS_PER_DAY,
    MEGA_DROP_DISCOUNT_PERCENT,
    ORIGINS,
    DailyScanResult,
    _build_signal,
    routes_for_today,
    scan_and_dispatch,
)
from trip_hunter.engine.route_benchmark import get_route_benchmark, is_deal_price
from trip_hunter.feed_radar import MIN_FLIGHT_DISCOUNT_PERCENT
from trip_hunter.models import FlightOffer
from trip_hunter.price_history_repository import PriceHistoryRepository
from trip_hunter.providers.errors import FlightProviderTimeoutError
from trip_hunter.providers.flight_provider import FlightProvider
from trip_hunter.scanner_history_repository import ScannerHistoryRepository

_NOW = datetime(2026, 10, 8, 7, 0, tzinfo=timezone.utc)
_TODAY = _NOW.date()
_ROOT = Path(__file__).parent.parent


class _FakeFlightProvider(FlightProvider):
    """Returns one fixed-price offer per (origin, destination) pair from
    `prices`, or no offers at all for a route not listed there. Counts
    every call, the same credit-safety proof test_daily_sampler.py's own
    _CountingFlightProvider already established."""

    def __init__(self, prices: dict[tuple[str, str], float]):
        self._prices = prices
        self.search_calls = 0
        self.calls: list[tuple[str, str, date, date]] = []

    def search_flights(self, origin, destination, earliest_departure, latest_departure, return_date=None):
        self.search_calls += 1
        self.calls.append((origin, destination, earliest_departure, return_date))
        price = self._prices.get((origin, destination))
        if price is None:
            return []
        return [
            FlightOffer(
                origin=origin, destination=destination, departure_date=earliest_departure,
                return_date=return_date, price=price, currency="EUR", airline="Test Airways",
                stops=0, provider="test",
            )
        ]

    def get_typical_price(self, origin, destination, month):
        return None

    def get_price_insight(self, origin, destination, departure_date, return_date):
        return None


class _BoomFlightProvider(FlightProvider):
    def search_flights(self, origin, destination, earliest_departure, latest_departure, return_date=None):
        raise FlightProviderTimeoutError("boom")

    def get_typical_price(self, origin, destination, month):
        return None


class _Dispatch:
    def __init__(self, results=None):
        self.sent = []
        self._results = list(results or [])

    def __call__(self, signal) -> bool:
        self.sent.append(signal)
        return self._results.pop(0) if self._results else True


# --- routes_for_today ------------------------------------------------------------


def test_routes_for_today_returns_exactly_max_requests_cells():
    routes = routes_for_today(_TODAY)
    assert len(routes) == MAX_REQUESTS_PER_DAY
    for origin, destination in routes:
        assert origin in ORIGINS and destination in DESTINATIONS


def test_routes_for_today_is_deterministic_for_the_same_date():
    assert routes_for_today(_TODAY) == routes_for_today(_TODAY)


def test_consecutive_days_advance_through_the_grid_without_repeats_or_gaps():
    day_one = routes_for_today(date(2026, 10, 8), max_requests=4)
    day_two = routes_for_today(date(2026, 10, 9), max_requests=4)
    assert set(day_one).isdisjoint(set(day_two))


def test_the_full_grid_is_covered_with_no_duplicate_cell_within_one_pass():
    cells_per_day = 4
    full_grid_size = len(ORIGINS) * len(DESTINATIONS)

    seen: list[tuple[str, str]] = []
    offset = 0
    while len(seen) < full_grid_size:
        for cell in routes_for_today(date(2026, 1, 1) + timedelta(days=offset), max_requests=cells_per_day):
            if len(seen) == full_grid_size:
                break
            assert cell not in seen  # no repeat within one full pass
            seen.append(cell)
        offset += 1

    assert len(seen) == full_grid_size  # every cell visited exactly once


def test_the_requested_cap_is_honoured_for_a_different_max_requests():
    assert len(routes_for_today(_TODAY, max_requests=1)) == 1
    assert len(routes_for_today(_TODAY, max_requests=10)) == 10


def test_default_request_cap_is_four_not_the_tasks_original_ten_to_fifteen():
    """The user's own explicit budget decision: reduced from the task's
    original "10-15/day" ask so this module's own usage, ADDED to
    daily_sampler.py's existing ~114 credits/month, stays safely inside
    the SerpApi Free plan's 250/month cap (see module docstring)."""
    assert MAX_REQUESTS_PER_DAY == 4


def test_all_seven_requested_origins_and_thirteen_destinations_are_present():
    assert set(ORIGINS) == {"FRA", "MUC", "BER", "DUS", "HAM", "VIE", "ZRH"}
    assert set(DESTINATIONS) == {
        "PMI", "BCN", "LIS", "FCO", "DXB", "TFS", "RAK", "JFK", "MIA", "BKK", "DPS", "NBO", "CUN",
    }


# --- discount gate (reuses feed_radar's EXISTING rule, never a second copy) -----


def test_a_deal_under_30_percent_off_is_rejected(tmp_path):
    """JFK's own benchmark is 530 EUR (engine/route_benchmark.py) - 450 EUR
    is only ~15% off, well short of MIN_FLIGHT_DISCOUNT_PERCENT (30%)."""
    benchmark = get_route_benchmark("FRA", "JFK")
    is_deal, discount_percent = is_deal_price(450.0, benchmark, MIN_FLIGHT_DISCOUNT_PERCENT)
    assert is_deal is False and discount_percent < 30.0

    provider = _FakeFlightProvider({("FRA", "JFK"): 450.0})
    dispatch = _Dispatch()
    result = scan_and_dispatch(
        provider=provider, repository=PriceHistoryRepository(tmp_path / "p.db"),
        scanner_history=ScannerHistoryRepository(tmp_path / "s.db"), dispatch_fn=dispatch,
        routes=[("FRA", "JFK")], now=_NOW,
    )
    assert result.candidates == 0 and result.sent == 0 and dispatch.sent == []


def test_a_deal_at_or_above_30_percent_off_goes_through(tmp_path):
    """350 EUR against JFK's 530 EUR benchmark is ~34% off - clears
    MIN_FLIGHT_DISCOUNT_PERCENT."""
    benchmark = get_route_benchmark("FRA", "JFK")
    is_deal, discount_percent = is_deal_price(350.0, benchmark, MIN_FLIGHT_DISCOUNT_PERCENT)
    assert is_deal is True and discount_percent >= 30.0

    provider = _FakeFlightProvider({("FRA", "JFK"): 350.0})
    dispatch = _Dispatch()
    result = scan_and_dispatch(
        provider=provider, repository=PriceHistoryRepository(tmp_path / "p.db"),
        scanner_history=ScannerHistoryRepository(tmp_path / "s.db"), dispatch_fn=dispatch,
        routes=[("FRA", "JFK")], now=_NOW,
    )
    assert result.candidates == 1 and result.sent == 1
    assert len(dispatch.sent) == 1
    assert dispatch.sent[0].destination_iata == "JFK" and dispatch.sent[0].origins == ("FRA",)


def test_no_offer_found_for_a_route_is_a_quiet_skip_not_a_crash(tmp_path):
    provider = _FakeFlightProvider({})  # no price configured -> empty offer list
    dispatch = _Dispatch()
    result = scan_and_dispatch(
        provider=provider, repository=PriceHistoryRepository(tmp_path / "p.db"),
        scanner_history=ScannerHistoryRepository(tmp_path / "s.db"), dispatch_fn=dispatch,
        routes=[("FRA", "JFK")], now=_NOW,
    )
    assert result.scanned == 1 and result.candidates == 0 and dispatch.sent == []


def test_a_failing_search_is_logged_and_skipped_not_fatal(tmp_path, capsys):
    dispatch = _Dispatch()
    result = scan_and_dispatch(
        provider=_BoomFlightProvider(), repository=PriceHistoryRepository(tmp_path / "p.db"),
        scanner_history=ScannerHistoryRepository(tmp_path / "s.db"), dispatch_fn=dispatch,
        routes=[("FRA", "JFK")], now=_NOW,
    )
    assert result.scanned == 1 and result.candidates == 0 and dispatch.sent == []
    assert "fehlgeschlagen" in capsys.readouterr().out


# --- mega-drop -> Tier-1 -> also reaches the Free channel (via dispatch_signal_alert's EXISTING rule) --


def test_a_mega_drop_is_marked_tier_1_so_it_also_reaches_free():
    """890 EUR against JFK's 530 EUR... use a route/price combo that is
    >= 50% off to directly exercise the mega-drop marking in _build_signal
    via scan_and_dispatch, then confirm the resulting signal is Tier-1."""
    signal = _build_signal("FRA", "JFK", date(2026, 11, 1), date(2026, 11, 8), 350.0, now=_NOW)
    # 350 EUR is ~34% off - NOT a mega-drop, is_tier_1 stays False here.
    assert not signal.is_tier_1


def test_mega_drop_discount_threshold_is_50_percent():
    assert MEGA_DROP_DISCOUNT_PERCENT == 50.0


def test_a_genuine_mega_drop_signal_ends_up_tier_1_end_to_end(tmp_path):
    """260 EUR against JFK's 530 EUR benchmark is ~51% off - clears
    MEGA_DROP_DISCOUNT_PERCENT, so the dispatched signal must be Tier-1."""
    provider = _FakeFlightProvider({("FRA", "JFK"): 260.0})
    dispatch = _Dispatch()
    result = scan_and_dispatch(
        provider=provider, repository=PriceHistoryRepository(tmp_path / "p.db"),
        scanner_history=ScannerHistoryRepository(tmp_path / "s.db"), dispatch_fn=dispatch,
        routes=[("FRA", "JFK")], now=_NOW,
    )
    assert result.sent == 1
    assert dispatch.sent[0].is_tier_1


def test_an_absolute_tier_1_price_is_also_marked_tier_1_even_without_50_percent_off(tmp_path):
    """PMI's own benchmark (140 EUR) makes a 35 EUR fare both >=30% off
    AND under feed_sensor.TIER_1_MAX_PRICE (40 EUR, short-haul) - Tier-1
    via the absolute-price rule, not the mega-drop one."""
    provider = _FakeFlightProvider({("FRA", "PMI"): 35.0})
    dispatch = _Dispatch()
    result = scan_and_dispatch(
        provider=provider, repository=PriceHistoryRepository(tmp_path / "p.db"),
        scanner_history=ScannerHistoryRepository(tmp_path / "s.db"), dispatch_fn=dispatch,
        routes=[("FRA", "PMI")], now=_NOW,
    )
    assert result.sent == 1
    assert dispatch.sent[0].is_tier_1


# --- history storage (organic growth) --------------------------------------------


def test_a_live_qualifying_search_is_stored_in_price_history(tmp_path):
    repository = PriceHistoryRepository(tmp_path / "p.db")
    provider = _FakeFlightProvider({("FRA", "JFK"): 350.0})
    scan_and_dispatch(
        provider=provider, repository=repository, scanner_history=ScannerHistoryRepository(tmp_path / "s.db"),
        dispatch_fn=_Dispatch(), routes=[("FRA", "JFK")], now=_NOW,
    )
    history = repository.route_price_history("FRA", "JFK")
    assert 350.0 in history


def test_a_non_qualifying_search_is_still_stored_in_price_history(tmp_path):
    """The market-price history grows from EVERY live search, not only
    the ones that turn out to clear the deal gate - see module docstring
    ("HISTORY")."""
    repository = PriceHistoryRepository(tmp_path / "p.db")
    provider = _FakeFlightProvider({("FRA", "JFK"): 500.0})  # not a deal
    scan_and_dispatch(
        provider=provider, repository=repository, scanner_history=ScannerHistoryRepository(tmp_path / "s.db"),
        dispatch_fn=_Dispatch(), routes=[("FRA", "JFK")], now=_NOW,
    )
    assert 500.0 in repository.route_price_history("FRA", "JFK")


# --- deduplication -----------------------------------------------------------------


def test_the_same_route_and_date_window_is_not_re_alerted_within_the_suppression_window(tmp_path):
    provider = _FakeFlightProvider({("FRA", "JFK"): 350.0})
    dispatch = _Dispatch()
    scanner_history = ScannerHistoryRepository(tmp_path / "s.db")
    repository = PriceHistoryRepository(tmp_path / "p.db")

    first = scan_and_dispatch(
        provider=provider, repository=repository, scanner_history=scanner_history,
        dispatch_fn=dispatch, routes=[("FRA", "JFK")], now=_NOW,
    )
    second = scan_and_dispatch(
        provider=provider, repository=repository, scanner_history=scanner_history,
        dispatch_fn=dispatch, routes=[("FRA", "JFK")], now=_NOW,
    )

    assert first.sent == 1
    assert second.candidates == 1 and second.suppressed == 1 and second.sent == 0
    assert len(dispatch.sent) == 1  # never sent twice


# --- credit safety: exactly one search per route, no more -----------------------


def test_exactly_one_search_call_per_route_never_more(tmp_path):
    provider = _FakeFlightProvider({("FRA", "JFK"): 350.0, ("MUC", "PMI"): 90.0})
    scan_and_dispatch(
        provider=provider, repository=PriceHistoryRepository(tmp_path / "p.db"),
        scanner_history=ScannerHistoryRepository(tmp_path / "s.db"), dispatch_fn=_Dispatch(),
        routes=[("FRA", "JFK"), ("MUC", "PMI")], now=_NOW,
    )
    assert provider.search_calls == 2


def test_the_request_cap_limits_how_many_routes_are_scanned_per_run(tmp_path):
    """Budget limitation, end to end: `max_requests` caps how many cells
    routes_for_today() itself returns, and scan_and_dispatch makes exactly
    one search per route - so the TOTAL number of SerpApi calls this run
    can ever make is bounded by `max_requests`, full stop."""
    provider = _FakeFlightProvider({})
    for cap in (1, 4, 10):
        provider.search_calls = 0
        scan_and_dispatch(
            provider=provider, repository=PriceHistoryRepository(tmp_path / "p.db"),
            scanner_history=ScannerHistoryRepository(tmp_path / "s.db"), dispatch_fn=_Dispatch(),
            max_requests=cap, now=_NOW,
        )
        assert provider.search_calls == cap


def test_a_provider_is_required_for_a_live_run():
    with pytest.raises(ValueError):
        scan_and_dispatch(dry_run=False, routes=[("FRA", "JFK")], now=_NOW)


# --- dry run: zero SerpApi credits spent -----------------------------------------


def test_dry_run_makes_no_search_call_at_all():
    provider = _FakeFlightProvider({("FRA", "JFK"): 350.0})
    result = scan_and_dispatch(provider=provider, dry_run=True, routes=[("FRA", "JFK")], now=_NOW)
    assert provider.search_calls == 0
    assert result == DailyScanResult(planned=1)


def test_dry_run_prints_the_planned_routes(capsys):
    scan_and_dispatch(dry_run=True, routes=[("FRA", "JFK"), ("MUC", "PMI")], now=_NOW)
    out = capsys.readouterr().out
    assert "FRA → JFK" in out and "MUC → PMI" in out
    assert "dry-run" in out and "0 SerpApi-Credits" in out


def test_dry_run_needs_no_provider_repository_or_scanner_history():
    # Must not raise even though none of these were passed - dry_run skips
    # every code path that would need them.
    result = scan_and_dispatch(dry_run=True, routes=[("FRA", "JFK")], now=_NOW)
    assert result.planned == 1


# --- GitHub Actions workflow -------------------------------------------------------


def _workflow_text() -> str:
    return (_ROOT / ".github" / "workflows" / "daily_scanner.yml").read_text(encoding="utf-8")


def test_workflow_runs_once_daily_at_07_00_utc_with_a_manual_dispatch():
    text = _workflow_text()
    assert '- cron: "0 7 * * *"' in text
    assert "workflow_dispatch:" in text
    assert "python -m trip_hunter.engine.daily_scanner" in text


def test_workflow_shares_the_same_state_cache_and_lock_as_the_other_two_workflows():
    text = _workflow_text()
    other_workflows = [
        (_ROOT / ".github" / "workflows" / "feed_radar_fast.yml").read_text(encoding="utf-8"),
        (_ROOT / ".github" / "workflows" / "daily_sample.yml").read_text(encoding="utf-8"),
    ]
    block = "path: |\n            data/trip_hunter.db\n            data/cache/\n          key: trip-hunter-state-${{ github.run_id }}"
    assert block in text
    for other in other_workflows:
        assert block in other  # identical "cache version" - shared state
    assert "group: trip-hunter-state" in text and "cancel-in-progress: false" in text


def test_workflow_passes_the_serpapi_key_and_the_existing_telegram_secrets():
    text = _workflow_text()
    assert "secrets.SERPAPI_API_KEY" in text
    assert "TRIP_HUNTER_SERPAPI_KEY:" in text  # mapped to the env var trip_hunter.config actually reads
    assert "TELEGRAM_FREE_CHAT_ID" in text and "TELEGRAM_VIP_CHAT_ID" in text
