"""Tests for engine/feed_sensor.py. All RSS payloads are inline mocks and
all HTTP is faked - no real network access anywhere in this file."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

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


def test_well_formed_but_non_rss_xml_is_not_a_feed(capsys):
    assert parse_feed("<html><body>Just a moment...</body></html>", "test") == []
    assert "kein RSS-Feed" in capsys.readouterr().out


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

    signals = scan_feeds({"a": "https://a", "dead": "https://dead", "b": "https://b"}, max_age=None, session=session)

    assert [s.destination for s in signals] == ["Paris", "Madrid", "Rom"]  # tier 1 first, then newest
    assert session.calls == ["https://a", "https://dead", "https://b"]


def test_scan_feeds_tier_1_only():
    xml = _rss(_item("Rom ab Hamburg ab 149€"), _item("Preisfehler: Paris ab Berlin 25€", link="https://x/2"))
    signals = scan_feeds({"a": "https://a"}, tier_1_only=True, max_age=None, session=_Session({"https://a": _Resp(200, xml)}))
    assert [s.destination for s in signals] == ["Paris"]


# --- FlyerTalk (Mileage Run Deals) ---------------------------------------------


def _flyertalk_rss(*titles: str) -> str:
    """vBulletin-style RSS 2.0 with the ISO-8859-1 declaration FlyerTalk uses."""
    items = "".join(
        f"<item><title>{t}</title><link>https://www.flyertalk.com/forum/mileage-run-deals-372/{i}-x.html</link>"
        f"<pubDate>Thu, 24 Sep 2026 20:00:00 GMT</pubDate>"
        f"<description>&lt;p&gt;thread text&lt;/p&gt;</description></item>"
        for i, t in enumerate(titles)
    )
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>\n<rss version="2.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>'
        f"<title>FlyerTalk Forums - Mileage Run Deals</title>{items}</channel></rss>"
    )


def _ft(title: str) -> DealSignal:
    signals = parse_feed(_flyertalk_rss(title), "flyertalk")
    assert len(signals) == 1, signals
    return signals[0]


def test_flyertalk_feed_is_registered_with_forum_372():
    assert FEED_SOURCES["flyertalk"] == "https://www.flyertalk.com/forum/external.php?type=rss2&forumids=372"


@pytest.mark.parametrize(
    "title, origin, destination, price",
    [
        ("LH: FRA-JFK 280 EUR", "FRA", "JFK", 280.0),
        ("BA/AA: DUS-MIA €320 rt", "DUS", "MIA", 320.0),
        ("HAM-LIS from 35€", "HAM", "LIS", 35.0),
        ("MUC - BKK 240 EUR", "MUC", "BKK", 240.0),
        ("BER–LIS 49 EUR", "BER", "LIS", 49.0),  # en dash
        ("HAM/LIS 45€", "HAM", "LIS", 45.0),
        ("FRA→CDG €60", "FRA", "CDG", 60.0),
        ("FRA->JFK 300 EUR", "FRA", "JFK", 300.0),
    ],
)
def test_flyertalk_route_formats(title, origin, destination, price):
    signal = _ft(title)

    assert signal.origins == (origin,)
    assert signal.destination == destination == signal.destination_iata
    assert signal.price == price
    assert signal.source == "flyertalk"


def test_flyertalk_round_trip_route_uses_the_german_departure():
    signal = _ft("FRA-JFK-FRA Mistake fare")
    assert (signal.origins, signal.destination_iata) == (("FRA",), "JFK")


def test_flyertalk_inbound_flights_to_germany_are_not_departures():
    xml = _flyertalk_rss("JFK-FRA 250 EUR", "MIA - DUS €300", "LIS-HAM from 30€", "HAM-LIS from 35€")
    assert [s.origins for s in parse_feed(xml, "flyertalk")] == [("HAM",)]


def test_flyertalk_non_german_departures_are_dropped():
    assert parse_feed(_flyertalk_rss("VIE-BKK 300 EUR", "LHR-JFK $200", "CDG-LIS €40"), "flyertalk") == []


def test_flyertalk_several_german_origins_in_one_title():
    signal = _ft("HAM-LIS, BER-LIS from 35€")
    assert signal.origins == ("HAM", "BER")


def test_flyertalk_non_airport_words_after_a_code_are_not_destinations():
    assert parse_feed(_flyertalk_rss("HAM-USA 30 EUR"), "flyertalk") == []


def test_lowercase_or_embedded_codes_are_not_treated_as_routes():
    assert parse_feed(_flyertalk_rss("Sam-ham 30 EUR", "XHAM-LIS 30 EUR"), "flyertalk") == []


@pytest.mark.parametrize(
    "title, keyword",
    [
        ("ERROR: BER-DXB 199 EUR", "error"),
        ("Mistake fare? HAM-LIS 80 EUR", "mistake"),
        ("Price drop FRA/MAD 90 EUR", "drop"),
        ("FRA-JFK dropped to 200 EUR", "drop"),
        ("Errors on MUC-LIS fares", "error"),
    ],
)
def test_flyertalk_keywords_trigger_tier_1(title, keyword):
    assert f"keyword:{keyword}" in _ft(title).tier_1_reasons


def test_keywords_are_word_bounded():
    signal = _ft("Dropbox promo HAM-LIS 90 EUR")
    assert not signal.is_tier_1


def test_short_haul_price_bar_is_40():
    assert _ft("HAM-LIS from 40€").tier_1_reasons == ("price<=40",)
    assert not _ft("HAM-LIS from 41€").is_tier_1


def test_short_haul_price_above_40_is_not_tier_1_even_below_the_long_haul_bar():
    assert not _ft("HAM-LIS from 200 EUR").is_tier_1


def test_long_haul_price_bar_is_250():
    at_bar = _ft("FRA-JFK 250 EUR")
    assert at_bar.tier_1_reasons == ("price<=250:long-haul",)
    assert not _ft("FRA-JFK 251 EUR").is_tier_1
    assert not _ft("LH: FRA-JFK 280 EUR").is_tier_1
    assert _ft("MUC - BKK 240 EUR").is_tier_1


def test_unknown_destination_never_uses_the_long_haul_bar():
    assert not _ft("FRA-XYZ 200 EUR").is_tier_1


def test_non_euro_prices_are_ignored_not_converted():
    signal = _ft("BER-DXB $150")
    assert signal.price is None and not signal.is_tier_1


def test_long_haul_bar_also_applies_to_german_titles():
    signal = _one("Bangkok ab 199€ von Berlin")
    assert signal.destination_iata == "BKK" and signal.is_tier_1


def test_month_abbreviations_are_read_as_travel_dates():
    assert _ft("MUC-BKK 240 EUR Oct-Nov").travel_dates == "Oct"
    assert _ft("HAM-LIS 35€ Nov 2026").travel_dates == "Nov 2026"


def test_flyertalk_iso_8859_1_payload_with_umlauts_parses():
    xml = _flyertalk_rss("Düsseldorf DUS-LIS 39 EUR")
    signal = parse_feed(xml, "flyertalk")[0]
    assert signal.origins == ("DUS",) and "Düsseldorf" in signal.title


def test_empty_flyertalk_feed_yields_no_signals():
    """The feed legitimately holds zero items at times (seen live)."""
    assert parse_feed(_flyertalk_rss(), "flyertalk") == []


def test_scan_feeds_keeps_flyertalk_threads_with_query_string_links_apart():
    xml = (
        '<rss version="2.0"><channel>'
        "<item><title>HAM-LIS 30 EUR</title><link>https://ft/showthread.php?t=1&amp;utm_source=x</link></item>"
        "<item><title>BER-LIS 31 EUR</title><link>https://ft/showthread.php?t=2</link></item>"
        "</channel></rss>"
    )
    signals = scan_feeds({"ft": "https://ft"}, max_age=None, session=_Session({"https://ft": _Resp(200, xml)}))
    assert len(signals) == 2


def test_scan_feeds_combines_flyertalk_and_travel_dealz():
    ft = _flyertalk_rss("HAM-LIS from 35€")
    td = _rss(_item("Preisfehler: Paris ab Berlin 25€", link="https://td/1"))
    session = _Session({"https://ft": _Resp(200, ft), "https://td": _Resp(200, td)})

    signals = scan_feeds({"flyertalk": "https://ft", "travel-dealz": "https://td"}, max_age=None, session=session)

    assert {s.source for s in signals} == {"flyertalk", "travel-dealz"}
    assert all(s.is_tier_1 for s in signals)


# --- Urlaubspiraten ------------------------------------------------------------


def _up_item(title, link, description):
    return (
        f"<item><title>{title}</title><link>{link}</link><guid isPermaLink=\"true\">{link}</guid>"
        f"<pubDate>Thu, 24 Sep 2026 17:00:00 +0200</pubDate><description>{description}</description></item>"
    )


def _up_rss(*items):
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        "<channel><title>Urlaubspiraten</title>" + "".join(items) + "</channel></rss>"
    )


_UP = "https://www.urlaubspiraten.de"
_MALEDIVEN = _up_item(
    "Malediven Flugkracher 🔥 ", f"{_UP}/fluege/malediven-air-arabia",
    "Ahoi Piraten, wollt ihr die Malediven endlich abhaken? Mit denen ihr ab Frankfurt ab nur 495 € "
    "auf die Inseln kommt, im Oktober und November! Beim günstigsten Beispiel fliegt ihr mit Air Arabia.",
)
_KIRGISISTAN = _up_item(
    "Günstige Flüge nach Kirgisistan 🇰🇬", f"{_UP}/fluege/bischkek-ajet",
    "Ahoi Piraten! Flüge in die Hauptstadt Bischkek mit Ajet über Ankara. Ab vielen Flughäfen für nur 159€.",
)
_CHIANG_MAI = _up_item(
    "Günstige Flüge nach Chiang Mai 🌴☀️", f"{_UP}/fluege/airchina-chiangmai",
    "Wir haben günstige Flüge nach Chiang Mai entdeckt! Ihr zahlt gerade mal ab 474 € mit Air China. "
    "Los gehts ab Frankfurt!",
)
_HOTEL = _up_item(
    "Zentral in Hamburg ⚓️", f"{_UP}/hotels/grand-elysee-hamburg",
    "Für zwei Übernachtungen im Grand Elysée Hamburg zahlt ihr ab 189€ pro Person. Anreise ab Hamburg.",
)


def test_urlaubspiraten_is_registered():
    assert FEED_SOURCES["urlaubspiraten"] == "https://www.urlaubspiraten.de/feed"


def test_urlaubspiraten_origin_and_price_come_from_the_description():
    (signal,) = parse_feed(_up_rss(_MALEDIVEN), "urlaubspiraten")

    assert signal.origins == ("FRA",)
    assert signal.price == 495.0
    assert signal.travel_dates == "Oktober"
    assert signal.source == "urlaubspiraten"
    assert signal.link == f"{_UP}/fluege/malediven-air-arabia"
    assert not signal.is_tier_1  # 495 > long-haul bar; no keyword in the title


def test_urlaubspiraten_destination_from_the_title_with_emoji_stripped():
    (signal,) = parse_feed(_up_rss(_CHIANG_MAI), "urlaubspiraten")

    assert signal.destination == "Chiang Mai"
    assert signal.destination_iata == "CNX"
    assert signal.price == 474.0 and signal.origins == ("FRA",)


def test_urlaubspiraten_items_without_a_named_german_airport_are_dropped():
    assert parse_feed(_up_rss(_KIRGISISTAN), "urlaubspiraten") == []  # "Ab vielen Flughäfen"


def test_urlaubspiraten_only_flight_links_count():
    assert parse_feed(_up_rss(_HOTEL), "urlaubspiraten") == []


def test_urlaubspiraten_cruises_are_dropped_even_under_a_flight_link():
    cruise = _up_item("Kreuzfahrt inkl. Flüge", f"{_UP}/fluege/aida", "ab Hamburg ab 999€")
    assert parse_feed(_up_rss(cruise), "urlaubspiraten") == []


def test_urlaubspiraten_tier_1_by_price_and_by_title_keyword():
    cheap = _up_item(
        "Günstige Flüge nach Lissabon", f"{_UP}/fluege/lissabon", "Los gehts ab Hamburg, ab nur 35 € hin und zurück!"
    )
    error = _up_item("Preisfehler nach Bangkok?", f"{_UP}/fluege/bkk", "Abflug ab München, 600 €.")
    signals = parse_feed(_up_rss(cheap, error), "urlaubspiraten")

    assert [s.tier_1_reasons for s in signals] == [("price<=40",), ("keyword:preisfehler",)]
    assert signals[0].destination_iata == "LIS"


def test_urlaubspiraten_keywords_in_the_description_do_not_trigger_tier_1():
    item = _up_item(
        "Günstige Flüge nach Rom", f"{_UP}/fluege/rom", "Kein Error Fare, nur ein Drop. Ab Berlin ab 120 €."
    )
    (signal,) = parse_feed(_up_rss(item), "urlaubspiraten")
    assert not signal.is_tier_1


def test_urlaubspiraten_tier_1_only_and_dedupe_in_a_scan():
    cheap = _up_item("Flüge nach Lissabon", f"{_UP}/fluege/lissabon", "Ab Hamburg ab 35 €.")
    session = _Session({"https://up": _Resp(200, _up_rss(cheap, cheap, _MALEDIVEN))})

    all_signals = scan_feeds({"urlaubspiraten": "https://up"}, max_age=None, session=session)
    tier_1 = scan_feeds({"urlaubspiraten": "https://up"}, tier_1_only=True, max_age=None, session=session)

    assert len(all_signals) == 2  # duplicate cheap item collapsed
    assert [s.destination_iata for s in tier_1] == ["LIS"]


def test_other_sources_do_not_read_origin_or_price_from_the_description():
    item = _item("Rom ab 199€", description="<p>ab Hamburg</p>", link="https://x/1")
    assert parse_feed(_rss(item), "travel-dealz") == []


# --- FlyerTalk chains / encoding -----------------------------------------------


@pytest.mark.parametrize(
    "title, origins, destination",
    [
        ("EI: DUB-MIA/ORD/MCO/PHL/BOS from €105 rt", (), None),  # Dublin departure
        ("LH: FRA-MIA/ORD/BOS from €300 rt", ("FRA",), "MIA"),
        ("AC/LX: LGA-ZRH-FRA-JFK rt USD 700", (), None),  # FRA is a connection
        ("LH: MUC/FRA-JFK 280 EUR", ("MUC", "FRA"), "JFK"),
        ("FRA-JFK-FRA rt 290 EUR", ("FRA",), "JFK"),
    ],
)
def test_flyertalk_code_chains(title, origins, destination):
    signals = parse_feed(_flyertalk_rss(title), "flyertalk")
    if not origins:
        assert signals == []
    else:
        assert (signals[0].origins, signals[0].destination) == (origins, destination)


def test_flyertalk_cp1252_euro_sign_is_read_as_a_price():
    signal = _ft("EI: HAM-MIA from \x80105 rt")
    assert signal.price == 105.0


def test_flyertalk_titles_from_the_live_forum_that_are_not_german_departures_are_dropped():
    titles = [
        "AC/LX: LCY-ZRH-ATH-YYZ-YUL-LGA rt GBP 708 14392 BIS",
        "Hawaii &gt; Europe,  320$ OW [multiple airports, *A, OW, ST]",
        "PR: LAX/SFO to HKG/SGN, sub-$200, o/w",
        "LH: BOG-ZRH OW Premium Economy 384 \x80:",
    ]
    assert parse_feed(_flyertalk_rss(*titles), "flyertalk") == []


# --- Secret Flying (English "X to Y for only €N" titles) -----------------------


def _sf_item(title, *, pub="Thu, 24 Sep 2026 15:12:01 +0000", link=None, cats=("Flight", "Depart Germany")):
    slug = re.sub(r"\W+", "-", title.lower())[:40]
    cat_xml = "".join(f"<category><![CDATA[{c}]]></category>" for c in cats)
    return (
        f"<item><title>{title}</title><link>{link or f'https://www.secretflying.com/posts/{slug}/'}</link>"
        f"<pubDate>{pub}</pubDate>{cat_xml}"
        f"<description><![CDATA[Cheap flights: {title}.]]></description></item>"
    )


def _sf(title, **kw) -> DealSignal:
    signals = parse_feed(_rss(_sf_item(title, **kw)), "secretflying")
    assert len(signals) == 1, signals
    return signals[0]


def test_secretflying_hot_nonstop_from_to_title():
    signal = _sf("HOT: Non-stop from Frankfurt to New York for only €280 roundtrip")

    assert signal.origins == ("FRA",)
    assert (signal.destination, signal.destination_iata) == ("New York", "JFK")
    assert signal.price == 280.0
    assert not signal.is_tier_1  # 280 > the 250 long-haul bar, no keyword


def test_secretflying_crazy_error_fare_title():
    signal = _sf("CRAZY ERROR FARE: Munich to Tokyo from only €310")

    assert signal.origins == ("MUC",)
    assert (signal.destination, signal.destination_iata) == ("Tokyo", "TYO")
    assert signal.price == 310.0
    assert signal.tier_1_reasons == ("keyword:error",)


def test_secretflying_city_country_form():
    signal = _sf("Frankfurt, Germany to Bangkok, Thailand for only €399 roundtrip")

    assert signal.origins == ("FRA",)
    assert signal.destination == "Bangkok, Thailand" and signal.destination_iata == "BKK"


def test_secretflying_several_german_origins_with_or():
    signal = _sf("Non-stop from Hamburg or Berlin to Miami for only €240 roundtrip")

    assert signal.origins == ("HAM", "BER")
    assert signal.destination_iata == "MIA"
    assert signal.tier_1_reasons == ("price<=250:long-haul",)


@pytest.mark.parametrize(
    "title, origin, iata",
    [
        ("Düsseldorf to Lisbon for only €39 roundtrip", "DUS", "LIS"),
        ("Dusseldorf to Barcelona for only €35 roundtrip", "DUS", "BCN"),
        ("SUMMER: Non-stop from Munich to Palma de Mallorca for only €89", "MUC", "PMI"),
        ("Berlin, Germany to Rome, Italy for only €29 roundtrip", "BER", "FCO"),
    ],
)
def test_secretflying_european_routes(title, origin, iata):
    signal = _sf(title)
    assert signal.origins == (origin,) and signal.destination_iata == iata


def test_secretflying_dollar_and_pound_prices_are_ignored():
    signal = _sf("Non-stop from Düsseldorf to Toronto for only $250 roundtrip")

    assert signal.origins == ("DUS",) and signal.destination_iata == "YYZ"
    assert signal.price is None and not signal.is_tier_1


@pytest.mark.parametrize(
    "title",
    [
        "London, UK to Kuala Lumpur, Malaysia for only £333 roundtrip",
        "Non-stop from Dublin or Shannon, Ireland to US cities from only €366 roundtrip",
        "New York to Berlin, Germany for only $359 roundtrip",  # Berlin is the destination
        "Chicago to Frankfurt for only $365 roundtrip",
        "Non-stop from Washington DC to Vancouver, Canada for only $283 roundtrip",
    ],
)
def test_secretflying_non_german_departures_are_dropped(title):
    assert parse_feed(_rss(_sf_item(title)), "secretflying") == []


def test_secretflying_real_feed_shape_with_categories_and_content_encoded():
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0"><channel><title>Secret Flying</title>'
        "<item><title>Frankfurt to Cancun, Mexico for only €295 roundtrip</title>"
        "<link>https://www.secretflying.com/posts/frankfurt-cancun/</link>"
        "<pubDate>Thu, 24 Sep 2026 15:12:01 +0000</pubDate><dc:creator><![CDATA[Admin]]></dc:creator>"
        "<category><![CDATA[1 Stop]]></category><category><![CDATA[Depart Germany]]></category>"
        '<guid isPermaLink="false">http://www.secretflying.com/?p=1</guid>'
        "<description><![CDATA[Cheap flights from Frankfurt to Cancun for only €295 roundtrip.]]></description>"
        "<content:encoded><![CDATA[<p>Cheap flights ... travel dates Oct-Nov</p>]]></content:encoded></item>"
        "</channel></rss>"
    )
    (signal,) = parse_feed(xml, "secretflying")

    assert (signal.origins, signal.destination_iata, signal.price) == (("FRA",), "CUN", 295.0)


# --- mirror chains / silent failures -------------------------------------------


class _MapSession:
    """Fake HTTP: url -> _Resp or Exception; anything else is a 404."""

    def __init__(self, by_url):
        self.by_url, self.calls = by_url, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        result = self.by_url.get(url, _Resp(404, ""))
        if isinstance(result, Exception):
            raise result
        return result


_GOOD_SF = _rss(_sf_item("CRAZY ERROR FARE: Munich to Tokyo from only €310"))
_NOW = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)


def test_first_working_url_of_a_chain_is_used_and_later_ones_are_not_requested(capsys):
    session = _MapSession({"https://official": _Resp(403, "cf"), "https://mirror": _Resp(200, _GOOD_SF), "https://third": _Resp(200, _GOOD_SF)})

    signals = scan_feeds({"secretflying": ("https://official", "https://mirror", "https://third")}, now=_NOW, session=session)

    assert [s.destination_iata for s in signals] == ["TYO"]
    assert session.calls == ["https://official", "https://mirror"]
    assert "403" not in capsys.readouterr().out  # fallback failures are silent


@pytest.mark.parametrize(
    "failure",
    [_Resp(503, "down"), requests.exceptions.Timeout(), requests.exceptions.ConnectionError("boom"), _Resp(200, "<html>Just a moment...</html>")],
)
def test_any_third_party_failure_falls_through_to_the_next_mirror(failure):
    session = _MapSession({"https://first": failure, "https://second": _Resp(200, _GOOD_SF)})

    signals = scan_feeds({"secretflying": ("https://first", "https://second")}, now=_NOW, session=session)

    assert len(signals) == 1


def test_all_mirrors_down_skips_the_source_without_raising(capsys):
    session = _MapSession({"https://a": requests.exceptions.Timeout(), "https://b": _Resp(500, "x")})
    other = _rss(_item("Rom ab Hamburg ab 25€", link="https://td/1"))
    session.by_url["https://td"] = _Resp(200, other)

    signals = scan_feeds(
        {"secretflying": ("https://a", "https://b"), "travel-dealz": "https://td"}, now=_NOW, session=session
    )

    assert [s.source for s in signals] == ["travel-dealz"]  # the run goes on
    out = capsys.readouterr().out
    assert out.count("secretflying") == 1 and "übersprungen" in out  # one summary line, no per-URL noise


def test_default_chain_for_secretflying_ends_with_the_open_feedburner_feed():
    from trip_hunter.engine.feed_sensor import _default_sources

    chain = _default_sources()["secretflying"]
    assert chain[0] == "https://www.secretflying.com/feed/"
    assert chain[-1] == "https://feeds.feedburner.com/SecretFlying"
    assert "rsshub.app" not in " ".join(chain)


def test_env_mirror_is_tried_first(monkeypatch):
    from trip_hunter.engine.feed_sensor import _default_sources

    monkeypatch.setenv("TRIP_HUNTER_FEED_MIRROR_SECRETFLYING", "https://my-rsshub.example/sf")
    assert _default_sources()["secretflying"][0] == "https://my-rsshub.example/sf"
    monkeypatch.setenv("TRIP_HUNTER_FEED_MIRROR_SECRETFLYING", "  ")
    assert _default_sources()["secretflying"][0] == "https://www.secretflying.com/feed/"


def test_default_scan_reaches_a_configured_mirror_when_the_official_feed_is_blocked(monkeypatch):
    monkeypatch.setenv("TRIP_HUNTER_FEED_MIRROR_SECRETFLYING", "https://my-rsshub.example/sf")
    session = _MapSession({"https://my-rsshub.example/sf": _Resp(200, _GOOD_SF)})

    signals = scan_feeds(now=_NOW, session=session)

    assert [(s.source, s.destination_iata) for s in signals] == [("secretflying", "TYO")]


# --- freshness -----------------------------------------------------------------

_STALE_FEEDBURNER = _rss(_sf_item("Frankfurt to New York for only €199 roundtrip", pub="Fri, 11 Jul 2025 15:12:01 +0000"))


def test_stale_mirror_items_are_dropped_by_default():
    session = _MapSession({"https://fb": _Resp(200, _STALE_FEEDBURNER)})
    assert scan_feeds({"secretflying": "https://fb"}, now=_NOW, session=session) == []


def test_freshness_boundary_and_undated_items():
    fresh = _sf_item("Rom ab Hamburg 25€", pub="Mon, 21 Sep 2026 19:00:00 +0000", link="https://x/fresh")  # 2d23h old
    old = _sf_item("Rom ab Berlin 25€", pub="Mon, 21 Sep 2026 17:00:00 +0000", link="https://x/old")  # 3d1h old
    undated = "<item><title>Rom ab München 25€</title><link>https://x/undated</link></item>"
    session = _MapSession({"https://f": _Resp(200, _rss(fresh, old, undated))})

    signals = scan_feeds({"a": "https://f"}, now=_NOW, session=session)

    assert sorted(s.origins[0] for s in signals) == ["HAM", "MUC"]


def test_max_age_none_disables_the_filter():
    session = _MapSession({"https://fb": _Resp(200, _STALE_FEEDBURNER)})
    assert len(scan_feeds({"a": "https://fb"}, max_age=None, session=session)) == 1


def test_custom_max_age():
    session = _MapSession({"https://f": _Resp(200, _rss(_sf_item("Rom ab Hamburg 25€", pub="Thu, 24 Sep 2026 12:00:00 +0000")))})
    assert scan_feeds({"a": "https://f"}, now=_NOW, max_age=timedelta(hours=3), session=session) == []
    assert len(scan_feeds({"a": "https://f"}, now=_NOW, max_age=timedelta(hours=12), session=session)) == 1
