from __future__ import annotations

from datetime import date

from trip_hunter.alerts.deal_formatter import format_deal, format_newsletter
from trip_hunter.models import (
    AccommodationOffer,
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

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 4)


def _flight(
    price: float = 65.0,
    *,
    stops: int = 0,
    booking_link: str | None = "https://example.com/book/flight",
    departure_date: date = _FRI,
    return_date: date = _SUN,
) -> FlightOffer:
    return FlightOffer(
        origin="HAM",
        destination="PMI",
        departure_date=departure_date,
        return_date=return_date,
        price=price,
        currency="EUR",
        airline="Eurowings",
        stops=stops,
        provider="test",
        booking_link=booking_link,
    )


def _accommodation(
    total_price: float = 90.0,
    *,
    rating: float | None = 4.3,
    booking_link: str | None = "https://example.com/book/hotel",
) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI",
        check_in=_FRI,
        check_out=_SUN,
        total_price=total_price,
        currency="EUR",
        name="Hostal Born Boutique",
        rating=rating,
        provider="test",
        booking_link=booking_link,
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
    price_insight: PriceInsight | None = None,
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
        price_insight=price_insight,
    )


_BASELINE = HistoricalBaseline(
    statistics=PriceStatistics(
        observation_count=5, minimum=120.0, maximum=160.0, mean=140.0, median=140.0, p25=130.0, p75=150.0, stdev=10.0
    ),
    position=HistoricalPosition.BELOW_HISTORY,
    percent_diff_from_median=-53.6,
)


# --- format_deal: header / route -----------------------------------------


def test_header_contains_route_dates_and_nights():
    output = format_deal(_deal(accommodation=_accommodation()))

    assert "HAM → PMI" in output
    assert "02.10.2026" in output
    assert "04.10.2026" in output
    assert "2 Nächte" in output


def test_single_night_uses_singular():
    flight = _flight(departure_date=_FRI, return_date=date(2026, 10, 3))  # 1 night
    output = format_deal(_deal(flight=flight))

    assert "1 Nacht" in output
    assert "1 Nächte" not in output


def test_deal_type_label_is_customer_friendly():
    output = format_deal(_deal(deal_type=DealType.COMBINED_TRIP_DROP))

    assert "Top-Kombi-Deal" in output
    assert "COMBINED_TRIP_DROP" not in output


def test_unmapped_deal_type_falls_back_to_raw_value():
    """Defensive: every current DealType is mapped, but a formatter must
    never crash on a hypothetical future/unmapped one."""
    from trip_hunter.alerts import deal_formatter

    original = dict(deal_formatter._DEAL_TYPE_LABELS)
    del deal_formatter._DEAL_TYPE_LABELS[DealType.FLIGHT_DROP]
    try:
        output = format_deal(_deal(deal_type=DealType.FLIGHT_DROP))
        assert "FLIGHT_DROP" in output
    finally:
        deal_formatter._DEAL_TYPE_LABELS.clear()
        deal_formatter._DEAL_TYPE_LABELS.update(original)


# --- format_deal: savings --------------------------------------------------


def test_savings_are_shown_when_present():
    output = format_deal(_deal(savings_absolute=135.0, savings_percentage=0.466))

    assert "135 EUR" in output
    assert "47%" in output  # 0.466 rounds to 47% under :.0%


def test_missing_savings_says_so_instead_of_crashing():
    output = format_deal(_deal(savings_absolute=None, savings_percentage=None, score=None))

    assert "Ersparnis nicht verfügbar" in output


# --- format_deal: flight/hotel breakdown -----------------------------------


def test_flight_line_shows_price_airline_and_nonstop():
    output = format_deal(_deal(flight=_flight(stops=0)))

    assert "65.00 EUR" in output
    assert "Eurowings" in output
    assert "Nonstop" in output


def test_flight_line_shows_stop_count_when_not_direct():
    output = format_deal(_deal(flight=_flight(stops=1)))

    assert "1 Stopp(s)" in output
    assert "Nonstop" not in output


def test_hotel_line_shown_when_accommodation_present():
    output = format_deal(_deal(accommodation=_accommodation()))

    assert "Hostal Born Boutique" in output
    assert "90.00 EUR" in output
    assert "4.3" in output


def test_hotel_line_omitted_when_no_accommodation():
    output = format_deal(_deal(accommodation=None))

    assert "**Hotel:**" not in output


def test_hotel_rating_omitted_when_none():
    output = format_deal(_deal(accommodation=_accommodation(rating=None)))

    assert "Hostal Born Boutique" in output
    assert "★" not in output


# --- format_deal: booking links / CTA --------------------------------------


def test_booking_links_included_for_flight_and_hotel():
    output = format_deal(_deal(accommodation=_accommodation()))

    assert "[Flug buchen](https://example.com/book/flight)" in output
    assert "[Hotel buchen](https://example.com/book/hotel)" in output


def test_missing_flight_booking_link_says_so_instead_of_omitting_silently():
    output = format_deal(_deal(flight=_flight(booking_link=None)))

    assert "Flug: kein Direktlink verfügbar" in output


def test_missing_hotel_booking_link_says_so_instead_of_omitting_silently():
    output = format_deal(_deal(accommodation=_accommodation(booking_link=None)))

    assert "Hotel: kein Direktlink verfügbar" in output


def test_no_hotel_cta_line_when_no_accommodation_at_all():
    output = format_deal(_deal(accommodation=None))

    assert "Hotel:" not in output
    assert "Hotel buchen" not in output


# --- format_deal: baseline transparency -------------------------------------


def test_own_historical_baseline_with_stats_shows_median():
    output = format_deal(_deal(baseline_source=BaselineSource.OWN_HISTORICAL_BASELINE, historical_baseline=_BASELINE))

    assert "eigene Preishistorie" in output
    assert "140.00 EUR" in output


def test_own_historical_baseline_without_stats_still_labeled():
    output = format_deal(
        _deal(baseline_source=BaselineSource.OWN_HISTORICAL_BASELINE, historical_baseline=None)
    )

    assert "eigene Preishistorie" in output


def test_provider_price_insight_is_labeled_with_source():
    insight = PriceInsight(
        provider_lowest_price=89.0, typical_price_low=160.0, typical_price_high=220.0,
        price_level="low", source="google_flights",
    )
    output = format_deal(
        _deal(baseline_source=BaselineSource.PROVIDER_PRICE_INSIGHT, price_insight=insight)
    )

    assert "Preis-Einschätzung von google_flights" in output
    assert "keine eigene Historie" in output


def test_no_baseline_is_labeled_honestly():
    output = format_deal(_deal(baseline_source=BaselineSource.NO_BASELINE, score=None))

    assert "Vergleichswert: nicht verfügbar" in output


# --- format_newsletter -------------------------------------------------------


def test_empty_deal_list_produces_honest_no_deals_message():
    output = format_newsletter([])

    assert "Keine passenden Deals" in output


def test_single_deal_newsletter_uses_singular_count():
    output = format_newsletter([_deal()])

    assert "1 passender Deal gefunden" in output


def test_multiple_deals_newsletter_uses_plural_count_and_includes_both():
    deal_a = _deal(flight=_flight(price=65.0))
    deal_b = _deal(flight=_flight(price=55.0, booking_link="https://example.com/book/flight2"))

    output = format_newsletter([deal_a, deal_b])

    assert "2 passende Deals gefunden" in output
    assert output.count("## HAM → PMI") == 2
    assert "https://example.com/book/flight2" in output


def test_newsletter_title_is_customizable():
    output = format_newsletter([_deal()], title="Custom Title")

    assert output.startswith("# Custom Title")
