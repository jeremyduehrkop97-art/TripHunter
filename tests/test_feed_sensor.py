"""Tests for engine/feed_sensor.py. All RSS payloads are inline mocks and
all HTTP is faked - no real network access anywhere in this file."""

from __future__ import annotations

import pytest
import requests

from trip_hunter.engine.feed_sensor import (
    FEED_SOURCES,
    DealSignal,
    fetch_feed,
    find_german_origins,
    parse_feed,
    scan_feeds,
)


def _rss(*items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>'
        "<title>Mock</title>" + "".join(items) + "</channel></rss>"
    )


def _item(title, *, link="https://example.com/deal/1/?utm_source=feed", description="", pub="Thu, 24 Sep 2026 15:55:00 +0000", cats=()):
    cat_xml = "".join(f"<category><![CDATA[{c}]]></category>" for c in cats)
    return (
        f"<item><title>{title}</title><link>{link.replace('&', '&amp;')}</link>"
        f"<pubDate>{pub}</pubDate>{cat_xml}"
        f"<description><![CDATA[{description}]]></description></item>"
    )


def _one(title, **kw) -> DealSignal:
    signals = parse_feed(_rss(_item(title, **kw)), "test")
    assert len(signals) == 1, signals
    return signals[0]


# --- origin filter -------------------------------------------------------------


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Taipeh ab 470€ mit Air China von München und Frankfurt", ("MUC", "FRA")),
        ("Barcelona ab Düsseldorf ab 19€", ("DUS",)),
        ("Hamburg to Lisbon for €29 roundtrip", ("HAM",)),
        ("Palma ab HAM, BER & MUC ab 35€", ("HAM", "BER", "MUC")),
        ("Flüge nach Bangkok ab Berlin", ("BER",)),
        ("Munich to Rome for €39", ("MUC",)),
        ("Weltreisebaustein: Singapur → München oder Wien, oneway ab 756€", ()),
        ("Flüge nach Hamburg ab Wien für 49€", ()),
        ("Costa Pacifica: ab/bis Barcelona", ()),
        ("Hamburger Fischmarkt Gutschein", ()),
        ("Paris ab Stuttgart ab 30€", ()),
    ],
)
def test_find_german_origins(title, expected):
    assert find_german_origins(title) == expected


def test_items_without_a_german_origin_are_dropped():
    xml = _rss(_item("Lissabon ab Wien ab 29€"), _item("Rom ab Hamburg ab 49€"))
    signals = parse_feed(xml, "test")
    assert [s.origins for s in signals] == [("HAM",)]


@pytest.mark.parametrize(
    "title, cats",
    [
        ("Kreuzfahrt ab Hamburg ab 499€", ()),
        ("Mittelmeer ab Hamburg ab 499€", ("Kreuzfahrten",)),
        ("Interrail ab Berlin ab 4€", ()),
        ("Gutschein: Flüge ab Frankfurt", ()),
    ],
)
def test_non_flight_items_are_dropped(title, cats):
    assert parse_feed(_rss(_item(title, cats=cats)), "test") == []


# --- tier 1 --------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "ERROR FARE: Rom ab Hamburg",
        "Preisfehler? Lissabon ab Berlin für 89€",
        "Mistake fare from Frankfurt to Bali €199",
        "Extrem günstig: Madrid ab München ab 120€",
    ],
)
def test_keyword_signals_are_tier_1(title):
    signal = _one(title)
    assert signal.is_tier_1
    assert signal.tier_1_reasons[0].startswith("keyword:")


def test_price_at_or_below_40_is_tier_1():
    signal = _one("Barcelona ab Düsseldorf ab 40€")
    assert signal.is_tier_1
    assert signal.tier_1_reasons == ("price<=40",)


def test_price_above_40_is_not_tier_1():
    signal = _one("Barcelona ab Düsseldorf ab 41€")
    assert not signal.is_tier_1


def test_per_night_or_per_day_prices_do_not_trigger_the_price_rule():
    assert not _one("Hotel-Flug-Kombi ab Berlin: 30€/Nacht in Rom").is_tier_1
    assert not _one("Rom ab Berlin, Bahn 4€ pro Tag").is_tier_1


def test_tier_1_only_filter():
    xml = _rss(_item("Rom ab Hamburg ab 149€"), _item("Preisfehler: Paris ab Hamburg 25€"))
    assert len(parse_feed(xml, "test")) == 2
    only = parse_feed(xml, "test", tier_1_only=True)
    assert len(only) == 1 and only[0].price == 25.0


# --- extraction ----------------------------------------------------------------


def test_destination_and_iata_from_dest_first_title():
    signal = _one("Lissabon ab 39€ mit TAP von Hamburg")
    assert signal.destination == "Lissabon"
    assert signal.destination_iata == "LIS"
    assert signal.price == 39.0
    assert signal.origins == ("HAM",)


def test_destination_from_route_arrow_and_to():
    a = _one("Hamburg → Palma de Mallorca ab 35€")
    assert (a.destination, a.destination_iata) == ("Palma de Mallorca", "PMI")
    b = _one("Berlin to Barcelona for €29 roundtrip")
    assert (b.destination, b.destination_iata) == ("Barcelona", "BCN")
    assert b.price == 29.0


def test_destination_after_nach_and_prefix_stripped():
    signal = _one("Error Fare: Flüge nach Bangkok ab Berlin für 199€")
    assert signal.destination == "Bangkok"
    assert signal.destination_iata == "BKK"


def test_explicit_iata_destination():
    assert _one("HAM to LIS for €30").destination_iata == "LIS"


def test_unknown_destination_keeps_text_but_no_iata():
    signal = _one("Ulan Bator ab 39€ von Frankfurt")
    assert signal.destination == "Ulan Bator"
    assert signal.destination_iata is None


def test_german_airport_is_never_reported_as_the_destination():
    from trip_hunter.engine.feed_sensor import _extract_destination

    assert _extract_destination("Hamburg ab 30€ mit Ryanair") is None


def test_missing_destination_is_none_not_guessed():
    signal = _one("Preisfehler ab Hamburg")
    assert signal.destination is None and signal.destination_iata is None


@pytest.mark.parametrize(
    "title, price",
    [("Rom ab 1.999€ von München", 1999.0), ("Rom ab 29,90€ von München", 29.9), ("Rom ab München €35", 35.0), ("Rom von München", None)],
)
def test_price_parsing(title, price):
    assert _one(title).price == price


def test_travel_dates_from_title_range():
    assert _one("Rom ab Hamburg 25€ (12.10.–19.10.2026)").travel_dates == "12.10.–19.10.2026"


def test_travel_dates_month_from_title_and_description_fallback():
    assert _one("Rom ab Hamburg 25€ im Oktober 2026").travel_dates == "Oktober 2026"
    signal = _one("Rom ab Hamburg 25€", description="<p>Flüge im November verfügbar</p>")
    assert signal.travel_dates == "November"


def test_no_travel_dates_is_none():
    assert _one("Rom ab Hamburg 25€").travel_dates is None


def test_metadata_and_html_entities():
    signal = _one("Rom &amp; Mailand ab Hamburg 25€", link="https://example.com/d/?utm_source=feed&utm_medium=rss")
    assert signal.source == "test"
    assert "&" in signal.title and "&amp;" not in signal.title
    assert signal.link == "https://example.com/d/?utm_source=feed&utm_medium=rss"
    assert signal.published is not None and signal.published.year == 2026


def test_bad_pubdate_is_none():
    assert _one("Rom ab Hamburg 25€", pub="not a date").published is None


def test_signal_is_immutable():
    with pytest.raises(Exception):
        _one("Rom ab Hamburg 25€").price = 1.0  # type: ignore[misc]


# --- robustness ----------------------------------------------------------------


def test_malformed_xml_returns_empty(capsys):
    assert parse_feed("<rss><channel><item>", "test") == []
    assert "kein gültiges XML" in capsys.readouterr().out


def test_html_challenge_page_returns_empty():
    assert parse_feed("<!DOCTYPE html><html><head><title>Just a moment...</title>", "test") == []


def test_entity_expansion_payload_is_rejected():
    bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><rss><channel><item><title>&a; ab Hamburg 20€</title></item></channel></rss>'
    assert parse_feed(bomb, "test") == []


def test_oversized_feed_is_rejected():
    assert parse_feed(_rss(_item("Rom ab Hamburg 25€")) + " " * 2_000_001, "test") == []


def test_item_without_title_is_skipped():
    assert parse_feed(_rss("<item><link>https://x</link></item>"), "test") == []


# --- fetch / scan (fake HTTP) --------------------------------------------------


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code, self.text = status_code, text


class _Session:
    def __init__(self, by_url):
        self.by_url, self.calls = by_url, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        result = self.by_url[url]
        if isinstance(result, Exception):
            raise result
        return result


def test_feed_sources_cover_both_required_feeds():
    assert FEED_SOURCES["travel-dealz"] == "https://travel-dealz.de/feed/"
    assert FEED_SOURCES["secretflying"] == "https://www.secretflying.com/feed/"


@pytest.mark.parametrize(
    "outcome",
    [_Resp(403, "blocked"), requests.exceptions.Timeout(), requests.exceptions.ConnectionError("boom")],
)
def test_fetch_feed_never_raises_and_returns_none_on_failure(outcome):
    assert fetch_feed("https://f", session=_Session({"https://f": outcome})) is None


def test_fetch_feed_returns_body_and_sends_user_agent():
    class S(_Session):
        def get(self, url, headers=None, timeout=None):
            self.headers = headers
            return super().get(url, headers, timeout)

    session = S({"https://f": _Resp(200, "<rss/>")})
    assert fetch_feed("https://f", session=session) == "<rss/>"
    assert "TripHunter" in session.headers["User-Agent"]


def test_scan_feeds_merges_dedupes_sorts_and_survives_a_dead_source():
    a = _rss(
        _item("Rom ab Hamburg ab 149€", link="https://a.example/1/?utm_source=x", pub="Thu, 24 Sep 2026 10:00:00 +0000"),
        _item("Preisfehler: Paris ab Berlin 25€", link="https://a.example/2/", pub="Wed, 23 Sep 2026 10:00:00 +0000"),
    )
    b = _rss(
        _item("Rom ab Hamburg ab 149€", link="https://a.example/1/?utm_source=y"),  # duplicate
        _item("Madrid ab Frankfurt ab 99€", link="https://b.example/3/", pub="Thu, 24 Sep 2026 12:00:00 +0000"),
    )
    session = _Session({"https://a": _Resp(200, a), "https://dead": _Resp(403, "cf"), "https://b": _Resp(200, b)})

    signals = scan_feeds({"a": "https://a", "dead": "https://dead", "b": "https://b"}, session=session)

    assert [s.destination for s in signals] == ["Paris", "Madrid", "Rom"]  # tier 1 first, then newest
    assert session.calls == ["https://a", "https://dead", "https://b"]


def test_scan_feeds_tier_1_only():
    xml = _rss(_item("Rom ab Hamburg ab 149€"), _item("Preisfehler: Paris ab Berlin 25€", link="https://x/2"))
    signals = scan_feeds({"a": "https://a"}, tier_1_only=True, session=_Session({"https://a": _Resp(200, xml)}))
    assert [s.destination for s in signals] == ["Paris"]
