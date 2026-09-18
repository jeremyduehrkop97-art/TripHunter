from __future__ import annotations

from datetime import date

import pytest

from trip_hunter.alerts.instant_alert_formatter import format_instant_alert, format_instant_alerts
from trip_hunter.models import AccommodationOffer, Deal, DealScore, DealType, FlightOffer

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def _no_affiliate_tag(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_AFFILIATE_TAG", raising=False)


def _flight(
    price: float = 79.0,
    *,
    booking_link: str | None = "https://example.com/book/flight",
) -> FlightOffer:
    return FlightOffer(
        origin="HAM", destination="PMI", departure_date=_FRI, return_date=_SUN,
        price=price, currency="EUR", airline="Eurowings", stops=0, provider="test",
        booking_link=booking_link,
    )


def _accommodation(
    total_price: float = 90.0, *, booking_link: str | None = "https://example.com/book/hotel"
) -> AccommodationOffer:
    return AccommodationOffer(
        destination="PMI", check_in=_FRI, check_out=_SUN, total_price=total_price,
        currency="EUR", name="Hostal Born Boutique", rating=4.3, provider="test",
        booking_link=booking_link,
    )


def _deal(
    *,
    flight: FlightOffer | None = None,
    accommodation: AccommodationOffer | None = None,
    deal_type: DealType = DealType.FLIGHT_DROP,
    savings_absolute: float | None = 61.0,
    savings_percentage: float | None = 0.436,
    score: int | None = 70,
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
    )


# --- format_instant_alert ------------------------------------------------------


def test_matches_the_product_brief_example_shape():
    output = format_instant_alert(_deal(flight=_flight(79.0), savings_percentage=0.436))

    assert output.startswith("🚨 FLIGHT DROP: HAM → PMI für 79.00 EUR")


def test_combined_trip_drop_uses_fire_emoji():
    output = format_instant_alert(_deal(deal_type=DealType.COMBINED_TRIP_DROP))

    assert output.startswith("🔥 COMBINED TRIP DROP")


def test_hotel_drop_uses_hotel_emoji():
    output = format_instant_alert(_deal(deal_type=DealType.HOTEL_DROP))

    assert output.startswith("🏨 HOTEL DROP")


def test_unusually_low_uses_lightbulb_emoji():
    output = format_instant_alert(_deal(deal_type=DealType.UNUSUALLY_LOW))

    assert output.startswith("💡 UNUSUALLY LOW")


def test_dates_and_nights_included():
    output = format_instant_alert(_deal())

    assert "02.10.2026" in output
    assert "04.10.2026" in output
    assert "2 Nächte" in output


def test_savings_percentage_shown_as_negative():
    output = format_instant_alert(_deal(savings_percentage=0.436))

    assert "(-44%)" in output


def test_negative_overall_savings_percentage_does_not_double_negate():
    """Regression test: a Deal can earn its deal_type from flight-level
    savings alone yet end up with a NEGATIVE overall savings_percentage
    once the hotel side is combined (trip_combiner.py) - i.e. genuinely
    priced above the baseline. Found via a real end-to-end run: a cheap
    flight (FLIGHT_DROP) combined with an above-baseline real hotel price
    produced savings_percentage=-0.0548, which the old "(-{:.0%})" format
    rendered as the broken "(--5%)"."""
    output = format_instant_alert(_deal(savings_percentage=-0.0548))

    assert "(--5%)" not in output
    assert "(+5%)" in output


def test_missing_savings_percentage_omits_suffix_without_crashing():
    output = format_instant_alert(_deal(savings_absolute=None, savings_percentage=None, score=None))

    assert "(-" not in output


def test_hotel_line_included_when_accommodation_present():
    output = format_instant_alert(_deal(accommodation=_accommodation()))

    assert "🏨 Hostal Born Boutique" in output
    assert "90.00 EUR" in output
    assert "💰 Gesamt:" in output


def test_hotel_line_and_total_omitted_when_no_accommodation():
    output = format_instant_alert(_deal(accommodation=None))

    assert "🏨" not in output
    assert "💰" not in output


def test_message_stays_compact():
    """"Extrem kompakt" - a flight-only deal should fit in a handful of
    short lines, not a full breakdown."""
    output = format_instant_alert(_deal())

    assert len(output.splitlines()) <= 4


# --- links / affiliate -----------------------------------------------------


def test_booking_links_included_with_pointer_emoji():
    output = format_instant_alert(_deal(accommodation=_accommodation()))

    assert "👉 https://example.com/book/flight" in output
    assert "👉 https://example.com/book/hotel" in output


def test_missing_booking_link_is_omitted_not_mentioned():
    """Unlike the newsletter/HTML channels, a compact push simply skips a
    missing link rather than spending a line saying so."""
    output = format_instant_alert(_deal(flight=_flight(booking_link=None), accommodation=None))

    assert "kein Direktlink" not in output
    assert "👉" not in output


def test_booking_links_are_affiliate_decorated_when_configured(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_AFFILIATE_TAG", "triphunter123")

    output = format_instant_alert(_deal(accommodation=_accommodation()))

    assert "tp_aff=triphunter123" in output
    assert "👉 https://example.com/book/flight?tp_aff=triphunter123" in output


# --- format_instant_alerts (batch, still one message per deal) ----------------


def test_batch_returns_one_message_per_deal_as_separate_strings():
    deal_a = _deal(flight=_flight(79.0))
    deal_b = _deal(flight=_flight(55.0, booking_link="https://example.com/book/flight2"), deal_type=DealType.COMBINED_TRIP_DROP)

    messages = format_instant_alerts([deal_a, deal_b])

    assert len(messages) == 2
    assert messages[0].startswith("🚨")
    assert messages[1].startswith("🔥")
    assert "https://example.com/book/flight2" in messages[1]


def test_batch_preserves_order():
    deals = [_deal(flight=_flight(p)) for p in (79.0, 55.0, 99.0)]

    messages = format_instant_alerts(deals)

    assert "79.00 EUR" in messages[0]
    assert "55.00 EUR" in messages[1]
    assert "99.00 EUR" in messages[2]


def test_empty_deal_list_returns_empty_list():
    assert format_instant_alerts([]) == []
