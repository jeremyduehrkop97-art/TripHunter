"""Tests for scanner_history_repository.py."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from trip_hunter.scanner_history_repository import SCANNER_SUPPRESSION_DAYS, ScannerHistoryRepository

_NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
_DEP, _RET = date(2026, 11, 14), date(2026, 11, 21)


def test_an_unmarked_route_was_never_recently_alerted(tmp_path):
    repo = ScannerHistoryRepository(tmp_path / "h.db")
    assert not repo.was_recently_alerted("FRA", "JFK", _DEP, _RET, now=_NOW)


def test_a_freshly_marked_route_is_suppressed(tmp_path):
    repo = ScannerHistoryRepository(tmp_path / "h.db")
    repo.mark_alerted("FRA", "JFK", _DEP, _RET, now=_NOW)
    assert repo.was_recently_alerted("FRA", "JFK", _DEP, _RET, now=_NOW)
    assert repo.was_recently_alerted("FRA", "JFK", _DEP, _RET, now=_NOW + timedelta(days=1))


def test_suppression_expires_after_the_configured_number_of_days(tmp_path):
    repo = ScannerHistoryRepository(tmp_path / "h.db")
    repo.mark_alerted("FRA", "JFK", _DEP, _RET, now=_NOW)

    just_before = _NOW + timedelta(days=SCANNER_SUPPRESSION_DAYS) - timedelta(seconds=1)
    just_after = _NOW + timedelta(days=SCANNER_SUPPRESSION_DAYS) + timedelta(seconds=1)
    assert repo.was_recently_alerted("FRA", "JFK", _DEP, _RET, now=just_before)
    assert not repo.was_recently_alerted("FRA", "JFK", _DEP, _RET, now=just_after)


def test_within_days_can_be_overridden_per_call(tmp_path):
    repo = ScannerHistoryRepository(tmp_path / "h.db")
    repo.mark_alerted("FRA", "JFK", _DEP, _RET, now=_NOW)
    assert not repo.was_recently_alerted("FRA", "JFK", _DEP, _RET, within_days=1, now=_NOW + timedelta(days=2))


def test_different_dates_for_the_same_route_are_independent(tmp_path):
    repo = ScannerHistoryRepository(tmp_path / "h.db")
    repo.mark_alerted("FRA", "JFK", _DEP, _RET, now=_NOW)
    other_dep, other_ret = date(2026, 12, 1), date(2026, 12, 8)
    assert not repo.was_recently_alerted("FRA", "JFK", other_dep, other_ret, now=_NOW)


def test_different_origins_or_destinations_for_the_same_dates_are_independent(tmp_path):
    repo = ScannerHistoryRepository(tmp_path / "h.db")
    repo.mark_alerted("FRA", "JFK", _DEP, _RET, now=_NOW)
    assert not repo.was_recently_alerted("MUC", "JFK", _DEP, _RET, now=_NOW)
    assert not repo.was_recently_alerted("FRA", "MIA", _DEP, _RET, now=_NOW)


def test_marking_again_refreshes_the_suppression_window(tmp_path):
    repo = ScannerHistoryRepository(tmp_path / "h.db")
    repo.mark_alerted("FRA", "JFK", _DEP, _RET, now=_NOW)
    later = _NOW + timedelta(days=5)
    repo.mark_alerted("FRA", "JFK", _DEP, _RET, now=later)  # idempotent re-mark, refreshes alerted_at

    # Still suppressed 5 days after the REFRESH (10 days after the
    # original mark - would have expired under the original timestamp).
    assert repo.was_recently_alerted("FRA", "JFK", _DEP, _RET, now=later + timedelta(days=5))


def test_default_suppression_is_seven_days():
    assert SCANNER_SUPPRESSION_DAYS == 7
