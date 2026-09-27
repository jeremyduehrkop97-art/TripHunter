"""Hourly feed radar: scans the deal feeds and pushes new DACH-departure
deals to Telegram - and NOTHING ELSE. It never touches SerpApi: it does
not import a provider, does not load the SerpApi key and the workflow does
not even pass it, so a radar run costs exactly 0 credits by construction.
(`python -m trip_hunter.daily_sampler --feeds-only` runs this same code.)

What is pushed (DACH = Germany, Austria, Switzerland) - an unverified
hint from a third-party feed, labelled as
such (alerts/instant_alert_formatter.format_signal_alert):
  - every Tier-1 signal (error-fare keywords/category, or a price under the
    Tier-1 bars) -> VIP immediately, and Free per the existing
    teaser / delayed_full logic (dispatch_signal_alert);
  - any other signal with a known destination AND price -> VIP only
    (steady content, like the Tier-3 deals; Free is not spammed).
Signals without destination or price are skipped, at most MAX_PUSHES_PER_RUN
go out per run (Tier 1 first, then newest; the rest waits for the next
hour), and the same article - or the same deal from another feed - is never
sent twice (feed_seen_repository.py). The very first run, with nothing
stored yet, only SEEDS the current feed contents silently: the backlog is
not dumped on the channels. A restored-empty database therefore never
floods either.

Failure policy: empty, blocked or malformed feeds, and any Telegram error,
end in a quiet skip - never in an exception. A failed send is not recorded,
so the next hour retries it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from trip_hunter.dispatch.telegram import dispatch_signal_alert
from trip_hunter.engine.feed_sensor import FEED_SOURCES, DealSignal, scan_feeds
from trip_hunter.feed_seen_repository import FeedSeenRepository, deal_key, url_key
from trip_hunter.price_history_repository import DEFAULT_DB_PATH

FAST_SOURCE_NAMES = ("mydealz", "fly4free", "travel-dealz", "urlaubspiraten")
RADAR_MAX_AGE = timedelta(hours=24)
MAX_PUSHES_PER_RUN = 5


@dataclass(frozen=True)
class RadarResult:
    scanned: int = 0
    seeded: int = 0
    candidates: int = 0
    sent: int = 0
    failed: int = 0


def radar_sources() -> dict[str, str]:
    return {name: FEED_SOURCES[name] for name in FAST_SOURCE_NAMES}


def is_pushworthy(signal: DealSignal) -> bool:
    """Tier 1, or a concrete deal: destination and price both known."""
    if signal.is_tier_1:
        return True
    return bool((signal.destination or signal.destination_iata) and signal.price is not None)


def run_radar(
    seen: FeedSeenRepository,
    *,
    scan_fn: Callable[..., list[DealSignal]] | None = None,
    dispatch_fn: Callable[[DealSignal], bool] | None = None,
    sources: dict[str, str] | None = None,
    max_pushes: int = MAX_PUSHES_PER_RUN,
    dry_run: bool = False,
    now: datetime | None = None,
) -> RadarResult:
    """One radar pass. Never raises."""
    scan_fn = scan_fn or scan_feeds
    dispatch_fn = dispatch_fn or dispatch_signal_alert
    status: dict[str, str] = {}
    try:
        signals = scan_fn(
            sources if sources is not None else radar_sources(),
            max_age=RADAR_MAX_AGE, now=now, status=status,
        )
    except Exception as exc:  # noqa: BLE001 - the radar must never crash the workflow
        print(f"Feed-Radar: Scan übersprungen ({type(exc).__name__}).")
        return RadarResult()
    if status:
        print("Feed-Radar: " + "; ".join(f"{name} {outcome}" for name, outcome in status.items()))

    try:
        if seen.is_empty():
            if not dry_run:
                for signal in signals:
                    seen.mark(signal, action="seed", now=now)
            print(f"Feed-Radar: erster Lauf - {len(signals)} bestehende Einträge still vorgemerkt, nichts gesendet.")
            return RadarResult(scanned=len(signals), seeded=len(signals))

        batch: set[str] = set()
        candidates: list[DealSignal] = []
        for signal in signals:  # scan_feeds order: Tier 1 first, then newest
            keys = {url_key(signal), deal_key(signal)} - {None}
            if not is_pushworthy(signal) or keys & batch or seen.has_seen(signal):
                continue
            batch |= keys
            candidates.append(signal)

        sent = failed = 0
        for signal in candidates[:max_pushes]:
            label = f"{signal.source}: {signal.title[:70]}"
            if dry_run:
                print(f"Feed-Radar (dry-run): würde senden - {label}")
                continue
            if dispatch_fn(signal):
                seen.mark(signal, action="sent", now=now)
                sent += 1
            else:
                failed += 1
                print(f"Feed-Radar: Versand fehlgeschlagen, nächster Lauf versucht es erneut - {label}")
        waiting = max(0, len(candidates) - max_pushes)
        print(f"Feed-Radar: {len(candidates)} neue Signale, {sent} gesendet, {failed} fehlgeschlagen, {waiting} warten.")
        return RadarResult(scanned=len(signals), candidates=len(candidates), sent=sent, failed=failed)
    except Exception as exc:  # noqa: BLE001
        print(f"Feed-Radar: abgebrochen ({type(exc).__name__}).")
        return RadarResult(scanned=len(signals))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trip_hunter.feed_radar",
        description="Scan the deal feeds and push new DACH-departure deals. Uses no SerpApi credits.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent; send and record nothing.")
    parser.add_argument("--max-pushes", type=int, default=MAX_PUSHES_PER_RUN, help="Cap of messages per run.")
    return parser


def run(argv: list[str] | None = None) -> RadarResult:
    """CLI entry point (the hourly workflow)."""
    args = _build_parser().parse_args(argv)
    print("TRIP HUNTER — FEED RADAR (0 SerpApi-Credits)")
    if args.dry_run:
        print("Modus: DRY RUN")
    seen = FeedSeenRepository(db_path=DEFAULT_DB_PATH)
    return run_radar(seen, max_pushes=max(0, args.max_pushes), dry_run=args.dry_run, now=datetime.now(timezone.utc))


if __name__ == "__main__":
    run()
