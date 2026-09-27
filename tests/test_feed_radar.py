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
         dest="Lisbon", iata="LIS", price=89.0, tier1=False, published=None) -> DealSignal:
    return DealSignal(
        source=source, title=title, link=link or f"https://www.fly4free.com/deal/{abs(hash(title)) % 10**6}/",
        origins=origins, tier_1_reasons=("keyword:error",) if tier1 else (), destination=dest, destination_iata=iata,
        price=price, published=published or _NOW - timedelta(hours=1),
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


def test_worthy_means_tier_1_or_destination_and_price():
    assert is_pushworthy(_sig())
    assert is_pushworthy(_sig(tier1=True, dest=None, iata=None, price=None))
    assert is_pushworthy(_sig(iata=None, dest="Bangkok"))
    assert not is_pushworthy(_sig(dest=None, iata=None))       # no destination
    assert not is_pushworthy(_sig(price=None))                 # no price


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


def test_vip_gets_the_full_signal_with_a_source_button():
    session = _Session()
    signal = _sig("Cheap flights from Hamburg to Lisbon for €89", link="https://www.fly4free.com/deal/1/")

    assert _push(signal, session) is True

    vip = _by_chat(session)["vip"]
    assert "Hamburg nach Lissabon" in vip["caption"] and "ab 89 €" in vip["caption"] and "Fly4free" in vip["caption"]
    assert "noch nicht geprüft" in vip["caption"]
    assert json.loads(vip["reply_markup"])["inline_keyboard"] == [[{"text": "🔎 Deal ansehen", "url": "https://www.fly4free.com/deal/1/"}]]
    assert "has_spoiler" not in vip


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
    assert item.text.startswith("⏱ Dieser Hinweis ging vor 24 Std. an den VIP-Kanal") and "Fly4free" in item.text


def test_share_text_of_a_signal_has_destination_price_and_invite_but_no_source_link(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setenv("FREE_CHANNEL_INVITE_URL", "https://t.me/+Invite")
    session = _Session()
    _push(_sig(price=19.0, tier1=True, link="https://www.fly4free.com/secret-article/"), session)

    share = json.loads(_by_chat(session)["free"]["reply_markup"])["inline_keyboard"][1][0]["url"]
    text = parse_qs(urlsplit(share).query)["text"][0]

    assert text == "Schau mal, Trip Hunter hat gerade Lissabon ab 19 € gefunden! ✈️ Hier ist der Deal: https://t.me/+Invite"
    assert "fly4free" not in text.lower() and "secret-article" not in text


def test_html_in_feed_titles_and_destinations_is_escaped():
    session = _Session()
    _push(_sig("Flights <b>from</b> Hamburg & more", dest="A&B <c>", iata=None), session)

    caption = _by_chat(session)["vip"]["caption"]
    assert "A&amp;B &lt;c&gt;" in caption and "&lt;b&gt;from&lt;/b&gt; Hamburg &amp; more" in caption


def test_no_telegram_configuration_returns_false_without_raising(capsys):
    assert dispatch_signal_alert(_sig(), bot_token="", free_chat_id="", vip_chat_id="", default_chat_id="") is False
    assert "nicht konfiguriert" in capsys.readouterr().out


def test_signal_without_https_link_gets_no_button():
    session = _Session()
    _push(_sig(link="http://insecure.example/x"), session)
    assert "reply_markup" not in _by_chat(session)["vip"]


# --- DACH scope in the signal alert (departure line, currency) -------------------


def test_signal_alert_shows_a_clean_departure_line_with_city_and_code():
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    text = format_signal_alert(_sig(origins=("VIE",), dest="Zurich", iata="ZRH"))
    assert "🛫 Abflug: Wien (VIE)" in text.splitlines()


def test_signal_alert_departure_line_lists_several_dach_origins():
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    text = format_signal_alert(_sig(origins=("HAM", "ZRH")))
    assert "🛫 Abflug: Hamburg (HAM) / Zürich (ZRH)" in text.splitlines()


@pytest.mark.parametrize("origin, city", [("VIE", "Wien"), ("SZG", "Salzburg"), ("INN", "Innsbruck"),
                                          ("ZRH", "Zürich"), ("GVA", "Genf"), ("BSL", "Basel")])
def test_departure_line_for_every_new_dach_airport(origin, city):
    from trip_hunter.alerts.instant_alert_formatter import format_signal_alert

    assert f"🛫 Abflug: {city} ({origin})" in format_signal_alert(_sig(origins=(origin,))).splitlines()


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
    assert any("🛫 Abflug: Wien (VIE)" in t for t in vip_texts)
    assert any("🛫 Abflug: Zürich (ZRH)" in t for t in vip_texts)
    assert any("🛫 Abflug: Basel (BSL)" in t for t in vip_texts)
