"""Phase 2: wider destination pool + hub rotation for the daily featured
trip. Pure repertoire extension - the static batch and the per-run
credit budget must not change."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from trip_hunter.alerts.destination_context import _FALLBACK_CONTEXT, destination_context
from trip_hunter.alerts.destination_images import FALLBACK_IMAGE_URL, destination_image_url
from trip_hunter.daily_sampler import _hotel_target_for_flight_template
from trip_hunter.models import FlightComparisonGroup, TripType
from trip_hunter.providers.serpapi_accommodation_provider import _DESTINATION_QUERY
from trip_hunter.sampling_targets import (
    FLIGHT_TARGETS,
    HOTEL_TARGETS,
    ROTATION_FLIGHT_TEMPLATES,
    ROTATION_HOTEL_TARGETS,
    build_rotating_flight_targets,
    featured_rotation_of_the_day,
)

_HUBS = ["HAM", "BER", "FRA", "MUC", "DUS"]
_NEW_DESTINATIONS = {"BGY", "VCE", "VIE", "STN", "FAO", "OPO"}
_START = date(2026, 10, 1)


@pytest.fixture(autouse=True)
def _default_origins(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_ORIGINS", raising=False)


# --- static batch untouched ------------------------------------------------------


def test_static_batches_are_unchanged():
    assert [(t.origin, t.destination) for t in FLIGHT_TARGETS] == [
        ("HAM", "PMI"), ("HAM", "BCN"), ("HAM", "BCN"), ("HAM", "FCO"),
    ]
    assert {t.destination for t in HOTEL_TARGETS} == {"PMI", "BCN", "FCO"}
    assert len(HOTEL_TARGETS) == 4


# --- the pool --------------------------------------------------------------------


def test_pool_contains_the_requested_destinations():
    destinations = {t.destination for t in ROTATION_FLIGHT_TEMPLATES}
    assert _NEW_DESTINATIONS | {"BCN", "PMI", "FCO"} <= destinations


def test_pool_extends_the_static_targets_without_duplicates():
    assert ROTATION_FLIGHT_TEMPLATES[: len(FLIGHT_TARGETS)] == FLIGHT_TARGETS
    assert len(ROTATION_FLIGHT_TEMPLATES) == len(set(ROTATION_FLIGHT_TEMPLATES))
    assert len(ROTATION_HOTEL_TARGETS) == len(set(ROTATION_HOTEL_TARGETS))


@pytest.mark.parametrize("template", ROTATION_FLIGHT_TEMPLATES, ids=lambda t: f"{t.destination}-{t.departure_date}")
def test_every_template_has_a_paired_hotel_target(template):
    hotel = _hotel_target_for_flight_template(template, ROTATION_HOTEL_TARGETS)
    assert hotel is not None
    assert hotel.destination in _DESTINATION_QUERY  # else the hotel search silently returns nothing


@pytest.mark.parametrize("template", ROTATION_FLIGHT_TEMPLATES[len(FLIGHT_TARGETS):], ids=lambda t: t.destination)
def test_new_templates_are_upcoming_short_getaways(template):
    assert template.trip_type is TripType.ROUND_TRIP
    assert template.departure_date > _START
    assert template.departure_date.weekday() in (3, 4)  # Thu or Fri
    assert template.return_date.weekday() == 6  # Sunday
    assert 2 <= (template.return_date - template.departure_date).days <= 3


@pytest.mark.parametrize("code", sorted(_NEW_DESTINATIONS | {"PMI", "BCN", "FCO"}))
def test_every_pool_destination_has_image_context_and_hotel_query(code):
    assert destination_image_url(code) != FALLBACK_IMAGE_URL
    assert destination_context(code) != _FALLBACK_CONTEXT
    assert code in _DESTINATION_QUERY


def test_new_destination_images_are_all_distinct():
    urls = [destination_image_url(c) for c in sorted(_NEW_DESTINATIONS)]
    assert len(set(urls)) == len(urls)


# --- origin rotation -------------------------------------------------------------


def test_rotation_is_deterministic_per_date():
    assert featured_rotation_of_the_day(today=_START, origins=_HUBS) == featured_rotation_of_the_day(
        today=_START, origins=_HUBS
    )


def test_rotation_visits_every_hub_and_every_pool_destination():
    n, m = len(ROTATION_FLIGHT_TEMPLATES), len(_HUBS)
    seen = [
        featured_rotation_of_the_day(today=_START + timedelta(days=d), origins=_HUBS) for d in range(n * m)
    ]

    assert {origin for _, origin in seen} == set(_HUBS)
    assert {t.destination for t, _ in seen} == {t.destination for t in ROTATION_FLIGHT_TEMPLATES}


def test_a_destination_is_visited_from_several_origins_not_locked_to_one():
    """Regression for lock-step rotation: with day % len(origins) and
    day % len(templates) sharing a factor, each destination would only
    ever see one origin."""
    n, m = len(ROTATION_FLIGHT_TEMPLATES), len(_HUBS)
    origins_for_venice = {
        origin
        for d in range(n * m)
        for t, origin in [featured_rotation_of_the_day(today=_START + timedelta(days=d), origins=_HUBS)]
        if t.destination == "VCE"
    }
    assert len(origins_for_venice) >= 4


def test_rotation_never_picks_a_cell_that_rebuilds_a_static_target():
    for d in range(len(ROTATION_FLIGHT_TEMPLATES) * len(_HUBS) * 2):
        template, origin = featured_rotation_of_the_day(today=_START + timedelta(days=d), origins=_HUBS)
        (rebuilt,) = build_rotating_flight_targets(today=_START, origins=[origin], base_targets=[template])
        assert rebuilt not in FLIGHT_TARGETS, (d, rebuilt)


def test_rotation_uses_configured_origins_by_default(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_ORIGINS", "MUC,DUS")
    origins = {featured_rotation_of_the_day(today=_START + timedelta(days=d))[1] for d in range(60)}
    assert origins == {"MUC", "DUS"}


def test_rotation_rejects_empty_inputs():
    with pytest.raises(ValueError):
        featured_rotation_of_the_day(templates=[], today=_START)
    with pytest.raises(ValueError):
        featured_rotation_of_the_day(origins=[], today=_START)


def test_fully_excluded_grid_falls_back_to_the_days_raw_cell():
    only = FlightComparisonGroup("HAM", "PMI", date(2026, 10, 2), date(2026, 10, 7), TripType.ROUND_TRIP, "EUR")
    template, origin = featured_rotation_of_the_day(templates=[only], origins=["HAM"], today=_START, exclude=[only])
    assert (template, origin) == (only, "HAM")


# --- credit check ----------------------------------------------------------------


def test_calls_per_run_do_not_grow_on_any_day_of_the_cycle():
    """Static targets + exactly 1 rotating flight + at most 1 hotel - the
    same 6 worst-case live calls as before the pool was widened."""
    for d in range(len(ROTATION_FLIGHT_TEMPLATES) * len(_HUBS) * 2):
        today = _START + timedelta(days=d)
        template, origin = featured_rotation_of_the_day(today=today, origins=_HUBS)
        rotating = build_rotating_flight_targets(today=today, origins=[origin], base_targets=[template])
        hotel = _hotel_target_for_flight_template(template, ROTATION_HOTEL_TARGETS)

        flights = set(FLIGHT_TARGETS) | set(rotating)
        assert len(rotating) == 1
        assert len(flights) == len(FLIGHT_TARGETS) + 1  # never collapses onto a static target
        assert len(flights) + (1 if hotel else 0) <= len(FLIGHT_TARGETS) + 2
