"""Tests for build_newsletter.py. No real HTTP calls anywhere in this file:
`build_and_export` is exercised with hand-built Deal lists (no
provider/network dependency at all), `_collect_real_deals` with a
DealEngine wired to fake providers, and the one test that goes through
`run()` monkeypatches SerpApiClient.search_flights /
SerpApiHotelsClient.search_hotels.
"""

from __future__ import annotations

from datetime import date

import pytest

from trip_hunter.build_newsletter import (
    DEFAULT_INSTANT_ALERT_CRITERIA,
    DEFAULT_NEWSLETTER_CRITERIA,
    BuildResult,
    _build_parser,
    _collect_real_deals,
    _parse_args,
    build_and_export,
    run,
)
from trip_hunter.engine.deal_engine import DealEngine
from trip_hunter.engine.deal_filters import DealFilterCriteria
from trip_hunter.models import (
    AccommodationOffer,
    Deal,
    DealScore,
    DealType,
    FlightComparisonGroup,
    FlightOffer,
    TripType,
)
from trip_hunter.providers.accommodation_provider import AccommodationProvider
from trip_hunter.providers.flight_provider import FlightProvider

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def _no_affiliate_tag(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_AFFILIATE_TAG", raising=False)


def _flight(price: float = 65.0, *, origin: str = "HAM", destination: str = "PMI") -> FlightOffer:
    return FlightOffer(
        origin=origin, destination=destination, departure_date=_FRI, return_date=_SUN,
        price=price, currency="EUR", airline="Eurowings", stops=0, provider="test",
        booking_link="https://example.com/book/flight",
    )


def _accommodation(total_price: float = 90.0) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI", check_in=_FRI, check_out=_SUN, total_price=total_price,
        currency="EUR", name="Hostal Born Boutique", rating=4.3, provider="test",
        booking_link="https://example.com/book/hotel",
    )


def _deal(
    *,
    flight: FlightOffer | None = None,
    accommodation: AccommodationOffer | None = None,
    deal_type: DealType = DealType.COMBINED_TRIP_DROP,
    total_price: float | None = None,
    score: int | None = 76,
) -> Deal:
    flight = flight or _flight()
    if total_price is not None:
        # convenience: override just the total via flight price when no
        # accommodation, otherwise callers should size flight+accommodation directly
        flight = _flight(total_price)
    return Deal(
        deal_type=deal_type,
        flight=flight,
        accommodation=accommodation,
        expected_flight_price=140.0,
        expected_accommodation_price=150.0 if accommodation else None,
        score=DealScore(total=score, breakdown={}) if score is not None else None,
        savings_absolute=135.0,
        savings_percentage=0.466,
    )


# --- build_and_export: pure, no network at all ---------------------------------


def test_writes_all_three_export_files(tmp_path):
    result = build_and_export([_deal(accommodation=_accommodation())], output_dir=tmp_path)

    assert result.newsletter_html_path.is_file()
    assert result.newsletter_md_path.is_file()
    assert result.instant_alerts_path.is_file()


def test_creates_output_dir_if_missing(tmp_path):
    output_dir = tmp_path / "nested" / "output"
    assert not output_dir.exists()

    build_and_export([_deal()], output_dir=output_dir)

    assert output_dir.is_dir()


def test_html_export_contains_expected_deal_content(tmp_path):
    result = build_and_export([_deal(accommodation=_accommodation())], output_dir=tmp_path)

    html = result.newsletter_html_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert "Top-Kombi-Deal" in html
    assert "Hostal Born Boutique" in html


def test_markdown_export_contains_expected_deal_content(tmp_path):
    result = build_and_export([_deal(accommodation=_accommodation())], output_dir=tmp_path)

    markdown = result.newsletter_md_path.read_text(encoding="utf-8")
    assert "# Trip Hunter" in markdown
    assert "Top-Kombi-Deal" in markdown


def test_instant_alerts_export_contains_expected_content(tmp_path):
    result = build_and_export(
        [_deal(deal_type=DealType.COMBINED_TRIP_DROP, score=90)], output_dir=tmp_path
    )

    content = result.instant_alerts_path.read_text(encoding="utf-8")
    assert "🔥 COMBINED TRIP DROP" in content


def test_empty_deal_list_produces_honest_files_in_all_three_channels(tmp_path):
    result = build_and_export([], output_dir=tmp_path)

    assert "Keine passenden Deals" in result.newsletter_html_path.read_text(encoding="utf-8")
    assert "Keine passenden Deals" in result.newsletter_md_path.read_text(encoding="utf-8")
    assert "Keine passenden Instant-Alert-Deals" in result.instant_alerts_path.read_text(encoding="utf-8")
    assert result.newsletter_deal_count == 0
    assert result.instant_alert_count == 0


def test_result_counts_reflect_curation_not_raw_input(tmp_path):
    cheap_good_deal = _deal(deal_type=DealType.COMBINED_TRIP_DROP, score=90)
    expensive_flight = _flight(10_000.0)
    over_budget_deal = _deal(flight=expensive_flight, deal_type=DealType.FLIGHT_DROP, score=90)

    result = build_and_export(
        [cheap_good_deal, over_budget_deal],
        output_dir=tmp_path,
        newsletter_criteria=DealFilterCriteria(max_total_price=200.0, min_score=50),
        instant_alert_criteria=DealFilterCriteria(max_total_price=200.0, min_score=50),
    )

    assert result.newsletter_deal_count == 1
    assert result.instant_alert_count == 1


def test_newsletter_and_instant_alert_criteria_curate_independently(tmp_path):
    """A HOTEL_DROP passes the (broader) default newsletter criteria but
    not the (narrower) default instant-alert criteria, which only allows
    ERROR_FARE/FLIGHT_DROP/COMBINED_TRIP_DROP."""
    hotel_drop_deal = _deal(deal_type=DealType.HOTEL_DROP, score=80)

    result = build_and_export([hotel_drop_deal], output_dir=tmp_path)

    assert result.newsletter_deal_count == 1
    assert result.instant_alert_count == 0


def test_custom_newsletter_title_used_in_both_html_and_markdown(tmp_path):
    result = build_and_export([_deal()], output_dir=tmp_path, newsletter_title="Custom Title")

    assert "Custom Title" in result.newsletter_html_path.read_text(encoding="utf-8")
    assert result.newsletter_md_path.read_text(encoding="utf-8").startswith("# Custom Title")


def test_default_criteria_are_defined_and_differ():
    """The newsletter uses one flat budget ceiling; the instant-alert
    criteria deliberately does NOT (duration-aware cap instead - see
    DEFAULT_INSTANT_ALERT_CRITERIA's own comment in build_newsletter.py)."""
    assert DEFAULT_NEWSLETTER_CRITERIA.max_total_price is not None
    assert DEFAULT_INSTANT_ALERT_CRITERIA.max_total_price is None
    assert DEFAULT_INSTANT_ALERT_CRITERIA.weekend_max_total is not None
    assert DEFAULT_INSTANT_ALERT_CRITERIA.max_price_per_night is not None
    assert DEFAULT_INSTANT_ALERT_CRITERIA.allowed_deal_types is not None
    assert DEFAULT_NEWSLETTER_CRITERIA.allowed_deal_types is None


def test_build_result_is_a_frozen_dataclass_instance(tmp_path):
    result = build_and_export([], output_dir=tmp_path)
    assert isinstance(result, BuildResult)


# --- _collect_real_deals: DealEngine orchestration, fakes only ----------------


class _FakeFlightProvider(FlightProvider):
    def __init__(self, offers_by_route: dict[tuple[str, str], list[FlightOffer]]):
        self._offers_by_route = offers_by_route
        self.search_calls = 0

    def search_flights(self, origin, destination, earliest_departure, latest_departure, return_date=None):
        self.search_calls += 1
        return self._offers_by_route.get((origin, destination), [])

    def get_typical_price(self, origin, destination, month):
        return 140.0

    def get_price_insight(self, origin, destination, departure_date, return_date):
        return None


class _FakeAccommodationProvider(AccommodationProvider):
    def search_accommodations(self, destination, check_in, check_out):
        return []

    def get_typical_total_price(self, destination, nights, month):
        return None


def test_collect_real_deals_runs_once_per_target_and_aggregates():
    targets = [
        FlightComparisonGroup(
            origin="HAM", destination="PMI", departure_date=_FRI, return_date=_SUN,
            trip_type=TripType.ROUND_TRIP, currency="EUR",
        ),
        FlightComparisonGroup(
            origin="HAM", destination="BCN", departure_date=_FRI, return_date=_SUN,
            trip_type=TripType.ROUND_TRIP, currency="EUR",
        ),
    ]
    flight_provider = _FakeFlightProvider(
        {
            ("HAM", "PMI"): [_flight(65.0, origin="HAM", destination="PMI")],
            ("HAM", "BCN"): [_flight(120.0, origin="HAM", destination="BCN")],
        }
    )
    engine = DealEngine(flight_provider=flight_provider, accommodation_provider=_FakeAccommodationProvider())

    deals = _collect_real_deals(engine, targets)

    assert flight_provider.search_calls == 2
    destinations = {deal.flight.destination for deal in deals}
    assert "PMI" in destinations


def test_collect_real_deals_with_no_targets_makes_no_calls():
    flight_provider = _FakeFlightProvider({})
    engine = DealEngine(flight_provider=flight_provider, accommodation_provider=_FakeAccommodationProvider())

    deals = _collect_real_deals(engine, [])

    assert deals == []
    assert flight_provider.search_calls == 0


# --- CLI parsing --------------------------------------------------------------


def test_output_dir_defaults_to_output():
    args = _parse_args([])
    assert args.output_dir == "output"


def test_output_dir_can_be_overridden():
    args = _parse_args(["--output-dir", "custom"])
    assert args.output_dir == "custom"


def test_parser_has_a_prog_name():
    parser = _build_parser()
    assert "build_newsletter" in parser.prog


# --- run(): CLI wiring, no real network -----------------------------------------


def test_run_prints_friendly_message_when_serpapi_key_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRIP_HUNTER_SERPAPI_KEY", raising=False)
    monkeypatch.delenv("VACATION_HUNTER_SERPAPI_KEY", raising=False)

    result = run([])

    assert result is None
    assert "Configuration missing" in capsys.readouterr().out


def test_run_end_to_end_with_monkeypatched_clients_writes_real_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "fake-test-key-not-real")

    import trip_hunter.providers.serpapi_client as serpapi_client_module
    import trip_hunter.providers.serpapi_hotels_client as serpapi_hotels_client_module

    call_counts = {"flights": 0, "hotels": 0}

    def fake_search_flights(self, **kwargs):
        call_counts["flights"] += 1
        # departure/arrival times must echo back the ACTUALLY requested
        # outbound_date, exactly like a real response would - a hardcoded
        # date here would desync SerpApiGoogleFlightsProvider's internal
        # cache key between search_flights() and the BASELINE_UNAVAILABLE
        # fallback's get_price_insight() call (which is keyed off the
        # normalized offer's own departure_date), causing a second,
        # unintended live call. See test_serpapi_flight_provider.py's
        # test_deal_engine_run_makes_only_one_live_call_end_to_end for the
        # original regression this mirrors.
        outbound_date = kwargs["outbound_date"]
        return {
            "best_flights": [
                {
                    "price": 184,
                    "flights": [
                        {
                            "departure_airport": {"id": kwargs["origin"], "time": f"{outbound_date} 21:50"},
                            "arrival_airport": {"id": kwargs["destination"], "time": f"{outbound_date} 23:20"},
                            "airline": "Vueling",
                        }
                    ],
                }
            ],
            "other_flights": [],
        }

    def fake_search_hotels(self, **kwargs):
        call_counts["hotels"] += 1
        return {"properties": []}

    monkeypatch.setattr(serpapi_client_module.SerpApiClient, "search_flights", fake_search_flights)
    monkeypatch.setattr(
        serpapi_hotels_client_module.SerpApiHotelsClient, "search_hotels", fake_search_hotels
    )

    result = run(["--output-dir", str(tmp_path / "out")])

    from trip_hunter.sampling_targets import FLIGHT_TARGETS

    assert call_counts["flights"] == len(FLIGHT_TARGETS)
    assert result is not None
    assert result.newsletter_html_path.is_file()
    assert result.newsletter_md_path.is_file()
    assert result.instant_alerts_path.is_file()

    captured = capsys.readouterr().out
    assert "TRIP HUNTER — NEWSLETTER BUILDER" in captured
