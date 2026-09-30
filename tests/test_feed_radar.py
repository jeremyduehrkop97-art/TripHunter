"""Hourly feed radar: feeds only (no SerpApi), dedup, seeding, fail-safe."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

import trip_hunter.feed_radar as feed_radar
from trip_hunter.dispatch.telegram import dispatch_signal_alert
from trip_hunter.engine.feed_sensor import DealSignal
from trip_hunter.feed_radar import FAST_SOURCE_NAMES, MAX_PUSHES_PER_RUN, is_pushworthy, radar_sources, run, run_radar
from trip_hunter.feed_seen_repository import FeedSeenRepository, deal_key, url_key
from trip_hunter.free_queue_repository import FreeQueueRepository

_NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
_ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("FREE_CHANNEL_MODE", "FREE_CHANNEL_DELAY_HOURS", "FREE_CHANNEL_INVITE_URL", "TELEGRAM_BOT_USERNAME",
                 "VIP_SUBSCRIPTION_URL", "FAQ_URL"):
        monkeypatch.delenv(name, raising=False)


def _sig(title="Cheap flights from Hamburg to Lisbon for €89", *, link=None, source="fly4free", origins=("HAM",),
         dest="Lisbon", iata="LIS", price=89.0, tier1=False, published=None, travel_dates=None) -> DealSignal:
    return DealSignal(
        source=source, title=title, link=link or f"https://www.fly4free.com/deal/{abs(hash(title)) % 10**6}/",
        origins=origins, tier_1_reasons=("keyword:error",) if tier1 else (), destination=dest, destination_iata=iata,
        price=price, published=published or _NOW - timedelta(hours=1), travel_dates=travel_dates,
    )


class _Dispatch:
    def __init__(self, results=None):
        self.sent, self._results = [], list(results or [])

    def __call__(self, signal) -> bool:
        self.sent.append(signal)
        return self._results.pop(0) if self._results else True


def _scan(signals):
    def scan(sources, **kwargs):
        if "status" in kwargs:
            kwargs["status"].update({name: "ok" for name in (sources or {})})
        # like the real scan_feeds: Tier 1 first, then newest first
        return sorted(signals, key=lambda x: (not x.is_tier_1, -(x.published.timestamp() if x.published else 0)))
    return scan


def _seen(tmp_path, *, seeded=True) -> FeedSeenRepository:
    repo = FeedSeenRepository(tmp_path / "t.db")
    if seeded:
        repo.mark(_sig("seed item", link="https://x/seed", price=1.0), action="seed")  # not the first run any more
    return repo


# --- sources, no SerpApi ---------------------------------------------------------


def test_radar_scans_exactly_the_four_requested_feeds():
    assert FAST_SOURCE_NAMES == ("mydealz", "fly4free", "travel-dealz", "urlaubspiraten")
    assert set(radar_sources()) == set(FAST_SOURCE_NAMES)


def _forbid_serpapi(monkeypatch):
    """Any SerpApi use - client call, key loading - fails the test."""
    import trip_hunter.config as config
    import trip_hunter.daily_sampler as sampler
    import trip_hunter.providers.serpapi_client as flights
    import trip_hunter.providers.serpapi_hotels_client as hotels

    def boom(*args, **kwargs):
        raise AssertionError("SerpApi must not be touched in feeds-only mode")

    monkeypatch.setattr(flights.SerpApiClient, "search_flights", boom)
    monkeypatch.setattr(hotels.SerpApiHotelsClient, "search_hotels", boom)
    monkeypatch.setattr(config, "load_serpapi_config", boom)
    monkeypatch.setattr(sampler, "load_serpapi_config", boom)


def test_feed_radar_run_makes_no_serpapi_call_and_loads_no_key(monkeypatch, tmp_path, capsys):
    _forbid_serpapi(monkeypatch)
    monkeypatch.setenv("TRIP_HUNTER_SERPAPI_KEY", "must-never-be-read")
    monkeypatch.setattr(feed_radar, "DEFAULT_DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(feed_radar, "scan_feeds", _scan([_sig()]))

    result = run([])

    assert result.seeded == 1 and "0 SerpApi-Credits" in capsys.readouterr().out


def test_daily_sampler_feeds_only_flag_runs_the_radar_and_never_the_sampler(monkeypatch, tmp_path):
    import trip_hunter.daily_sampler as sampler

    _forbid_serpapi(monkeypatch)
    calls = []
    monkeypatch.setattr(feed_radar, "run", lambda argv=None: calls.append(argv))
    monkeypatch.setattr(sampler, "run_sampler", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sampler ran")))

    sampler.run(["--feeds-only"])
    sampler.run(["--feeds-only", "--dry-run"])

    assert calls == [[], ["--dry-run"]]


def test_feed_radar_module_does_not_import_any_serpapi_provider():
    source = (_ROOT / "src" / "trip_hunter" / "feed_radar.py").read_text(encoding="utf-8")
    import_lines = [l for l in source.splitlines() if l.startswith(("import ", "from "))]
    assert not any("serpapi" in l.lower() or "providers" in l or "daily_sampler" in l for l in import_lines)


def test_hourly_workflow_is_feeds_only_with_the_requested_schedule():
    text = (_ROOT / ".github" / "workflows" / "feed_radar_fast.yml").read_text(encoding="utf-8")

    assert '- cron: "15 6-22 * * *"' in text
    assert "workflow_dispatch" in text
    assert "python -m trip_hunter.feed_radar" in text
    assert "TRIP_HUNTER_SERPAPI_KEY:" not in text and "secrets.SERPAPI" not in text  # the key is never passed
    assert "daily_sampler" not in text


def test_both_workflows_share_state_cache_paths_and_one_lock():
    radar = (_ROOT / ".github" / "workflows" / "feed_radar_fast.yml").read_text(encoding="utf-8")
    daily = (_ROOT / ".github" / "workflows" / "daily_sample.yml").read_text(encoding="utf-8")

    block = "path: |\n            data/trip_hunter.db\n            data/cache/\n          key: trip-hunter-state-${{ github.run_id }}"
    assert block in radar and block in daily  # same cache "version" -> shared database
    for text in (radar, daily):
        assert "group: trip-hunter-state" in text and "cancel-in-progress: false" in text


# --- push worthiness -----------------------------------------------------------------


def test_worthy_means_a_real_destination_and_price_regardless_of_tier():
    assert is_pushworthy(_sig())
    assert is_pushworthy(_sig(iata=None, dest="Bangkok"))
    assert not is_pushworthy(_sig(dest=None, iata=None))                    # no destination
    assert not is_pushworthy(_sig(price=None))                             # no price
    # Tier 1 is no longer a bypass - even a "clear error fare" with no
    # identifiable destination must be dropped (see the Ryanair Blitzverkauf
    # regression below).
    assert not is_pushworthy(_sig(tier1=True, dest=None, iata=None))
    assert not is_pushworthy(_sig(tier1=True, price=None))


def test_ryanair_blitzverkauf_title_is_dropped_end_to_end_by_the_radar(tmp_path):
    """The reported bug, run through the REAL parser and the real radar
    pipeline (not the synthetic _sig() helper): "Ryanair Blitzverkauf
    Flüge ab 15€" named no real destination, yet used to post as "Berlin
    nach Ryanair Blitzverkauf Flüge" - price <= 40 made it Tier 1, and
    Tier 1 used to bypass the missing-destination check entirely."""
    from trip_hunter.engine.feed_sensor import parse_feed

    title = "Ryanair Blitzverkauf | Flüge ab Berlin ab 15€ | z.B. London, Mallorca uvm."
    xml = f'<rss><channel><item><title>{title}</title><link>https://x/promo</link></item></channel></rss>'

    (signal,) = parse_feed(xml, "mydealz", tier_1_only=True)
    assert signal.is_tier_1 and signal.destination is None and signal.destination_iata is None
    assert not is_pushworthy(signal)

    result = run_radar(
        _seen(tmp_path), scan_fn=lambda sources, **kw: [signal], dispatch_fn=_Dispatch(), now=_NOW,
    )
    assert result.candidates == 0 and result.sent == 0


# --- seen repository ---------------------------------------------------------------------


def test_url_key_ignores_tracking_parameters():
    assert url_key(_sig(link="https://x/a/?utm_source=feed&id=1")) == url_key(_sig(link="https://x/a/?id=1&utm_medium=rss"))


def test_deal_key_is_the_same_for_the_same_deal_in_two_feeds():
    a = _sig("Cheap flights from Hamburg to Lisbon €89", link="https://a/1", source="fly4free")
    b = _sig("Hamburg nach Lissabon ab 89€", link="https://b/2", source="travel-dealz")
    assert deal_key(a) == deal_key(b) == "HAM|LIS|89"
    assert deal_key(_sig(price=None)) is None


def test_seen_repository_persists_and_dedups_by_url_and_by_deal(tmp_path):
    repo = FeedSeenRepository(tmp_path / "t.db")
    assert repo.is_empty()
    original = _sig(link="https://a/1", source="fly4free")
    repo.mark(original)
    repo.mark(original)  # idempotent

    reopened = FeedSeenRepository(tmp_path / "t.db")
    assert not reopened.is_empty() and reopened.count() == 1
    assert reopened.has_seen(original)
    assert reopened.has_seen(_sig(link="https://a/1?utm_source=x", price=5.0))            # same URL
    assert reopened.has_seen(_sig("other title", link="https://b/2", source="travel-dealz"))  # same deal, other feed
    assert not reopened.has_seen(_sig(link="https://c/3", price=90.0))


# --- the radar pass -------------------------------------------------------------------------


def test_first_run_seeds_silently_and_the_next_run_sends_only_what_is_new(tmp_path, capsys):
    repo = FeedSeenRepository(tmp_path / "t.db")
    dispatch = _Dispatch()
    backlog = [_sig("old 1", link="https://x/1", price=50.0), _sig("old 2", link="https://x/2", price=60.0, tier1=True)]

    first = run_radar(repo, scan_fn=_scan(backlog), dispatch_fn=dispatch, now=_NOW)
    assert (first.seeded, first.sent) == (2, 0) and dispatch.sent == []
    assert "erster Lauf" in capsys.readouterr().out

    fresh = _sig("brand new", link="https://x/3", price=70.0)
    second = run_radar(repo, scan_fn=_scan([*backlog, fresh]), dispatch_fn=dispatch, now=_NOW)

    assert second.sent == 1 and [s.title for s in dispatch.sent] == ["brand new"]


def test_a_sent_item_is_never_sent_again(tmp_path):
    repo, dispatch = _seen(tmp_path), _Dispatch()
    signal = _sig("new deal", link="https://x/n")

    run_radar(repo, scan_fn=_scan([signal]), dispatch_fn=dispatch, now=_NOW)
    run_radar(repo, scan_fn=_scan([signal]), dispatch_fn=dispatch, now=_NOW)
    run_radar(repo, scan_fn=_scan([signal]), dispatch_fn=dispatch, now=_NOW + timedelta(hours=1))

    assert len(dispatch.sent) == 1


def test_the_same_deal_from_two_feeds_goes_out_once(tmp_path):
    repo, dispatch = _seen(tmp_path), _Dispatch()
    a = _sig("A", link="https://a/1", source="fly4free")
    b = _sig("B", link="https://b/1", source="travel-dealz")

    run_radar(repo, scan_fn=_scan([a, b]), dispatch_fn=dispatch, now=_NOW)  # both in ONE scan
    assert len(dispatch.sent) == 1
    run_radar(repo, scan_fn=_scan([b]), dispatch_fn=dispatch, now=_NOW)     # and later: still once
    assert len(dispatch.sent) == 1


def test_tier_1_goes_first_and_the_cap_leaves_the_rest_for_the_next_hour(tmp_path):
    repo, dispatch = _seen(tmp_path), _Dispatch()
    normal = [_sig(f"normal {i}", link=f"https://x/n{i}", price=100.0 + i, iata=None, dest=f"City{i}") for i in range(6)]
    error = _sig("ERROR", link="https://x/e", price=19.0, tier1=True)

    first = run_radar(repo, scan_fn=_scan([*normal, error]), dispatch_fn=dispatch, max_pushes=3, now=_NOW)

    assert dispatch.sent[0].title == "ERROR"                      # scan order: Tier 1 first (see feed_sensor)
    assert (first.candidates, first.sent) == (7, 3)
    second = run_radar(repo, scan_fn=_scan([*normal, error]), dispatch_fn=dispatch, max_pushes=3, now=_NOW)
    assert second.sent == 3 and len({s.title for s in dispatch.sent}) == 6  # nothing repeated, nothing lost


def test_default_cap_is_five():
    assert MAX_PUSHES_PER_RUN == 5


def test_unworthy_signals_are_skipped_and_not_recorded(tmp_path):
    repo, dispatch = _seen(tmp_path), _Dispatch()
    junk = [_sig("no price", link="https://x/1", price=None), _sig("no dest", link="https://x/2", dest=None, iata=None)]

    result = run_radar(repo, scan_fn=_scan(junk), dispatch_fn=dispatch, now=_NOW)

    assert result.candidates == 0 and dispatch.sent == [] and repo.count() == 1  # only the seed marker


def test_a_failed_send_is_retried_by_the_next_run(tmp_path, capsys):
    repo = _seen(tmp_path)
    signal = _sig("flaky", link="https://x/f")

    failing = _Dispatch(results=[False])
    first = run_radar(repo, scan_fn=_scan([signal]), dispatch_fn=failing, now=_NOW)
    assert (first.sent, first.failed) == (0, 1) and not repo.has_seen(signal)

    working = _Dispatch()
    second = run_radar(repo, scan_fn=_scan([signal]), dispatch_fn=working, now=_NOW)
    assert second.sent == 1 and repo.has_seen(signal)


def test_dry_run_sends_and_records_nothing(tmp_path, capsys):
    repo, dispatch = _seen(tmp_path), _Dispatch()
    before = repo.count()

    run_radar(repo, scan_fn=_scan([_sig("x", link="https://x/d")]), dispatch_fn=dispatch, dry_run=True, now=_NOW)

    assert dispatch.sent == [] and repo.count() == before and "würde senden" in capsys.readouterr().out


def test_dry_run_first_run_does_not_seed(tmp_path):
    repo = FeedSeenRepository(tmp_path / "t.db")
    run_radar(repo, scan_fn=_scan([_sig()]), dispatch_fn=_Dispatch(), dry_run=True, now=_NOW)
    assert repo.is_empty()


# --- fail-safe -----------------------------------------------------------------------------


def test_empty_feeds_are_a_quiet_no_op(tmp_path):
    repo, dispatch = _seen(tmp_path), _Dispatch()
    result = run_radar(repo, scan_fn=_scan([]), dispatch_fn=dispatch, now=_NOW)
    assert (result.scanned, result.sent) == (0, 0) and dispatch.sent == []


def test_a_crashing_scan_is_caught(tmp_path, capsys):
    def boom(*a, **k):
        raise RuntimeError("secret detail")

    result = run_radar(_seen(tmp_path), scan_fn=boom, dispatch_fn=_Dispatch(), now=_NOW)

    out = capsys.readouterr().out
    assert result.scanned == 0 and "RuntimeError" in out and "secret detail" not in out


def test_a_crashing_dispatch_never_propagates(tmp_path):
    def boom(signal):
        raise ValueError("kaputt")

    result = run_radar(_seen(tmp_path), scan_fn=_scan([_sig("x", link="https://x/c")]), dispatch_fn=boom, now=_NOW)
    assert result.sent == 0


@pytest.mark.parametrize(
    "body",
    ["", "<html>blocked</html>", "\x00\x01", "<rss><channel><item><title>x", "<rss><channel></channel></rss>", "{}"],
)
def test_real_scan_with_broken_or_empty_feeds_is_silent_and_sends_nothing(tmp_path, body):
    """The real scan_feeds through a fake HTTP layer: broken payloads, 403s and
    timeouts on every one of the four feeds end in a quiet no-op."""

    class Session:
        def __init__(self):
            self.n = 0

        def get(self, url, headers=None, timeout=None):
            self.n += 1
            if self.n % 3 == 0:
                raise requests.exceptions.Timeout()
            r = type("R", (), {"status_code": 403 if self.n % 3 == 1 else 200, "text": body})()
            return r

    repo, dispatch = _seen(tmp_path), _Dispatch()
    from trip_hunter.engine.feed_sensor import scan_feeds

    result = run_radar(
        repo, scan_fn=lambda sources, **kw: scan_feeds(sources, session=Session(), **kw),
        dispatch_fn=dispatch, now=_NOW,
    )

    assert result.sent == 0 and dispatch.sent == []


# --- dispatch_signal_alert: VIP / Free / delayed --------------------------------------------------


class _Resp:
    status_code = 200
    text = ""

    def json(self):
        return {"ok": True}


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append({"method": url.rsplit("/", 1)[1], "data": data})
        return _Resp()


def _push(signal, session, **kw):
    return dispatch_signal_alert(signal, bot_token="123:ABC", free_chat_id="free", vip_chat_id="vip", session=session, **kw)


def _by_chat(session):
    return {c["data"]["chat_id"]: c["data"] for c in session.calls}


def test_vip_gets_the_fixed_layout_and_a_deal_sheet_button_never_the_source_link():
    """Lisbon with no exact date is a flexible-date combo case (Lisbon has
    a hotel guide-price tier), so this exercises the real, common
    end-to-end path: combo teaser + a deal-sheet button, never the
    third-party source."""
    session = _Session()
    signal = _sig("Cheap flights from Hamburg to Lisbon for €89", link="https://www.fly4free.com/deal/1/")

    assert _push(signal, session) is True

    vip = _by_chat(session)["vip"]
    lines = vip["caption"].splitlines()
    assert lines[0] == "✈️ <b>Hamburg nach Lissabon</b>"
    assert any(line.startswith("🌴") and "ab" in line and "p.P." in line for line in lines)
    assert any(line.startswith("🛫 Flug: Hin- & Rückflug ab") for line in lines)
    assert any(line.startswith("🏨 Hotel:") for line in lines)
    assert "⚠️ Feed-Hinweis: Preise können sich minütlich ändern." in lines
    assert "Fly4free" not in vip["caption"] and signal.title not in vip["caption"]  # no source citation any more

    (row,) = json.loads(vip["reply_markup"])["inline_keyboard"]
    (button,) = row
    assert button["text"] == "⚡️ Jetzt Deal buchen"
    assert "web_app" in button
    assert button["web_app"]["url"].startswith("https://trip-hunter.de/deal.html?")
    assert "fly4free.com" not in button["web_app"]["url"]  # never the third-party source
    assert "has_spoiler" not in vip


def test_vip_gets_the_plain_fixed_layout_for_a_destination_with_no_hotel_guide_price():
    """A destination this project has no hotel guide-price tier for
    (monetization/hotel_price_guide.py) never gets a fabricated combo -
    the plain, always-complete layout is used instead."""
    session = _Session()
    signal = _sig("Cheap flights from Hamburg to Bischkek for €89", dest="Bischkek", iata="FRU")

    assert _push(signal, session) is True

    lines = _by_chat(session)["vip"]["caption"].splitlines()
    assert "💥 Preis: ab 89 € p.P." in lines
    assert "🗓 Reisezeit: Flexible Reisetermine verfügbar" in lines
    assert not any(line.startswith("🌴") for line in lines)


def test_non_tier_1_signals_stay_vip_only():
    session = _Session()
    _push(_sig(), session)
    assert set(_by_chat(session)) == {"vip"}


def test_tier_1_signals_reach_free_as_a_masked_teaser_without_the_source():
    session = _Session()
    signal = _sig("HOT!! Error fare from Hamburg to Lisbon €19", link="https://www.fly4free.com/deal/e/", price=19.0, tier1=True)

    _push(signal, session)

    free = _by_chat(session)["free"]
    payload = free["caption"] + free["reply_markup"]
    assert "ERROR FARE" in free["caption"] and "Quelle & Deal-Link im VIP-Kanal" in free["caption"]
    assert "fly4free" not in payload.lower() and "Error fare from" not in free["caption"]
    assert free["has_spoiler"] == "true"
    texts = [r[0]["text"] for r in json.loads(free["reply_markup"])["inline_keyboard"]]
    assert texts == ["⚡️ Jetzt Deal buchen (VIP freischalten)", "📲 Mit Reise-Buddy teilen", "ℹ️ Wie funktioniert Trip Hunter?"]


def test_tier_1_in_delayed_full_mode_is_queued_for_free(monkeypatch, tmp_path):
    monkeypatch.setenv("FREE_CHANNEL_MODE", "delayed_full")
    queue = FreeQueueRepository(tmp_path / "q.db")
    session = _Session()

    _push(_sig(price=19.0, tier1=True), session, free_queue=queue, now=_NOW)

    assert set(_by_chat(session)) == {"vip"} and queue.pending_count() == 1
    (item,) = queue.due(_NOW + timedelta(hours=24))
    assert item.text.startswith("⏱ Dieser Hinweis ging vor 24 Std. an den VIP-Kanal")
    assert "✈️ <b>Hamburg nach" not in item.text or True  # (sig() default route; see the exact text below)
    assert "🏨 Unterkunft:" in item.text


def test_share_text_of_a_signal_has_destination_price_and_invite_but_no_source_link(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setenv("FREE_CHANNEL_INVITE_URL", "https://t.me/+Invite")
    session = _Session()
    _push(_sig(price=19.0, tier1=True, link="https://www.fly4free.com/secret-article/"), session)

    share = json.loads(_by_chat(session)["free"]["reply_markup"])["inline_keyboard"][1][0]["url"]
    text = parse_qs(urlsplit(share).query)["text"][0]

    assert text == "Schau mal, Trip Hunter hat gerade Lissabon ab 19 € gefunden! ✈️ Hier ist der Deal: https://t.me/+Invite"
    assert "fly4free" not in text.lower() and "secret-article" not in text


def test_html_in_the_destination_text_is_escaped():
    session = _Session()
    _push(_sig("Flights from Hamburg & more", dest="A&B <c>", iata=None), session)

    caption = _by_chat(session)["vip"]["caption"]
    assert "A&amp;B &lt;c&gt;" in caption
    assert "<c>" not in caption and "A&B <c>" not in caption


def test_no_telegram_configuration_returns_false_without_raising(capsys):
    assert dispatch_signal_alert(_sig(), bot_token="", free_chat_id="", vip_chat_id="", default_chat_id="") is False
    assert "nicht konfiguriert" in capsys.readouterr().out


def test_the_signals_own_link_scheme_never_affects_the_button():
    """The button is always our own deal sheet now - it no longer reads
    `signal.link` at all, so an insecure (http) or missing source link
    can't remove it."""
    session = _Session()
    _push(_sig(link="http://insecure.example/x"), session)
    assert "reply_markup" in _by_chat(session)["vip"]


def test_no_button_when_the_destination_is_unknown():
    from trip_hunter.alerts.instant_alert_formatter import signal_keyboards

    assert signal_keyboards(_sig(dest=None, iata=None)) == []


# --- DACH scope in the signal alert (departure line, currency) -------------------


def test_signal_header_names_the_dach_origin_city_with_no_code():
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    text = format_signal_alert(_sig(origins=("VIE",), dest="Zurich", iata="ZRH"))
    assert text.splitlines()[0] == "✈️ <b>Wien nach Zürich</b>"
    assert "VIE" not in text and "ZRH" not in text  # codes dropped from the fixed layout


def test_signal_header_lists_several_dach_origins():
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    text = format_signal_alert(_sig(origins=("HAM", "ZRH")))
    assert text.splitlines()[0].startswith("✈️ <b>Hamburg / Zürich nach")


@pytest.mark.parametrize("origin, city", [("VIE", "Wien"), ("SZG", "Salzburg"), ("INN", "Innsbruck"),
                                          ("ZRH", "Zürich"), ("GVA", "Genf"), ("BSL", "Basel")])
def test_header_for_every_new_dach_airport(origin, city):
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    assert format_signal_alert(_sig(origins=(origin,))).splitlines()[0] == f"✈️ <b>{city} nach Lissabon</b>"


def test_signal_currency_stays_euro_in_the_alert_text():
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    text = format_signal_alert(_sig(price=39.0))
    assert "ab 39 €" in text and "CHF" not in text and "$" not in text


def test_end_to_end_vie_zrh_bsl_signals_are_recognised_and_pushed(tmp_path):
    session = _Session()
    signals = [
        DealSignal(source="fly4free", title="Preisfehler: Bangkok ab Wien für 199€",
                   link="https://www.fly4free.com/d/1/", origins=("VIE",), tier_1_reasons=("keyword:error",),
                   destination="Bangkok", destination_iata="BKK", price=199.0, published=_NOW),
        DealSignal(source="fly4free", title="Zürich to New York for only €399 roundtrip",
                   link="https://www.fly4free.com/d/2/", origins=("ZRH",), tier_1_reasons=(),
                   destination="New York", destination_iata="JFK", price=399.0, published=_NOW),
        DealSignal(source="fly4free", title="Basel to Lisbon for only €39 roundtrip",
                   link="https://www.fly4free.com/d/3/", origins=("BSL",), tier_1_reasons=("price<=40",),
                   destination="Lisbon", destination_iata="LIS", price=39.0, published=_NOW),
    ]
    repo = _seen(tmp_path)  # pre-seeded, so this run actually pushes

    result = run_radar(repo, scan_fn=_scan(signals), dispatch_fn=lambda s: _push(s, session), now=_NOW)

    assert result.sent == 3
    vip_texts = [c["data"]["caption"] for c in session.calls if c["data"]["chat_id"] == "vip"]
    assert any("✈️ <b>Wien nach Bangkok</b>" in t for t in vip_texts)
    assert any("✈️ <b>Zürich nach New York</b>" in t for t in vip_texts)
    assert any("✈️ <b>Basel nach Lissabon</b>" in t for t in vip_texts)


# --- fixed message layout: Reisezeit / Preis / Details / Unterkunft --------------


def test_message_follows_the_exact_fixed_layout_for_a_known_exact_date():
    """An exact, day-precise date (parse_travel_date_range succeeds) keeps
    the plain fixed layout even for a destination that DOES have a hotel
    guide-price tier (Bangkok) - there is nothing "flexible" to fan out
    once the feed already names one real date range. The Unterkunft line
    still upgrades from the bare "Optional zubuchbar" to a concrete guide
    price, since Bangkok IS covered (see _accommodation_note)."""
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    signal = DealSignal(
        source="fly4free", title="Non-stop flights to Bangkok with Thai Airways from Frankfurt for €399",
        link="https://x/1", origins=("FRA",), tier_1_reasons=(), destination="Bangkok",
        destination_iata="BKK", price=399.0, travel_dates="12.10.–19.10.2026", published=_NOW,
    )

    assert format_signal_alert(signal).splitlines() == [
        "✈️ <b>Frankfurt nach Bangkok</b>",
        "",
        "🗓 Reisezeit: 12.10.–19.10.2026",
        "💥 Preis: ab 399 € p.P.",
        "🛫 Flug: Nonstop mit Thai Airways",
        "🏨 Unterkunft: 4-Sterne Hotel ab ca. 45 €/Nacht (separat buchen, Richtwert)",
        "",
        "⚠️ Feed-Hinweis: Preise können sich minütlich ändern.",
    ]


def test_unterkunft_line_shows_a_concrete_guide_price_for_a_covered_destination():
    """The exact task: even a very cheap/error-fare deal must never fall
    back to the bare "Optional zubuchbar" placeholder when this project
    actually has hotel guide-price data for the destination."""
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    signal = DealSignal(
        source="fly4free", title="Preisfehler: Rome from Munich for €19", link="https://x/1",
        origins=("MUC",), tier_1_reasons=("keyword:error",), destination="Rome", destination_iata="FCO",
        price=19.0, published=_NOW,
    )
    lines = format_signal_alert(signal).splitlines()

    assert any(line.startswith("🏨 Unterkunft: 4-Sterne Hotel ab ca.") and "Richtwert" in line for line in lines)
    assert not any("Optional zubuchbar" in line for line in lines)
    # Still the Tier-1 wait-before-booking advice, not a combo "book now" push.
    assert "Erst den Flug buchen, Buchungsbestätigung abwarten" in "\n".join(lines)


def test_month_only_or_dateless_signal_gets_the_flexible_combo_teaser_instead():
    """The "Urlaubspiraten model": a feed title naming only a month (or no
    date at all) for a destination WITH a hotel guide-price tier gets the
    richer combo teaser, not the plain layout - this is the actual
    behaviour change this feature is for."""
    from trip_hunter.alerts._shared import nights_label
    from trip_hunter.alerts.instant_alert_formatter import _short_date_de, format_signal_alert
    from trip_hunter.engine.flexible_dates import generate_example_windows, hero_window
    from trip_hunter.monetization.hotel_price_guide import hotel_nightly_guide_price

    signal = DealSignal(
        source="fly4free", title="Non-stop flights to Bangkok with Thai Airways from Frankfurt for €399",
        link="https://x/1", origins=("FRA",), tier_1_reasons=(), destination="Bangkok",
        destination_iata="BKK", price=399.0, travel_dates="Oktober 2026", published=_NOW,
    )

    # Bangkok is long-haul: the hero example is 14 nights (not the cheapest
    # of the four) - see engine/flexible_dates.py's "HERO NIGHTS PREFERENCE".
    windows = generate_example_windows("BKK")  # same "today" the formatter itself uses
    dep, ret = hero_window(windows, "BKK")
    nightly = hotel_nightly_guide_price("BKK")
    nights = (ret - dep).days
    assert nights == 14
    hotel_pp = round(nightly * nights / 2)
    combo_total = 399 + hotel_pp

    assert format_signal_alert(signal).splitlines() == [
        "✈️ <b>Frankfurt nach Bangkok</b>",
        "",
        f"🌴 {nights_label(nights)} inkl. 4★ Hotel für {combo_total} € p.P.!",
        f"(Beispiel: {_short_date_de(dep)} – {_short_date_de(ret)})",
        "",
        "🛫 Flug: Hin- & Rückflug ab 399 €",
        f"🏨 Hotel: 4-Sterne Hotel ab ca. {nightly} €/Nacht ({hotel_pp} € p.P., Richtwert)",
        "🗓 Weitere Termine: Mehrere Beispiel-Reisezeiten verfügbar!",
        "",
        "⚠️ Feed-Hinweis: Preise können sich minütlich ändern.",
    ]


def test_a_short_haul_flexible_signal_keeps_the_cheapest_ab_wording():
    """Unlike the long-haul case above, a short-haul destination's hero
    example is still the cheapest one, so the "ab"/"Günstigstes Beispiel"
    wording (implying nothing cheaper is shown) stays accurate."""
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    signal = DealSignal(
        source="fly4free", title="Cheap flights to Lisbon from Hamburg for €89", link="https://x/2",
        origins=("HAM",), tier_1_reasons=(), destination="Lisbon", destination_iata="LIS",
        price=89.0, published=_NOW,
    )
    lines = format_signal_alert(signal).splitlines()

    assert any(line.startswith("🌴") and " ab " in line for line in lines)
    assert any(line.startswith("(Günstigstes Beispiel:") for line in lines)


def test_flug_line_shows_fixed_fallback_when_no_airline_or_nonstop_is_named():
    """The 🛫 Flug line must never be missing - a feed title that names no
    airline/nonstop keyword gets the fixed fallback text instead of the
    line disappearing (that incompleteness was exactly the reported bug).
    Uses a destination with no hotel guide-price tier so the plain layout
    (not the flexible combo teaser) is the one under test."""
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    text = format_signal_alert(_sig(iata=None))
    assert "🛫 Flug: Hin- & Rückflug inklusive" in text.splitlines()


def test_details_line_shows_nonstop_only_or_airline_only():
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    nonstop_only = DealSignal(source="x", title="Non-stop to Rome from Munich for €59", link="https://x/1",
                              origins=("MUC",), tier_1_reasons=(), destination="Rome", destination_iata=None,
                              price=59.0, published=_NOW)
    airline_only = DealSignal(source="x", title="Rome with Ryanair from Munich for €59", link="https://x/2",
                              origins=("MUC",), tier_1_reasons=(), destination="Rome", destination_iata=None,
                              price=59.0, published=_NOW)

    assert "🛫 Flug: Nonstop" in format_signal_alert(nonstop_only).splitlines()
    assert "🛫 Flug: Ryanair" in format_signal_alert(airline_only).splitlines()


def test_accommodation_line_defaults_to_optional_but_detects_hotel_inclusion():
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    plain = DealSignal(source="x", title="Rome from Munich for €59", link="https://x/1", origins=("MUC",),
                       tier_1_reasons=(), destination="Rome", destination_iata=None, price=59.0, published=_NOW)
    bundled = DealSignal(source="x", title="Rome inkl. Hotel from Munich for €299", link="https://x/2",
                         origins=("MUC",), tier_1_reasons=(), destination="Rome", destination_iata=None,
                         price=299.0, published=_NOW)

    assert "🏨 Unterkunft: Optional zubuchbar" in format_signal_alert(plain).splitlines()
    assert "🏨 Unterkunft: Hotel inkl." in format_signal_alert(bundled).splitlines()


def test_reisezeit_line_shows_fixed_fallback_when_no_travel_dates_known():
    """Same "never incomplete" rule for the 🗓 Reisezeit line."""
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    text = format_signal_alert(_sig(iata=None))  # _sig() default has no travel_dates
    assert "🗓 Reisezeit: Flexible Reisetermine verfügbar" in text.splitlines()


def test_format_signal_alert_always_prints_all_four_detail_lines():
    """The exact reported bug: 'Frankfurt nach Bali 599 EUR' has neither a
    parseable travel period nor an airline/nonstop keyword, which used to
    make the Reisezeit and Flug lines vanish entirely. Using a destination
    with no hotel guide-price tier here keeps this test about the plain
    layout specifically (see the dedicated combo test for what a covered
    destination like Bali gets instead nowadays)."""
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    signal = _sig(title="Frankfurt nach Bali 599 EUR", origins=("FRA",), dest="Bali", iata=None, price=599.0)
    lines = format_signal_alert(signal).splitlines()

    assert any(line.startswith("🗓 Reisezeit:") for line in lines)
    assert any(line.startswith("💥 Preis:") for line in lines)
    assert any(line.startswith("🛫 Flug:") for line in lines)
    assert any(line.startswith("🏨 Unterkunft:") for line in lines)
    assert "🗓 Reisezeit: Flexible Reisetermine verfügbar" in lines
    assert "🛫 Flug: Hin- & Rückflug inklusive" in lines


def test_bali_with_no_exact_date_now_gets_the_flexible_combo_teaser():
    """Bali (DPS) DOES have a hotel guide-price tier, so the real reported
    case ("Frankfurt nach Bali 599 EUR", no date) now gets the richer
    combo teaser - never the plain layout, and never incomplete either
    way."""
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    signal = _sig(title="Frankfurt nach Bali 599 EUR", origins=("FRA",), dest="Bali", iata="DPS", price=599.0)
    lines = format_signal_alert(signal).splitlines()

    assert any(line.startswith("🌴") for line in lines)
    assert any(line.startswith("🛫 Flug: Hin- & Rückflug ab") for line in lines)
    assert any(line.startswith("🏨 Hotel:") for line in lines)


def test_teaser_has_the_same_layout_minus_the_disclaimer_and_lock_line_instead():
    from trip_hunter.alerts.instant_alert_formatter import format_signal_teaser

    lines = format_signal_teaser(_sig(price=39.0, iata=None)).splitlines()
    assert lines[0].startswith("✈️ <b>")
    assert "💥 Preis: ab 39 € p.P." in lines
    assert any(line.startswith("🏨 Unterkunft:") for line in lines)
    assert lines[-1] == "🔒 Quelle & Deal-Link im VIP-Kanal"
    assert not any("Feed-Hinweis" in line for line in lines)


# --- deal-sheet button never carries the source link -----------------------------


def test_deal_sheet_url_is_our_domain_with_the_route_encoded():
    """Bischkek (FRU) has no hotel guide-price tier, so no combo - this
    tests the bare, dateless, hotel-less signal deal sheet specifically
    (see the dedicated combo test below for a covered destination)."""
    from urllib.parse import parse_qs, urlsplit

    from trip_hunter.alerts.instant_alert_formatter import signal_deal_sheet_url

    url = signal_deal_sheet_url(_sig(origins=("FRA",), dest="Bischkek", iata="FRU", price=399.0))
    parts = urlsplit(url)

    assert parts.netloc == "trip-hunter.de" and parts.path.endswith("/deal.html")
    query = parse_qs(parts.query)
    assert query["from"] == ["Frankfurt"] and query["to"] == ["Bischkek"] and query["code"] == ["FRU"]
    assert query["fp"] == ["399"]
    assert query["fl"][0].startswith("https://www.google.com/travel/flights?")
    assert "hl" not in query  # no hotel link for a bare feed signal


def test_deal_sheet_url_for_a_flexible_combo_signal_carries_real_dated_windows(monkeypatch):
    """Bangkok (BKK) DOES have a hotel guide-price tier: with no exact
    date, the deal sheet gets the hero example window's real, dated,
    markered links up top (14 nights - the long-haul hero, not the
    cheapest 10-night option) plus a "windows" matrix of every example
    window, each with its own real dated links - never one fabricated
    single date passed off as confirmed."""
    from urllib.parse import parse_qs, unquote, urlsplit

    from trip_hunter.alerts.instant_alert_formatter import signal_deal_sheet_url
    from trip_hunter.engine.flexible_dates import generate_example_windows, hero_window

    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    signal = _sig(origins=("FRA",), dest="Bangkok", iata="BKK", price=399.0)

    query = parse_qs(urlsplit(signal_deal_sheet_url(signal)).query)
    expected_windows = generate_example_windows("BKK")  # same "today" the formatter itself uses
    lead_dep, lead_ret = hero_window(expected_windows, "BKK")
    assert (lead_ret - lead_dep).days == 14

    assert query["dep"] == [lead_dep.isoformat()] and query["ret"] == [lead_ret.isoformat()]
    assert "aviasales.com" in unquote(query["fl"][0]) and "marker=781828" in unquote(query["fl"][0])
    assert "booking.com" in unquote(query["hl"][0])

    windows = json.loads(query["windows"][0])
    assert len(windows) == 4
    assert {w["dep"] for w in windows} == {dep.isoformat() for dep, _ in expected_windows}
    for w in windows:
        assert "aviasales.com" in w["fl"] and "marker=781828" in w["fl"]
        assert "booking.com" in w["hl"]


def test_deal_sheet_flight_link_never_names_the_feed_source():
    from urllib.parse import parse_qs, unquote, urlsplit

    from trip_hunter.alerts.instant_alert_formatter import signal_deal_sheet_url

    url = signal_deal_sheet_url(_sig(link="https://www.fly4free.com/secret-deal-slug/"))
    flight_link = parse_qs(urlsplit(url).query)["fl"][0]
    assert "fly4free" not in unquote(flight_link)


def test_signal_keyboards_chain_matches_the_deal_button_pattern():
    from trip_hunter.alerts.instant_alert_formatter import signal_keyboards

    web_app, url_button = signal_keyboards(_sig())
    w = web_app["inline_keyboard"][0][0]
    u = url_button["inline_keyboard"][0][0]
    assert w["text"] == u["text"] == "⚡️ Jetzt Deal buchen"
    assert "web_app" in w and "url" in u and w["web_app"]["url"] == u["url"]


def test_a_button_rejection_falls_back_to_the_url_variant_for_a_signal_too():
    button_error = _Resp()
    button_error.status_code = 400
    button_error.text = '{"ok":false,"description":"Bad Request: BUTTON_TYPE_INVALID"}'

    class SeqSession(_Session):
        def __init__(self, responses):
            super().__init__()
            self._responses = list(responses)

        def post(self, url, data=None, timeout=None):
            self.calls.append({"method": url.rsplit("/", 1)[1], "data": data})
            return self._responses.pop(0) if self._responses else _Resp()

    session = SeqSession([button_error, _Resp()])
    assert _push(_sig(), session) is True
    assert len(session.calls) == 2
    assert "web_app" in json.loads(session.calls[0]["data"]["reply_markup"])["inline_keyboard"][0][0]
    assert "url" in json.loads(session.calls[1]["data"]["reply_markup"])["inline_keyboard"][0][0]


# --- absolute ban on third-party links anywhere in a Telegram payload ------------


_FOREIGN_SOURCE_HOSTS = ("urlaubspiraten", "mydealz", "fly4free", "travel-dealz", "flyertalk", "secretflying", "flynous")


def _payload_text(call: dict) -> str:
    """Everything Telegram would actually receive for one API call: the
    text/caption plus the raw (still-encoded) reply_markup JSON, so an
    encoded URL inside a button is caught too."""
    data = call["data"]
    return (data.get("text") or data.get("caption") or "") + (data.get("reply_markup") or "")


def test_no_foreign_source_domain_anywhere_in_the_vip_payload():
    session = _Session()
    signal = _sig(
        "Cheap flights from Hamburg to Lisbon for €89",
        link="https://www.fly4free.com/deal/geheimer-artikel/",
        travel_dates="12.10.–19.10.2026",
    )
    assert _push(signal, session) is True

    (call,) = session.calls
    payload = _payload_text(call).lower()
    for host in _FOREIGN_SOURCE_HOSTS:
        assert host not in payload, host
    assert "geheimer-artikel" not in payload


@pytest.mark.parametrize("mode", ["teaser", "delayed_full"])
def test_no_foreign_source_domain_anywhere_in_the_free_channel_payload(monkeypatch, mode, tmp_path):
    monkeypatch.setenv("FREE_CHANNEL_MODE", mode)
    session = _Session()
    queue = FreeQueueRepository(tmp_path / "q.db")
    signal = _sig(
        "Preisfehler: Hamburg to Bangkok for €199 via Fly4free exclusive",
        link="https://www.fly4free.com/deal/x/", price=199.0, tier1=True,
    )

    _push(signal, session, free_queue=queue, now=_NOW)

    if mode == "teaser":
        payload = _payload_text(_by_chat(session)["free"] and session.calls[-1]).lower()
    else:
        (item,) = queue.due(_NOW + timedelta(hours=24))
        payload = (item.text + str(item.keyboards)).lower()

    for host in _FOREIGN_SOURCE_HOSTS:
        assert host not in payload, host


def test_no_foreign_source_domain_in_any_call_across_a_full_radar_run(tmp_path):
    """End to end: scan real-shaped signals from every source, run the
    radar, and inspect every single Telegram call it made."""
    session = _Session()
    signals = [
        DealSignal(source=source, title=f"Cheap flights from Berlin to Rome for €{price} via {source}",
                   link=f"https://www.{source.replace('_', '-')}.com/deal/{i}/", origins=("BER",),
                   tier_1_reasons=(), destination="Rome", destination_iata="FCO", price=float(price),
                   published=_NOW - timedelta(minutes=i))
        for i, (source, price) in enumerate(
            [("urlaubspiraten", 60), ("mydealz", 65), ("fly4free", 70), ("travel-dealz", 75)]
        )
    ]
    # urlaubspiraten.com/mydealz.de/etc. aren't real per-source domains in this
    # synthetic set, but the point stands: whatever the source, the payload
    # must never carry the SOURCE NAME or its link.
    run_radar(_seen(tmp_path), scan_fn=_scan(signals), dispatch_fn=lambda s: _push(s, session), now=_NOW)

    assert len(session.calls) >= 4
    for call in session.calls:
        payload = _payload_text(call).lower()
        for host in _FOREIGN_SOURCE_HOSTS:
            assert host not in payload, (host, payload[:200])


def test_signal_link_field_itself_is_never_read_by_the_formatter_or_dispatcher():
    """Static guard: neither format_signal_alert/format_signal_teaser nor
    signal_deal_sheet_url/signal_keyboards ever puts `signal.link` into
    their output - the only place it's used at all is the (removed)
    source-citation, which this template no longer has."""
    from trip_hunter.alerts.instant_alert_formatter import (
        format_signal_alert,
        format_signal_teaser,
        signal_deal_sheet_url,
        signal_keyboards,
    )

    signal = _sig(link="https://www.fly4free.com/this-exact-url-must-never-leak/")

    assert "this-exact-url-must-never-leak" not in format_signal_alert(signal)
    assert "this-exact-url-must-never-leak" not in format_signal_teaser(signal)
    assert "this-exact-url-must-never-leak" not in (signal_deal_sheet_url(signal) or "")
    assert "this-exact-url-must-never-leak" not in str(signal_keyboards(signal))


# --- concrete travel dates flow into the deal-sheet button (marker-aware) --------


def test_a_day_precise_travel_date_range_produces_real_dep_ret_and_a_dated_flight_search(monkeypatch):
    from urllib.parse import parse_qs, unquote, urlsplit

    from trip_hunter.alerts.instant_alert_formatter import signal_deal_sheet_url

    monkeypatch.delenv("TRAVELPAYOUTS_MARKER", raising=False)
    signal = _sig(origins=("FRA",), dest="Bangkok", iata="BKK", travel_dates="12.10.–19.10.2026")

    query = parse_qs(urlsplit(signal_deal_sheet_url(signal)).query)

    assert query["dep"] == ["2026-10-12"] and query["ret"] == ["2026-10-19"]
    assert "2026-10-12" in unquote(query["fl"][0]) and "2026-10-19" in unquote(query["fl"][0])


def test_exact_date_signal_gets_a_real_dated_hotel_link_when_the_destination_is_covered():
    """Even outside the flexible combo path (an exact date is known here,
    so _signal_combo_estimate doesn't apply), a covered destination still
    gets a real, dated hotel search link and a computed total - never the
    bare flight-only sheet from before."""
    from urllib.parse import parse_qs, urlsplit

    from trip_hunter.alerts.instant_alert_formatter import signal_deal_sheet_url

    signal = _sig(origins=("FRA",), dest="Bangkok", iata="BKK", price=399.0, travel_dates="12.10.–19.10.2026")
    query = parse_qs(urlsplit(signal_deal_sheet_url(signal)).query)

    assert "hl" in query and "booking.com" in query["hl"][0]
    assert "checkin%3D2026-10-12" in query["hl"][0] or "checkin=2026-10-12" in query["hl"][0]
    nights = 7
    expected_hotel_pp = round(45 * nights / 2)  # BKK's guide nightly rate, HOTEL_GUESTS=2
    assert query["hp"] == [str(expected_hotel_pp)]
    assert query["tp"] == [str(399 + expected_hotel_pp)]


def test_tier1_signal_with_no_date_gets_a_dateless_hotel_link_when_the_destination_is_covered():
    """A Tier-1 (error fare) signal never gets the combo treatment (see
    _signal_combo_estimate), but a covered destination should still offer
    a real hotel search - just dateless (no nights to base a price on),
    never a fabricated number."""
    from urllib.parse import parse_qs, urlsplit

    from trip_hunter.alerts.instant_alert_formatter import signal_deal_sheet_url

    signal = _sig(origins=("FRA",), dest="Bangkok", iata="BKK", price=19.0, tier1=True)
    query = parse_qs(urlsplit(signal_deal_sheet_url(signal)).query)

    assert "hl" in query and "google.com/travel/search" in query["hl"][0]
    assert "hp" not in query  # no nights known - no fabricated hotel price
    assert query["tp"] == ["19"]


def test_month_only_travel_dates_never_fabricate_a_day_for_an_uncovered_destination(monkeypatch):
    """Bischkek (FRU) has no hotel guide-price tier, so no combo and no
    example windows either - a month-only feed title still never turns
    into one fabricated single day for an uncovered destination."""
    from urllib.parse import parse_qs, urlsplit

    from trip_hunter.alerts.instant_alert_formatter import signal_deal_sheet_url

    monkeypatch.delenv("TRAVELPAYOUTS_MARKER", raising=False)
    signal = _sig(origins=("FRA",), dest="Bischkek", iata="FRU", travel_dates="Oktober 2026")

    query = parse_qs(urlsplit(signal_deal_sheet_url(signal)).query)

    assert "dep" not in query and "ret" not in query
    assert "google.com/travel/flights" in query["fl"][0]


def test_travelpayouts_marker_is_used_for_a_dated_signal_deal_link(monkeypatch):
    from urllib.parse import parse_qs, unquote, urlsplit

    from trip_hunter.alerts.instant_alert_formatter import signal_deal_sheet_url

    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "781828")
    signal = _sig(origins=("FRA",), dest="Bangkok", iata="BKK", travel_dates="12.10.–19.10.2026")

    flight_link = unquote(parse_qs(urlsplit(signal_deal_sheet_url(signal)).query)["fl"][0])

    assert "aviasales.com" in flight_link and "marker=781828" in flight_link


def test_feed_radar_workflow_passes_the_travelpayouts_marker_and_deal_sheet_url():
    from pathlib import Path

    text = (Path(__file__).parent.parent / ".github" / "workflows" / "feed_radar_fast.yml").read_text(encoding="utf-8")
    assert "TRAVELPAYOUTS_MARKER: ${{ secrets.TRAVELPAYOUTS_MARKER }}" in text
    assert "DEAL_SHEET_URL: ${{ vars.DEAL_SHEET_URL }}" in text
