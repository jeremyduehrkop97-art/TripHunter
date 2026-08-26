from datetime import date

import pytest

from vacation_hunter.real_flight_demo import _parse_args, run


def test_parse_args_defaults():
    origin, destination, departure, return_ = _parse_args([])
    assert origin == "HAM"
    assert destination == "PMI"
    assert departure == date(2026, 10, 2)
    assert return_ == date(2026, 10, 7)


def test_parse_args_custom():
    origin, destination, departure, return_ = _parse_args(
        ["ber", "bcn", "2026-11-01", "2026-11-08"]
    )
    assert origin == "BER"
    assert destination == "BCN"
    assert departure == date(2026, 11, 1)
    assert return_ == date(2026, 11, 8)


def test_parse_args_wrong_count_exits():
    with pytest.raises(SystemExit):
        _parse_args(["HAM"])


def test_parse_args_invalid_date_exits():
    with pytest.raises(SystemExit):
        _parse_args(["HAM", "PMI", "not-a-date", "2026-10-07"])


def test_run_without_api_key_prints_friendly_message_and_returns_empty(monkeypatch, capsys):
    monkeypatch.delenv("VACATION_HUNTER_FLIGHT_API_KEY", raising=False)
    monkeypatch.delenv("VACATION_HUNTER_FLIGHT_API_SECRET", raising=False)

    deals = run([])

    assert deals == []
    captured = capsys.readouterr()
    assert "Configuration missing" in captured.out
