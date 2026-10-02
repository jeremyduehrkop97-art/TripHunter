"""web/index.html's hero comparison example ("#problem" section): the
concrete Palma weekend deal, not stale placeholder numbers."""

from __future__ import annotations

from pathlib import Path

_HTML = (Path(__file__).parent.parent / "web" / "index.html").read_text(encoding="utf-8")


def _compare_section() -> str:
    start = _HTML.index('<div class="compare-grid">')
    return _HTML[start : _HTML.index("</section>", start)]


def test_market_price_card_shows_the_full_undiscounted_trip():
    section = _compare_section()
    assert "230,00&nbsp;€" in section  # flight
    assert "180,00&nbsp;€" in section  # hotel, per person
    assert "410,00&nbsp;€" in section  # total, per person


def test_trip_hunter_card_shows_the_discovered_deal():
    section = _compare_section()
    assert "39,00&nbsp;€" in section   # nonstop flight
    assert "50,00&nbsp;€" in section   # checked hotel, per person
    assert "89,00&nbsp;€" in section   # total, per person
    assert "Nonstop" in section and "4,1★" in section


def test_savings_verdict_is_the_real_arithmetic_of_both_totals():
    assert 230.0 + 180.0 == 410.0
    assert 39.0 + 50.0 == 89.0
    assert round(410.0 - 89.0, 2) == 321.0
    assert round((410.0 - 89.0) / 410.0 * 100) == 78

    section = _compare_section()
    assert "321&nbsp;€ gespart" in section
    assert "−78&nbsp;%" in section


def test_compare_cards_example_is_the_palma_weekend():
    assert "02.–04.10." in _compare_section()
    assert "Palma" in _compare_section()


def test_stale_placeholder_numbers_are_gone():
    section = _compare_section()
    for stale in ("29,00&nbsp;€", "320,00&nbsp;€", "349,00&nbsp;€", "142,00&nbsp;€"):
        assert stale not in section


# --- hero mockup ("Beispiel-Alert"): the Tier-1 error-fare card ------------------
# Deliberately a DIFFERENT scenario from the #problem compare cards above
# (an error fare vs. a concrete weekend combi-deal) - two illustrations
# telling two different parts of the product story, not one shared example
# any more.


def _hero_card() -> str:
    start = _HTML.index('<div class="alert-card">')
    return _HTML[start : _HTML.index("</div>\n          <div class=\"bubble-meta\">", start)]


def test_hero_shows_the_error_fare_badge_and_route():
    card = _hero_card()
    assert "🚨 ERROR FARE" in card and "extrem schnell buchen" in card
    assert "Frankfurt nach New York" in card and "(JFK)" in card


def test_hero_shows_the_savings_tag():
    assert "-68" in _hero_card() and "günstiger als Normalpreis" in _hero_card()


def test_hero_shows_all_three_detail_rows_with_their_icons():
    card = _hero_card()
    assert "🗓" in card and "Reisezeit:</strong> z.B. 10.11.–18.11.2026" in card
    assert "💥" in card and "Rückflug für 189" in card and "Lufthansa / United" in card
    assert "🏨" in card and "4-Sterne Hotel ab ca. 120" in card and "Richtwert" in card


def test_hero_shows_the_tip_and_status_fine_print():
    card = _hero_card()
    assert "💡 Tipp:" in card and "24" in card and "48" in card
    assert "⚠️ Fehlerpreis:" in card and "jederzeit korrigieren" in card


def test_hero_cta_button_is_the_real_free_channel_link():
    card = _hero_card()
    assert "⚡️ Jetzt Deal buchen" in card
    assert 'href="https://t.me/triphunterfree"' in card
    assert 'rel="noopener"' in card


def test_old_combi_deal_mockup_content_is_gone():
    """The hero used to show a regular combi-deal (HAM->PMI, 184/58/242
    EUR) - replaced entirely by the error-fare card above."""
    for stale in ("HAM → PMI", "184,00&nbsp;€", "242,00&nbsp;€", "COMBINED TRIP DROP", "Du sparst 138,00"):
        assert stale not in _HTML
