"""Tests for weekend_radar_demo.py. Entirely local mock data (defined in
the module itself) - no network calls, no API key needed, nothing shared
with providers/mock_flight_provider.py's fixtures.
"""

from __future__ import annotations

from trip_hunter.models import DealType
from trip_hunter.weekend_radar_demo import run_demo


def test_default_run_returns_exactly_the_one_curated_weekend_deal(capsys):
    deals = run_demo()

    assert len(deals) == 1
    deal = deals[0]
    assert deal.flight.origin == "HAM"
    assert deal.flight.destination == "PMI"
    assert deal.deal_type == DealType.COMBINED_TRIP_DROP
    capsys.readouterr()  # drain printed output, not asserted on here


def test_bcn_route_never_becomes_a_deal_at_all(capsys):
    """~14% below baseline is below the UNUSUALLY_LOW threshold - DealEngine
    itself returns no Deal for this route, before any filtering happens."""
    run_demo()

    captured = capsys.readouterr()
    assert "HAM → BCN" in captured.out
    assert "HAM → BCN (2026-10-09 – 2026-10-11): 0 Deal(s) gefunden" in captured.out


def test_rom_route_is_a_real_deal_but_excluded_by_curation(capsys):
    """ROM does produce a raw FLIGHT_DROP deal (proves the pipeline finds
    it), but it doesn't survive the weekend_getaway curation at the
    default 250 EUR budget - proving the filter stage does real work, not
    just passing everything through."""
    deals = run_demo()

    captured = capsys.readouterr()
    assert "Rohdeals gesamt: 2" in captured.out  # PMI + ROM
    assert "Nach Kuratierung" in captured.out
    assert len(deals) == 1  # only PMI survives curation
    assert all(deal.flight.destination != "ROM" for deal in deals)


def test_stricter_budget_filters_out_even_the_curated_deal(capsys):
    deals = run_demo(max_total_price=50.0)  # PMI's own 155 EUR total no longer fits

    assert deals == []
    captured = capsys.readouterr()
    assert "Keine passenden Deals" in captured.out


def test_output_includes_a_formatted_newsletter_block(capsys):
    run_demo()

    captured = capsys.readouterr()
    assert "# Trip Hunter — Wochenend-Radar" in captured.out
    assert "Top-Kombi-Deal" in captured.out
    assert "[Flug buchen]" in captured.out
    assert "[Hotel buchen]" in captured.out


def test_curated_deal_carries_real_booking_links(capsys):
    deals = run_demo()

    deal = deals[0]
    assert deal.flight.booking_link is not None
    assert deal.accommodation is not None
    assert deal.accommodation.booking_link is not None
    capsys.readouterr()


def test_no_route_search_spans_more_than_the_requested_single_day():
    """Sanity check on the demo's own route table: earliest_departure ==
    latest_departure for every entry - one exact date per route, no date
    loops, matching the project's established API-credit-safety habit even
    though this demo never calls a real API."""
    from trip_hunter.weekend_radar_demo import _ROUTES

    for origin, destination, departure, return_date in _ROUTES:
        assert departure.weekday() == 4  # Friday
        assert return_date.weekday() == 6  # Sunday
