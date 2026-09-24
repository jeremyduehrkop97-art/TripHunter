"""Feed-sensor -> sampler integration, with mocked signals. No real HTTP
(tests/conftest.py blocks the feed fetch) and no real SerpApi call."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trip_hunter.daily_sampler import _fetch_signals, run
from trip_hunter.engine.feed_sensor import DealSignal
from trip_hunter.models import FlightComparisonGroup, TripType
from trip_hunter.sampling_targets import (
    FLIGHT_TARGETS,
    MAX_SIGNAL_TARGETS_PER_RUN,
    build_signal_flight_targets,
)

_TODAY = date(2026, 9, 24)  # a Thursday


def _signal(
    *,
    origins=("BER",),
    destination_iata="MAD",
    tier_1=True,
    title="Preisfehler: Madrid ab Berlin 25€",
) -> DealSignal:
    return DealSignal(
        source="test", title=title, link=f"https://x/{title}", origins=tuple(origins),
        tier_1_reasons=("keyword:preisfehler",) if tier_1 else (),
        destination="x", destination_iata=destination_iata, price=25.0,
    )


# --- build_signal_flight_targets: eligibility ------------------------------------


def test_valid_tier_1_signal_becomes_a_round_trip_target():
    (target,) = build_signal_flight_targets([_signal()], today=_TODAY)

    assert (target.origin, target.destination) == ("BER", "MAD")
    assert target.trip_type is TripType.ROUND_TRIP and target.currency == "EUR"


def test_non_tier_1_signal_never_triggers_a_scan():
    assert build_signal_flight_targets([_signal(tier_1=False)], today=_TODAY) == []


@pytest.mark.parametrize(
    "signal",
    [
        _signal(destination_iata=None),  # destination unknown
        _signal(origins=()),  # no German origin
        _signal(origins=("VIE",)),  # not a German origin
        _signal(origins=("BER",), destination_iata="BER"),  # origin == destination
    ],
)
def test_signals_without_valid_origin_and_destination_are_ignored(signal):
    assert build_signal_flight_targets([signal], today=_TODAY) == []


def test_first_german_origin_is_used():
    (target,) = build_signal_flight_targets([_signal(origins=("VIE", "MUC", "FRA"))], today=_TODAY)
    assert target.origin == "MUC"


# --- dates ---------------------------------------------------------------------


def test_default_dates_are_a_friday_to_sunday_weekend_at_least_two_weeks_out():
    (target,) = build_signal_flight_targets([_signal()], today=_TODAY)

    assert target.departure_date.weekday() == 4  # Friday
    assert target.return_date.weekday() == 6  # Sunday
    assert (target.departure_date - _TODAY).days >= 14


def test_upcoming_template_dates_for_the_destination_are_reused():
    (target,) = build_signal_flight_targets([_signal(destination_iata="PMI")], today=_TODAY)

    pmi = next(t for t in FLIGHT_TARGETS if t.destination == "PMI")
    assert (target.departure_date, target.return_date) == (pmi.departure_date, pmi.return_date)


def test_past_template_dates_are_not_reused():
    stale = FlightComparisonGroup("HAM", "PMI", date(2026, 1, 2), date(2026, 1, 4), TripType.ROUND_TRIP, "EUR")

    (target,) = build_signal_flight_targets(
        [_signal(destination_iata="PMI")], today=_TODAY, templates=[stale]
    )

    assert target.departure_date > _TODAY


# --- credit guard --------------------------------------------------------------


def test_at_most_one_signal_target_per_run_even_with_many_valid_signals():
    signals = [_signal(destination_iata=code, title=code) for code in ("MAD", "LIS", "ATH", "BKK")]

    targets = build_signal_flight_targets(signals, today=_TODAY)

    assert MAX_SIGNAL_TARGETS_PER_RUN == 1
    assert len(targets) == 1 and targets[0].destination == "MAD"  # first signal wins


def test_cap_cannot_be_raised_by_the_caller():
    signals = [_signal(destination_iata=code, title=code) for code in ("MAD", "LIS", "ATH")]
    assert len(build_signal_flight_targets(signals, today=_TODAY, max_targets=99)) == 1
    assert build_signal_flight_targets(signals, today=_TODAY, max_targets=0) == []


def test_invalid_and_non_tier_1_signals_do_not_use_up_the_cap():
    signals = [
        _signal(tier_1=False, title="a"),
        _signal(destination_iata=None, title="b"),
        _signal(destination_iata="LIS", title="c"),
    ]
    (target,) = build_signal_flight_targets(signals, today=_TODAY)
    assert target.destination == "LIS"


def test_signal_for_an_already_targeted_route_is_skipped_for_the_next_one():
    (existing,) = build_signal_flight_targets([_signal()], today=_TODAY)
    signals = [_signal(title="dup"), _signal(destination_iata="LIS", title="next")]

    (target,) = build_signal_flight_targets(signals, today=_TODAY, existing_targets=[existing])

    assert target.destination == "LIS"


def test_no_signals_no_targets():
    assert build_signal_flight_targets([], today=_TODAY) == []


# --- _fetch_signals ------------------------------------------------------------


def test_fetch_signals_asks_for_tier_1_only(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "trip_hunter.daily_sampler.scan_feeds", lambda **kw: seen.update(kw) or [_signal()]
    )
    assert len(_fetch_signals()) == 1
    assert seen == {"tier_1_only": True}


def test_fetch_signals_swallows_any_failure(monkeypatch, capsys):
    def boom(**kwargs):
        raise RuntimeError("feed exploded")

    monkeypatch.setattr("trip_hunter.daily_sampler.scan_feeds", boom)
    assert _fetch_signals() == []
    assert "Feed-Sensor übersprungen" in capsys.readouterr().out


# --- run(): signal replaces the rotating target, budget unchanged ----------------


def _patch_serpapi(monkeypatch):
    import trip_hunter.providers.serpapi_client as flights_mod
    import trip_hunter.providers.serpapi_hotels_client as hotels_mod

    calls = {"flights": [], "hotels": 0}

    def fake_flights(self, **kwargs):
        calls["flights"].append((kwargs.get("origin"), kwargs.get("destination")))
        return {"best_flights": [{"price": 184, "flights": [{
            "departure_airport": {"id": "HAM", "time": "2026-10-02 21:50"},
            "arrival_airport": {"id": "PMI", "time": "2026-10-02 23:20"}, "airline": "Vueling"}]}],
            "other_flights": []}

    def fake_hotels(self, **kwargs):
        calls["hotels"] += 1
        return {"properties": [{"name": "Test Hotel", "total_rate": {"extracted_lowest": 205}}]}

    monkeypatch.setattr(flights_mod.SerpApiClient, "search_flights", fake_flights)
    monkeypatch.setattr(hotels_mod.SerpApiHotelsClient, "search_hotels", fake_hotels)
    return calls


def _live_calls(tmp_path, name, monkeypatch, argv=()):
    """Run the real run() in a fresh empty working dir (fresh DB + cache,
    so every target is due) and return the fake SerpApi call log."""
    workdir = tmp_path / name
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "fake-test-key-not-real")
    calls = _patch_serpapi(monkeypatch)
    run(["--no-alerts", *argv])
    return calls


def test_signal_scan_replaces_the_rotating_target_so_the_budget_does_not_grow(tmp_path, monkeypatch, capsys):
    signals = [_signal(origins=("MUC",), destination_iata=c, title=c) for c in ("MAD", "LIS", "ATH")]
    monkeypatch.setattr("trip_hunter.daily_sampler.scan_feeds", lambda **kw: signals)

    calls = _live_calls(tmp_path, "with", monkeypatch)

    # Credit guard: the static targets + exactly ONE signal scan (the first
    # signal), never one per signal - the same ceiling as the rotating
    # target it replaces.
    assert len(calls["flights"]) == len(FLIGHT_TARGETS) + 1
    assert calls["flights"].count(("MUC", "MAD")) == 1
    assert not any(dest in ("LIS", "ATH") for _, dest in calls["flights"])
    assert calls["hotels"] == 1  # the featured trip's hotel, unchanged
    assert "Feed-Signal (Tier 1)" in capsys.readouterr().out


def test_no_signals_flag_skips_the_feed_entirely(tmp_path, monkeypatch):
    def must_not_run(**kwargs):
        raise AssertionError("feeds must not be scanned with --no-signals")

    monkeypatch.setattr("trip_hunter.daily_sampler.scan_feeds", must_not_run)

    calls = _live_calls(tmp_path, "off", monkeypatch, argv=["--no-signals"])

    assert calls["flights"]


def test_non_tier_1_signal_causes_no_extra_scan(tmp_path, monkeypatch):
    baseline = _live_calls(tmp_path, "base", monkeypatch)
    monkeypatch.setattr(
        "trip_hunter.daily_sampler.scan_feeds", lambda **kw: [_signal(tier_1=False, origins=("MUC",))]
    )

    calls = _live_calls(tmp_path, "sig", monkeypatch)

    assert calls["flights"] == baseline["flights"]
