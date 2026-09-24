from __future__ import annotations

from datetime import date

import pytest

from trip_hunter.alerts.instant_alert_formatter import (
    format_instant_alert,
    format_instant_alerts,
    format_teaser_alert,
)
from trip_hunter.models import AccommodationOffer, Deal, DealScore, DealType, FlightOffer

_FRI = date(2026, 10, 2)
_SUN = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def _no_affiliate_tag(monkeypatch):
    monkeypatch.delenv("TRIP_HUNTER_AFFILIATE_TAG", raising=False)


def _flight(
    price: float = 79.0,
    *,
    origin: str = "HAM",
    booking_link: str | None = "https://example.com/book/flight",
) -> FlightOffer:
    return FlightOffer(
        origin=origin, destination="PMI", departure_date=_FRI, return_date=_SUN,
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


def test_header_names_the_cities_with_flag_and_badge_shows_the_saving():
    lines = format_instant_alert(_deal(flight=_flight(79.0), savings_percentage=0.436)).splitlines()

    assert lines[0] == "🇪🇸 <b>Hamburg nach Palma de Mallorca</b>"
    assert lines[1] == "💥 <b>-44% günstiger als sonst</b>"


def test_price_and_iata_codes_are_not_in_the_header():
    header = format_instant_alert(_deal(flight=_flight(79.0))).splitlines()[0]

    assert "79" not in header and "EUR" not in header and "€" not in header
    assert "HAM" not in header and "PMI" not in header


def test_flight_price_appears_only_once_in_the_cost_breakdown():
    output = format_instant_alert(_deal(flight=_flight(79.0), accommodation=_accommodation()))

    assert output.count("79 €") == 1


def test_deal_type_headline_and_emoji_are_gone_from_the_header():
    for deal_type in (DealType.COMBINED_TRIP_DROP, DealType.HOTEL_DROP, DealType.UNUSUALLY_LOW):
        first_line = format_instant_alert(_deal(deal_type=deal_type)).splitlines()[0]
        assert first_line == "🇪🇸 <b>Hamburg nach Palma de Mallorca</b>"


def test_badge_falls_back_to_the_deal_type_label_without_a_saving():
    output = format_instant_alert(_deal(deal_type=DealType.HOTEL_DROP, savings_percentage=None, score=None))

    assert output.splitlines()[1] == "💥 <b>Günstiges Hotel</b>"
    assert "günstiger als sonst" not in output


# --- origin transparency (multi-origin / flexible Abflughäfen) ----------------


def test_instant_alert_shows_the_actual_departure_airport_for_a_non_ham_origin():
    """Every alert must transparently show the actual departure airport -
    not just for the historically HAM-only routes. A deal built from a
    BER-origin flight (e.g. a multi-origin rotation target, see
    sampling_targets.build_rotating_flight_targets) must say Berlin, not a
    hardcoded/leftover Hamburg."""
    output = format_instant_alert(_deal(flight=_flight(99.0, origin="BER")))

    assert output.splitlines()[0] == "🇪🇸 <b>Berlin nach Palma de Mallorca</b>"
    assert "Hamburg" not in output and "HAM" not in output


def test_teaser_alert_also_shows_the_actual_departure_airport_for_a_non_ham_origin():
    output = format_teaser_alert(_deal(flight=_flight(99.0, origin="MUC")))

    assert output.splitlines()[0] == "🇪🇸 <b>München nach Palma de Mallorca</b>"
    assert "Hamburg" not in output and "HAM" not in output


def test_unknown_airport_codes_keep_the_code_and_a_neutral_flag():
    flight = FlightOffer(
        origin="HAM", destination="ZZZ", departure_date=_FRI, return_date=_SUN,
        price=79.0, currency="EUR", airline="Eurowings", stops=0, provider="test",
    )
    assert format_instant_alert(_deal(flight=flight)).splitlines()[0] == "✈️ <b>Hamburg nach ZZZ</b>"


def test_flag_follows_the_destination_country():
    flight = FlightOffer(
        origin="HAM", destination="FAO", departure_date=_FRI, return_date=_SUN,
        price=79.0, currency="EUR", airline="Eurowings", stops=0, provider="test",
    )
    assert format_instant_alert(_deal(flight=flight)).splitlines()[0] == "🇵🇹 <b>Hamburg nach Faro (Algarve)</b>"


def test_dates_and_nights_included():
    output = format_instant_alert(_deal())

    assert "02.10.2026" in output
    assert "04.10.2026" in output
    assert "2 Nächte" in output


def test_savings_badge_rounds_the_percentage():
    assert "💥 <b>-45% günstiger als sonst</b>" in format_instant_alert(_deal(savings_percentage=0.4499))


def test_negative_overall_savings_never_claims_a_saving():
    """Regression: a Deal can earn its deal_type from flight-level savings
    alone yet end up with a NEGATIVE overall savings_percentage once the
    hotel side is combined (trip_combiner.py) - genuinely priced above
    baseline. It must never say "günstiger als sonst" (nor show "--5%")."""
    output = format_instant_alert(_deal(savings_percentage=-0.0548))

    assert "günstiger als sonst" not in output
    assert "--" not in output
    assert output.splitlines()[1] == "💥 <b>Günstiger Flug</b>"


def test_missing_savings_percentage_does_not_crash_or_claim_a_saving():
    output = format_instant_alert(_deal(savings_absolute=None, savings_percentage=None, score=None))

    assert "günstiger als sonst" not in output


def test_hotel_line_included_when_accommodation_present():
    output = format_instant_alert(_deal(accommodation=_accommodation()))

    assert "🏨 Hostal Born Boutique: <b>45 €</b> p.P. (DZ)" in output
    assert "💰 <b>GESAMTPREIS: 124 € p.P.</b>" in output


def test_hotel_line_and_total_omitted_when_no_accommodation():
    output = format_instant_alert(_deal(accommodation=None))

    assert "🏨" not in output
    assert "💰" not in output
    assert "✈️ Flug: <b>79 €</b> p.P." in output  # the flight price still appears, once


def test_message_stays_compact():
    """"Extrem kompakt" for the data rows themselves - a flight-only deal's
    headline/date/link lines stay to a handful, even though the message as
    a whole is now longer once the destination_context blurb (its own
    paragraph, not a data row) and link lines are included."""
    output = format_instant_alert(_deal())
    data_lines = [line for line in output.splitlines() if line and not line.startswith(("📍", "👉"))]

    assert len(data_lines) <= 4  # header, badge, dates, flight price


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
    assert messages[0].startswith("🇪🇸 <b>Hamburg nach")
    assert messages[1].startswith("🇪🇸 <b>Hamburg nach")
    assert "https://example.com/book/flight2" in messages[1]


def test_batch_preserves_order():
    deals = [_deal(flight=_flight(p)) for p in (79.0, 55.0, 99.0)]

    messages = format_instant_alerts(deals)

    assert "<b>79 €</b>" in messages[0]
    assert "<b>55 €</b>" in messages[1]
    assert "<b>99 €</b>" in messages[2]


def test_empty_deal_list_returns_empty_list():
    assert format_instant_alerts([]) == []


# --- format_teaser_alert (Free-channel: VIP-upgrade CTA, no booking links) ----


def test_teaser_shares_headline_and_price_with_full_alert():
    deal = _deal(deal_type=DealType.COMBINED_TRIP_DROP, accommodation=_accommodation())

    full = format_instant_alert(deal)
    teaser = format_teaser_alert(deal)

    assert full.splitlines()[0] == teaser.splitlines()[0]
    assert "🏨 Hostal Born Boutique: <b>45 €</b> p.P. (DZ)" in teaser
    assert "💰 <b>GESAMTPREIS: 124 € p.P.</b>" in teaser


def test_teaser_never_contains_the_actual_flight_or_hotel_booking_link():
    output = format_teaser_alert(_deal(accommodation=_accommodation()))

    assert "https://example.com/book/flight" not in output
    assert "https://example.com/book/hotel" not in output


def test_teaser_never_contains_the_actual_booking_link_even_with_affiliate_tag_configured(monkeypatch):
    """The teaser must withhold the real flight/hotel booking links
    unconditionally - an affiliate tag being configured must never cause
    one to leak into the Free channel's teaser."""
    monkeypatch.setenv("TRIP_HUNTER_AFFILIATE_TAG", "triphunter123")

    output = format_teaser_alert(_deal(accommodation=_accommodation()))

    assert "https://example.com/book/flight" not in output
    assert "https://example.com/book/hotel" not in output
    assert "tp_aff" not in output


def test_teaser_includes_vip_upgrade_hint():
    output = format_teaser_alert(_deal())

    assert "VIP" in output
    assert "🔒" in output


def test_teaser_includes_both_real_stripe_checkout_links():
    output = format_teaser_alert(_deal())

    assert "https://buy.stripe.com/00w00ke0x5FX6jOfHCbMQ02" in output
    assert "https://buy.stripe.com/3cIdRa9Kh4BTgYsanibMQ01" in output


def test_teaser_matches_the_exact_cta_copy_template():
    output = format_teaser_alert(_deal())

    assert (
        "🔒 Sofortige Buchungslinks für Flug &amp; Hotel im VIP-Kanal freischalten:\n"
        "👉 VIP Monats-Pass (7,99 €): https://buy.stripe.com/00w00ke0x5FX6jOfHCbMQ02\n"
        "👉 VIP Jahres-Pass (49 € – spare 49%): https://buy.stripe.com/3cIdRa9Kh4BTgYsanibMQ01"
    ) in output


def test_teaser_stripe_links_are_not_affiliate_tagged(monkeypatch):
    """add_affiliate_tag is for outbound flight/hotel booking links, not
    our own Stripe checkout pages - the tag must never end up appended to
    them."""
    monkeypatch.setenv("TRIP_HUNTER_AFFILIATE_TAG", "triphunter123")

    output = format_teaser_alert(_deal())

    assert "https://buy.stripe.com/00w00ke0x5FX6jOfHCbMQ02" in output
    assert "https://buy.stripe.com/3cIdRa9Kh4BTgYsanibMQ01" in output
    assert "tp_aff" not in output


# --- destination context (alerts/destination_context.py) ---------------------


def test_instant_alert_includes_the_destination_context_for_pmi():
    from trip_hunter.alerts.destination_context import destination_context

    output = format_instant_alert(_deal())

    assert f"📍 {destination_context('PMI')}" in output


def test_teaser_alert_includes_the_destination_context_for_pmi():
    from trip_hunter.alerts.destination_context import destination_context

    output = format_teaser_alert(_deal())

    assert f"📍 {destination_context('PMI')}" in output


def test_both_channels_show_the_identical_destination_context():
    """Harmonious across channels: neither formatter may drift from the
    other on destination tone (module docstring's explicit requirement)."""
    deal = _deal()

    instant = format_instant_alert(deal)
    teaser = format_teaser_alert(deal)

    instant_context_line = next(line for line in instant.splitlines() if line.startswith("📍"))
    teaser_context_line = next(line for line in teaser.splitlines() if line.startswith("📍"))
    assert instant_context_line == teaser_context_line


def test_unknown_destination_falls_back_gracefully_in_the_alert():
    from trip_hunter.alerts.destination_context import destination_context

    unknown_flight = FlightOffer(
        origin="HAM", destination="ZZZ", departure_date=_FRI, return_date=_SUN,
        price=79.0, currency="EUR", airline="Eurowings", stops=0, provider="test",
    )
    output = format_instant_alert(_deal(flight=unknown_flight))

    assert f"📍 {destination_context('ZZZ')}" in output


# --- Tier 1 banner / tip --------------------------------------------------------

_BANNER = "🚨 ERROR FARE: Kann sich minütlich ändern – extrem schnell buchen!"
_TIP = "💡 Tipp: Erst den Flug buchen"


def test_tier_1_vip_alert_has_banner_in_place_of_the_badge_and_tip_last():
    output = format_instant_alert(_deal(deal_type=DealType.ERROR_FARE, accommodation=_accommodation()))
    lines = output.splitlines()
    assert lines[0] == "🇪🇸 <b>Hamburg nach Palma de Mallorca</b>"
    assert lines[1] == _BANNER
    assert "günstiger als sonst" not in output
    assert lines[-1].startswith(_TIP)
    assert "24–48h später final buchen" in lines[-1]


def test_tier_1_free_teaser_has_banner_but_no_tip():
    output = format_teaser_alert(_deal(deal_type=DealType.ERROR_FARE))
    assert output.splitlines()[1] == _BANNER
    assert _TIP not in output


def test_non_tier_1_alert_has_neither_banner_nor_tip():
    deal = _deal(deal_type=DealType.FLIGHT_DROP, savings_percentage=0.40)
    assert _BANNER not in format_instant_alert(deal)
    assert _TIP not in format_instant_alert(deal)
    assert _BANNER not in format_teaser_alert(deal)


# --- price block ---------------------------------------------------------------


def _price_block(output: str) -> list[str]:
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("✈️"))
    return lines[start : start + 4]


@pytest.mark.parametrize("formatter", [format_instant_alert, format_teaser_alert])
def test_price_block_layout_is_identical_on_vip_and_free(formatter):
    block = _price_block(formatter(_deal(accommodation=_accommodation())))

    assert block == [
        "✈️ Flug: <b>79 €</b> p.P.",
        "🏨 Hostal Born Boutique: <b>45 €</b> p.P. (DZ)",
        "───────────────",
        "💰 <b>GESAMTPREIS: 124 € p.P.</b>",
    ]


def test_layout_order_header_badge_dates_prices_blurb():
    lines = format_instant_alert(_deal(accommodation=_accommodation())).splitlines()

    assert lines[0].startswith("🇪🇸") and lines[1].startswith("💥")
    assert "Nächte" in lines[2] and lines[3].startswith("✈️")
    assert lines[7] == "" and lines[8].startswith("📍")


def test_price_rule_is_short_enough_for_a_phone_screen():
    rule = _price_block(format_instant_alert(_deal(accommodation=_accommodation())))[2]
    assert set(rule) == {"─"} and 15 <= len(rule) <= 18


def test_no_decimals_anywhere_in_the_price_block():
    output = format_instant_alert(_deal(flight=_flight(79.4), accommodation=_accommodation(90.5)))
    assert not any(char.isdigit() and "," in line for line in _price_block(output) for char in line)


def test_amounts_are_rounded_commercially_to_whole_euros():
    """181.5 -> 182 and 102.5 -> 103 (half up), not Python's banker's
    rounding (which would give 102)."""
    output = format_instant_alert(_deal(flight=_flight(79.0), accommodation=_accommodation(205.0)))

    assert "<b>103 €</b> p.P. (DZ)" in output  # 205 / 2 = 102.5
    assert "GESAMTPREIS: 182 € p.P." in output


def test_printed_parts_add_up_to_the_total():
    # 79.4 -> 79 and 100.4 -> 100: the total is 179, not round(179.8) = 180.
    output = format_instant_alert(_deal(flight=_flight(79.4), accommodation=_accommodation(200.8)))

    assert "<b>79 €</b>" in output and "<b>100 €</b>" in output
    assert "GESAMTPREIS: 179 € p.P." in output


def test_bold_marks_the_three_prices_only():
    output = format_teaser_alert(_deal(accommodation=_accommodation(), savings_percentage=None))
    assert "**" not in output
    assert output.count("<b>") == output.count("</b>")


def test_unknown_currency_keeps_its_iso_code():
    flight = FlightOffer(
        origin="HAM", destination="PMI", departure_date=_FRI, return_date=_SUN,
        price=79.0, currency="CHF", airline="Eurowings", stops=0, provider="test",
    )
    assert "✈️ Flug: <b>79 CHF</b> p.P." in format_instant_alert(_deal(flight=flight, accommodation=_accommodation()))


def test_dynamic_text_is_html_escaped():
    hotel = AccommodationOffer(
        destination="PMI", check_in=_FRI, check_out=_SUN, total_price=90.0, currency="EUR",
        name="Tom & Jerry <Inn>", rating=4.0, provider="test",
        booking_link="https://example.com/h?a=1&b=2",
    )
    output = format_instant_alert(_deal(accommodation=hotel))

    assert "Tom &amp; Jerry &lt;Inn&gt;" in output
    assert "https://example.com/h?a=1&amp;b=2" in output
    assert "<Inn>" not in output


def test_total_is_flight_plus_half_the_hotel_room():
    """Flight = 1 adult, hotel = room for 2: total p.P. = flight + hotel / 2,
    while Deal.actual_total_price (filter basis) stays flight + whole hotel."""
    deal = _deal(flight=_flight(100.0), accommodation=_accommodation(80.0))

    assert "GESAMTPREIS: 140 € p.P." in format_instant_alert(deal)
    assert deal.actual_total_price == 180.0


def test_flight_only_deal_shows_just_the_flight_line_without_total_or_rule():
    output = format_instant_alert(_deal(accommodation=None))

    assert "✈️ Flug: <b>79 €</b> p.P." in output
    assert "GESAMTPREIS" not in output and "─" not in output
