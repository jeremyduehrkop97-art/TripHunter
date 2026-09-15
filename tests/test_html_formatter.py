from __future__ import annotations

from datetime import date

import pytest

from trip_hunter.alerts.html_formatter import format_deal_html, format_newsletter_html
from trip_hunter.models import (
    AccommodationOffer,
    BaselineSource,
    Deal,
    DealScore,
    DealType,
    FlightOffer,
    HistoricalBaseline,
    HistoricalPosition,
    PriceStatistics,
)

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def _no_affiliate_tag(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_AFFILIATE_TAG", raising=False)


def _flight(
    price: float = 65.0,
    *,
    stops: int = 0,
    booking_link: str | None = "https://example.com/book/flight",
    airline: str = "Eurowings",
) -> FlightOffer:
    return FlightOffer(
        origin="HAM", destination="PMI", departure_date=_FRI, return_date=_SUN,
        price=price, currency="EUR", airline=airline, stops=stops, provider="test",
        booking_link=booking_link,
    )


def _accommodation(
    total_price: float = 90.0,
    *,
    rating: float | None = 4.3,
    booking_link: str | None = "https://example.com/book/hotel",
    name: str = "Hostal Born Boutique",
) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI", check_in=_FRI, check_out=_SUN, total_price=total_price,
        currency="EUR", name=name, rating=rating, provider="test", booking_link=booking_link,
    )


def _deal(
    *,
    flight: FlightOffer | None = None,
    accommodation: AccommodationOffer | None = None,
    deal_type: DealType = DealType.COMBINED_TRIP_DROP,
    savings_absolute: float | None = 135.0,
    savings_percentage: float | None = 0.466,
    score: int | None = 76,
    baseline_source: BaselineSource = BaselineSource.OWN_HISTORICAL_BASELINE,
    historical_baseline: HistoricalBaseline | None = None,
) -> Deal:
    return Deal(
        deal_type=deal_type,
        flight=flight or _flight(),
        accommodation=accommodation,
        expected_flight_price=140.0,
        expected_accommodation_price=150.0 if accommodation else None,
        score=DealScore(total=score, breakdown={}) if score is not None else None,
        savings_absolute=savings_absolute,
        savings_percentage=savings_percentage,
        baseline_source=baseline_source,
        historical_baseline=historical_baseline,
    )


# --- format_deal_html --------------------------------------------------------


def test_card_contains_route_dates_and_nights():
    output = format_deal_html(_deal(accommodation=_accommodation()))

    assert "HAM" in output and "PMI" in output
    assert "02.10.2026" in output
    assert "04.10.2026" in output
    assert "2 Nächte" in output


def test_card_uses_only_inline_styles_no_style_block():
    """Email clients strip <style> blocks - every rule must be inline."""
    output = format_deal_html(_deal())

    assert "<style" not in output
    assert 'style="' in output


def test_combined_trip_drop_gets_the_flagship_badge_color():
    output = format_deal_html(_deal(deal_type=DealType.COMBINED_TRIP_DROP))

    assert "Top-Kombi-Deal" in output
    assert "#0a7d34" in output  # the flagship green


def test_other_deal_types_get_the_default_badge_color():
    output = format_deal_html(_deal(deal_type=DealType.HOTEL_DROP))

    assert "Günstiges Hotel" in output
    assert "#7b3fe4" in output


def test_score_badge_shown_when_score_present():
    output = format_deal_html(_deal(score=76))

    assert "Score: 76/100" in output


def test_score_badge_omitted_when_no_score():
    output = format_deal_html(_deal(score=None, savings_absolute=None, savings_percentage=None))

    assert "Score:" not in output


def test_flight_and_hotel_split_shown():
    output = format_deal_html(_deal(accommodation=_accommodation()))

    assert "65.00 EUR" in output
    assert "Eurowings" in output
    assert "Nonstop" in output
    assert "Hostal Born Boutique" in output
    assert "90.00 EUR" in output


def test_hotel_row_omitted_when_no_accommodation():
    output = format_deal_html(_deal(accommodation=None))

    assert "Hostal Born Boutique" not in output


def test_missing_savings_shown_honestly():
    output = format_deal_html(_deal(savings_absolute=None, savings_percentage=None, score=None))

    assert "Ersparnis nicht verfügbar" in output


def test_baseline_transparency_note_present():
    baseline = HistoricalBaseline(
        statistics=PriceStatistics(
            observation_count=5, minimum=120.0, maximum=160.0, mean=140.0, median=140.0,
            p25=130.0, p75=150.0, stdev=10.0,
        ),
        position=HistoricalPosition.BELOW_HISTORY,
        percent_diff_from_median=-53.6,
    )
    output = format_deal_html(
        _deal(baseline_source=BaselineSource.OWN_HISTORICAL_BASELINE, historical_baseline=baseline)
    )

    assert "eigene Preishistorie" in output
    assert "140.00 EUR" in output


# --- CTA / booking links / affiliate -----------------------------------------


def test_cta_buttons_link_to_booking_urls():
    output = format_deal_html(_deal(accommodation=_accommodation()))

    assert 'href="https://example.com/book/flight"' in output
    assert 'href="https://example.com/book/hotel"' in output
    assert "Flug buchen" in output
    assert "Hotel buchen" in output


def test_missing_booking_link_shown_honestly_not_omitted():
    output = format_deal_html(_deal(flight=_flight(booking_link=None)))

    assert "kein Direktlink verfügbar" in output


def test_booking_links_are_affiliate_decorated_when_configured(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_AFFILIATE_TAG", "triphunter123")

    output = format_deal_html(_deal(accommodation=_accommodation()))

    assert 'href="https://example.com/book/flight?tp_aff=triphunter123"' in output
    assert 'href="https://example.com/book/hotel?tp_aff=triphunter123"' in output


# --- HTML escaping -------------------------------------------------------------


def test_hotel_name_is_html_escaped():
    dangerous_name = 'Hotel <script>alert("x")</script> & Spa'
    output = format_deal_html(_deal(accommodation=_accommodation(name=dangerous_name)))

    assert "<script>" not in output
    assert "&lt;script&gt;" in output


def test_airline_is_html_escaped():
    output = format_deal_html(_deal(flight=_flight(airline="Air & <Test>")))

    assert "<Test>" not in output
    assert "&lt;Test&gt;" in output


# --- format_newsletter_html ---------------------------------------------------


def test_full_document_has_doctype_and_meta_viewport():
    output = format_newsletter_html([_deal()])

    assert output.startswith("<!doctype html>")
    assert 'name="viewport"' in output
    assert "<title>" in output


def test_empty_deal_list_produces_honest_message():
    output = format_newsletter_html([])

    assert "Keine passenden Deals" in output


def test_newsletter_includes_all_deal_cards():
    deal_a = _deal(flight=_flight(price=65.0))
    deal_b = _deal(flight=_flight(price=55.0, booking_link="https://example.com/book/flight2"))

    output = format_newsletter_html([deal_a, deal_b])

    assert output.count("Top-Kombi-Deal") == 2
    assert "https://example.com/book/flight2" in output


def test_title_is_escaped_and_used_in_head_and_body():
    output = format_newsletter_html([_deal()], title="Deals & <Dinge>")

    assert "<title>Deals &amp; &lt;Dinge&gt;</title>" in output
    assert "Deals &amp; &lt;Dinge&gt;" in output.split("<body", 1)[1]
