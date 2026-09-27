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


def test_example_route_and_dates_match_the_phone_frame_mockup_above_it():
    """Same HAM->PMI weekend (Fri 02.10. -> Sun 04.10.2026) as the hero's
    phone-frame example, so the two illustrations tell one coherent story."""
    assert "02.10.–04.10.2026" in _HTML  # phone-frame mockup
    assert "02.–04.10." in _compare_section()  # compare cards
    assert "Palma" in _compare_section()


def test_stale_placeholder_numbers_are_gone():
    section = _compare_section()
    for stale in ("29,00&nbsp;€", "320,00&nbsp;€", "349,00&nbsp;€", "142,00&nbsp;€"):
        assert stale not in section
