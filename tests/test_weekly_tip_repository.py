"""weekly_tip_repository.py: rotation state for the weekly travel-hack tip."""

from __future__ import annotations

from datetime import datetime, timezone

from trip_hunter.weekly_tip_repository import WeeklyTipRepository

_NOW = datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc)  # a Sunday


def test_never_sent_before_returns_none(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    assert repo.last_sent() is None


def test_record_sent_then_last_sent_round_trips(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    repo.record_sent(1, now=_NOW)

    state = repo.last_sent()
    assert state.last_index == 1
    assert state.last_sent_at == _NOW


def test_recording_again_overwrites_the_single_row_not_appends(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    repo.record_sent(0, now=_NOW)
    repo.record_sent(2, now=_NOW.replace(day=11))

    state = repo.last_sent()
    assert state.last_index == 2
    assert state.last_sent_at.day == 11


def test_state_survives_a_fresh_repository_instance_on_the_same_file(tmp_path):
    """Same guarantee as the other state repositories in this project
    (feed_seen_repository.py, free_queue_repository.py) - the SQLite
    file, not the Python object, is what persists (restored between
    GitHub Actions runs via actions/cache)."""
    db_path = tmp_path / "t.db"
    WeeklyTipRepository(db_path).record_sent(2, now=_NOW)

    reopened = WeeklyTipRepository(db_path)
    assert reopened.last_sent().last_index == 2


def test_record_sent_defaults_now_to_the_current_time(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    before = datetime.now(timezone.utc)
    repo.record_sent(0)
    after = datetime.now(timezone.utc)

    sent_at = repo.last_sent().last_sent_at
    assert before <= sent_at <= after
