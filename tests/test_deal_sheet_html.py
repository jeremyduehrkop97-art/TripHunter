"""web/deal.html - the Mini-App deal sheet. Static structure checks, plus the
page's validation logic executed in Node (skipped if node is missing)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HTML = (Path(__file__).parent.parent / "web" / "deal.html").read_text(encoding="utf-8")
_NODE = shutil.which("node")


def test_page_uses_tailwind_cdn_and_the_telegram_webapp_script():
    assert '<script src="https://cdn.tailwindcss.com"></script>' in _HTML
    assert '<script src="https://telegram.org/js/telegram-web-app.js"></script>' in _HTML
    assert 'name="viewport"' in _HTML and 'lang="de"' in _HTML


def test_page_has_the_requested_header_prices_and_two_step_buttons():
    for expected in (
        'id="hero"', 'id="badge"', 'id="route"',                       # header: image, saving badge, cities
        'id="fp"', 'id="hp"', 'id="tp"', "Gesamtpreis",                # price breakdown
        "1. ✈️ Flug prüfen &amp; reservieren",
        "Erst den Flug buchen, da Flugpreise schneller schwanken.",
        "2. 🏨 Hotel buchen",
    ):
        assert expected in _HTML, expected
    assert _HTML.index("1. ✈️ Flug prüfen") < _HTML.index("Erst den Flug buchen") < _HTML.index("2. 🏨 Hotel buchen")


def test_deal_data_is_read_from_the_query_string_not_the_hash():
    assert "window.location.search" in _HTML
    assert "location.hash" not in _HTML  # Telegram owns the hash (#tgWebAppData)


def test_no_untrusted_value_is_ever_written_as_html():
    render = re.search(r'<script id="deal-render">([\s\S]*?)</script>', _HTML).group(1)
    logic = re.search(r'<script id="deal-logic">([\s\S]*?)</script>', _HTML).group(1)

    for code in (render, logic):
        assert "innerHTML" not in code and "outerHTML" not in code and "document.write" not in code
        assert "insertAdjacentHTML" not in code and "eval(" not in code
    assert "textContent" in render


def test_page_is_not_indexed_and_sends_no_referrer():
    assert 'name="robots" content="noindex,nofollow"' in _HTML
    assert 'name="referrer" content="no-referrer"' in _HTML


def test_outbound_buttons_are_marked_as_sponsored_and_noopener():
    assert _HTML.count('rel="noopener noreferrer sponsored"') == 2
    assert "Affiliate" in _HTML


# --- logic in Node ---------------------------------------------------------------

_DRIVER = """
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const code = html.match(/<script id="deal-logic">([\\s\\S]*?)<\\/script>/)[1];
const ctx = { URL, URLSearchParams, Date, Math, Number, String, isFinite, isNaN };
ctx.globalThis = ctx; vm.createContext(ctx); vm.runInContext(code, ctx);
const D = ctx.DealSheet;
const out = JSON.parse(process.argv[2]).map(([fn, ...args]) => D[fn](...args));
console.log(JSON.stringify(out));
"""


def _run(*calls):
    result = subprocess.run(
        [_NODE, "-e", _DRIVER, str(Path(__file__).parent.parent / "web" / "deal.html"), json.dumps([list(c) for c in calls])],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


needs_node = pytest.mark.skipif(_NODE is None, reason="node not installed")

_FL = "https://www.google.com/travel/flights?q=Flights%20from%20HAM&hl=de"
_HL = "https://www.booking.com/searchresults.de.html?ss=Hotel%20X&aid=1"


def _qs(**params):
    from urllib.parse import urlencode

    return "?" + urlencode(params)


@needs_node
def test_a_full_deal_is_parsed_and_normalised():
    (deal,) = _run(("parseDeal", _qs(
        **{"from": "Hamburg", "to": "Palma de Mallorca", "flag": "🇪🇸", "code": "pmi", "dep": "2026-10-02",
           "ret": "2026-10-07", "fp": "79", "hp": "103", "tp": "182", "hn": "Hotel Playa Sol", "sv": "45",
           "fl": _FL, "hl": _HL, "img": "https://images.unsplash.com/photo-1?w=1280"})))

    assert deal["ok"] is True
    assert (deal["from"], deal["to"], deal["code"], deal["nights"]) == ("Hamburg", "Palma de Mallorca", "PMI", 5)
    assert (deal["fp"], deal["hp"], deal["tp"], deal["saving"]) == (79, 103, 182, 45)
    assert deal["flightLink"] == _FL and deal["hotelLink"] == _HL
    assert deal["image"] == "https://images.unsplash.com/photo-1?w=1280"


@needs_node
def test_missing_or_bad_flight_link_means_no_deal():
    results = _run(
        ("parseDeal", ""), ("parseDeal", "?fp=79"),
        ("parseDeal", _qs(fl="javascript:alert(1)")),
        ("parseDeal", _qs(fl="data:text/html,<script>alert(1)</script>")),
        ("parseDeal", _qs(fl="http://www.google.com/travel/flights")),      # not https
        ("parseDeal", _qs(fl="https://evil.example/phish")),                 # host not allowlisted
        ("parseDeal", _qs(fl="https://www.google.com.evil.example/x")),      # look-alike host
        ("parseDeal", _qs(fl="https://user:pw@www.google.com/x")),           # credentials in URL
    )
    assert all(r == {"ok": False} for r in results)


@needs_node
def test_links_and_image_are_checked_against_host_allowlists():
    (deal,) = _run(("parseDeal", _qs(
        fl=_FL, hl="https://evil.example/hotel", img="https://evil.example/x.png")))

    assert deal["ok"] is True and deal["hotelLink"] is None and deal["image"] is None


@needs_node
@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.google.com/travel/flights", True),
        ("https://www.booking.com/searchresults.de.html?aid=1", True),
        ("https://www.aviasales.com/search/HAM0210PMI07101?marker=1", True),
        ("https://www.skyscanner.de/transport/fluge/ham/pmi/", True),
        ("https://tp.media/r?marker=1", True),
        ("https://booking.com.evil.example/", False),
        ("https://evilbooking.com/", False),
        ("ftp://www.google.com/", False),
        ("//www.google.com/", False),
        ("not a url", False),
    ],
)
def test_link_allowlist(url, expected):
    hosts = ["google.com", "booking.com", "aviasales.com", "skyscanner.de", "tp.media"]
    (safe,) = _run(("safeUrl", url, hosts))
    assert (safe is not None) is expected


@needs_node
def test_html_in_text_parameters_stays_plain_text_data():
    (deal,) = _run(("parseDeal", _qs(fl=_FL, to="<img src=x onerror=alert(1)>", hn="<script>alert(1)</script>")))

    # The parser hands the text back verbatim (and length-capped); the page
    # only ever assigns it via textContent, so it is displayed, never run.
    assert deal["to"] == "<img src=x onerror=alert(1)>" and deal["hotelName"] == "<script>alert(1)</script>"


@needs_node
def test_numbers_are_validated_and_totals_derived():
    (deal, bad) = _run(
        ("parseDeal", _qs(fl=_FL, fp="79", hp="103")),
        ("parseDeal", _qs(fl=_FL, fp="abc", hp="-5", tp="1e9", sv="250")),
    )
    assert deal["tp"] == 182  # derived: flight + hotel share
    assert (bad["fp"], bad["hp"], bad["tp"], bad["saving"]) == (None, None, None, None)


@needs_node
def test_flight_only_deal_has_no_hotel_data():
    (deal,) = _run(("parseDeal", _qs(fl=_FL, fp="29")))
    assert deal["hp"] is None and deal["hotelLink"] is None and deal["tp"] == 29


@needs_node
def test_dates_and_nights():
    (deal, bad, label, euro) = _run(
        ("parseDeal", _qs(fl=_FL, dep="2026-10-02", ret="2026-10-04")),
        ("parseDeal", _qs(fl=_FL, dep="2026-13-45", ret="nonsense")),
        ("dateLabel", "2026-10-02"),
        ("euro", 1234.5),
    )
    assert deal["nights"] == 2 and bad["dep"] is None and bad["nights"] is None
    assert label == "02.10.2026"
    assert euro.replace(" ", " ").replace("\xa0", " ") in ("1.235 €", "1235 €")


@needs_node
def test_overlong_values_are_capped():
    (deal,) = _run(("parseDeal", _qs(fl=_FL, to="x" * 500)))
    assert len(deal["to"]) == 40
