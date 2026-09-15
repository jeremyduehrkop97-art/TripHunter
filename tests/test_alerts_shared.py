"""Direct tests for alerts/_shared.py - the presentation helpers all three
formatters (Markdown, HTML, instant-alert) depend on. Pinning these down
here means a future channel can rely on them without re-deriving the same
assertions.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from trip_hunter.alerts._shared import (
    baseline_source_note,
    deal_type_label,
    fmt_date,
    nights_label,
    trip_nights,
)
from trip_hunter.models import (
    BaselineSource,
    Deal,
    DealScore,
    DealType,
    FlightOffer,
    HistoricalBaseline,
    HistoricalPosition,
    PriceInsight,
    PriceStatistics,
)


def _flight(departure_date=date(2026, 10, 2), return_date=date(2026, 10, 4)) -> FlightOffer:
    return FlightOffer(
        origin="HAM", destination="PMI", departure_date=departure_date, return_date=return_date,
        price=65.0, currency="EUR", airline="Eurowings", stops=0, provider="test",
    )


def _deal(**overrides) -> Deal:
    defaults = dict(
        deal_type=DealType.FLIGHT_DROP,
        flight=_flight(),
        accommodation=None,
        expected_flight_price=140.0,
        expected_accommodation_price=None,
        score=DealScore(total=70, breakdown={}),
        savings_absolute=75.0,
        savings_percentage=0.536,
        baseline_source=BaselineSource.OWN_HISTORICAL_BASELINE,
        historical_baseline=None,
        price_insight=None,
    )
    defaults.update(overrides)
    return Deal(**defaults)


def test_deal_type_label_known_types():
    assert deal_type_label(DealType.COMBINED_TRIP_DROP) == "Top-Kombi-Deal"
    assert deal_type_label(DealType.FLIGHT_DROP) == "Günstiger Flug"
    assert deal_type_label(DealType.HOTEL_DROP) == "Günstiges Hotel"
    assert deal_type_label(DealType.ERROR_FARE) == "Möglicher Error Fare"
    assert deal_type_label(DealType.UNUSUALLY_LOW) == "Auffällig günstig"


def test_nights_label_singular_and_plural():
    assert nights_label(1) == "1 Nacht"
    assert nights_label(0) == "0 Nächte"
    assert nights_label(2) == "2 Nächte"


def test_trip_nights_uses_flight_dates():
    deal = _deal(flight=_flight(date(2026, 10, 2), date(2026, 10, 7)))
    assert trip_nights(deal) == 5


def test_fmt_date_format():
    assert fmt_date(date(2026, 10, 2)) == "02.10.2026"


def test_baseline_source_note_own_historical_with_stats():
    baseline = HistoricalBaseline(
        statistics=PriceStatistics(
            observation_count=5, minimum=120.0, maximum=160.0, mean=140.0, median=140.0,
            p25=130.0, p75=150.0, stdev=10.0,
        ),
        position=HistoricalPosition.BELOW_HISTORY,
        percent_diff_from_median=-53.6,
    )
    deal = _deal(baseline_source=BaselineSource.OWN_HISTORICAL_BASELINE, historical_baseline=baseline)

    note = baseline_source_note(deal)

    assert "eigene Preishistorie" in note
    assert "140.00 EUR" in note


def test_baseline_source_note_own_historical_without_stats():
    deal = _deal(baseline_source=BaselineSource.OWN_HISTORICAL_BASELINE, historical_baseline=None)

    note = baseline_source_note(deal)

    assert note == "Vergleichswert: eigene Preishistorie"


def test_baseline_source_note_provider_price_insight():
    insight = PriceInsight(
        provider_lowest_price=89.0, typical_price_low=160.0, typical_price_high=220.0,
        price_level="low", source="google_flights",
    )
    deal = _deal(baseline_source=BaselineSource.PROVIDER_PRICE_INSIGHT, price_insight=insight)

    note = baseline_source_note(deal)

    assert "google_flights" in note
    assert "keine eigene Historie" in note


def test_baseline_source_note_provider_price_insight_without_insight_object():
    deal = _deal(baseline_source=BaselineSource.PROVIDER_PRICE_INSIGHT, price_insight=None)

    note = baseline_source_note(deal)

    assert "Provider" in note


def test_baseline_source_note_no_baseline():
    deal = _deal(baseline_source=BaselineSource.NO_BASELINE)

    assert baseline_source_note(deal) == "Vergleichswert: nicht verfügbar"
