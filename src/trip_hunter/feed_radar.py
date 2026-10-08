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
  - a HOTEL-lead signal (deal_lead="hotel" - a heavily discounted stay
    with no flight named at all, see engine/feed_sensor.py's "HOTEL-FIRST
    SIGNALS") -> VIP only, once it clears is_hotel_deal_worthy's own
    discount threshold (never Tier-1, never the route-benchmark check
    below - a hotel's nightly rate isn't comparable to either);
  - any other (regular, non-Tier-1) FLIGHT-lead signal -> VIP only once it
    clears MIN_FLIGHT_DISCOUNT_PERCENT below its route's own
    engine/route_benchmark.get_economy_benchmark - a percentage gate
    against a realistic market price for THIS route, not one fixed
    ceiling shared by every destination in a whole distance class (the
    old price_cap_for).
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
from trip_hunter.engine.route_benchmark import get_route_benchmark, is_deal_price
from trip_hunter.feed_seen_repository import FeedSeenRepository, deal_key, url_key
from trip_hunter.price_history_repository import DEFAULT_DB_PATH

FAST_SOURCE_NAMES = ("mydealz", "fly4free", "travel-dealz", "urlaubspiraten")
RADAR_MAX_AGE = timedelta(hours=24)
MAX_PUSHES_PER_RUN = 5

# The bargain threshold for a REGULAR (non-Tier-1) FLIGHT-lead signal:
# how far under its route's own engine/route_benchmark.get_economy_
# benchmark the price has to be. Replaces the old flat per-distance-class
# EUR ceilings (price_cap_for/SHORT_HAUL_PRICE_CAP etc., removed) - a
# percentage against a realistic market price for THIS route scales
# honestly from a 140 EUR Mallorca weekend up through a 950 EUR Sydney
# fare, rather than one shared ceiling per whole distance class. A Tier-1
# error fare is recognised on its own terms (keyword/category, or an even
# lower absolute floor - see feed_sensor._tier_1_reasons) and is never
# subject to this gate at all, not merely given a more generous one.
MIN_FLIGHT_DISCOUNT_PERCENT = 30.0


def flight_deal_discount(signal: DealSignal) -> tuple[bool, float]:
    """Whether a regular FLIGHT-lead `signal` clears MIN_FLIGHT_DISCOUNT_
    PERCENT below its route's own get_route_benchmark - in `signal`'s OWN
    detected cabin_class's terms (engine/feed_sensor._extract_cabin_class),
    not always Economy's, so a genuine Business/First bargain (e.g. New
    York Business for 890 € against its 1.850 € benchmark) is judged
    against a realistic price for THAT class, not mistaken for an
    impossible Economy deal or dismissed as an expensive one - and the
    actual discount percentage either way (see is_hotel_deal_worthy for
    the separate hotel-lead check, and _tier_1_reasons for why a Tier-1
    signal never needs to call this at all)."""
    benchmark = get_route_benchmark(signal.origins[0], signal.destination_iata, cabin_class=signal.cabin_class)
    return is_deal_price(signal.price, benchmark, MIN_FLIGHT_DISCOUNT_PERCENT)


# The lowest discount the task that introduced hotel-first signals itself
# named as an example ("-50%/-60%/-70%") - a hotel's nightly rate has no
# destination-independent absolute ceiling that means anything the way a
# flight price does (90 EUR/night is a steal for a 5-star resort and
# unremarkable for a budget room), so the feed's own self-reported,
# visible discount is this project's honest substitute for price_cap_for.
MIN_HOTEL_DISCOUNT_PERCENT = 50


def is_hotel_deal_worthy(signal: DealSignal) -> bool:
    """A hotel-first signal only counts as a genuine bargain - never just
    "parsed successfully" - once it clears MIN_HOTEL_DISCOUNT_PERCENT. No
    stated discount at all (engine/feed_sensor.py's
    _extract_discount_percent found none) means not pushworthy, never a
    guessed/assumed one."""
    return signal.hotel_discount_percent is not None and signal.hotel_discount_percent >= MIN_HOTEL_DISCOUNT_PERCENT


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
    """A concrete deal only: a RESOLVED IATA code (destination_iata, not
    just free destination text) AND a price, both known - never for
    ANY tier, no exceptions. This is a hard gate: free destination text
    with no resolvable IATA code is NEVER enough on its own, however
    cheap or however clearly it reads as an error fare - the reported
    "Berlin nach Ryanair Fantastische Entdeckungen" bug (a marketing
    campaign name survived every text guard in engine/feed_sensor.py and
    got treated as a real place, with destination_iata staying None the
    whole time) is exactly what this closes. engine/feed_sensor.py's
    _extract_destination already refuses generic promo/campaign text,
    airline names and short-hop phantoms as a destination outright (so
    `signal.destination` itself is usually also None by the time this
    runs) - this is the second, independent line of defence: even if a
    destination string somehow survived uncaught, no IATA code means no
    push, full stop.

    A regular (non-Tier-1) FLIGHT-lead signal also has to clear
    MIN_FLIGHT_DISCOUNT_PERCENT below its route's own
    engine/route_benchmark.get_economy_benchmark - a channel that
    promises "echte Knaller-Angebote" (real bargains) shouldn't post a
    price just because parsing happened to succeed, but the bar is now a
    percentage against a realistic market price for THIS route (see
    flight_deal_discount) rather than one fixed EUR ceiling shared by
    every destination in a whole distance class. Tier-1 error fares ARE
    exempt from this gate entirely (they're recognised on their own,
    stricter terms - see feed_sensor._tier_1_reasons), so a genuine
    sub-40 €/sub-250 € error fare is never blocked by it.

    A HOTEL-lead signal (deal_lead="hotel") is never Tier-1 and never
    subject to the flight discount gate at all - see is_hotel_deal_worthy's
    docstring for why a euro ceiling doesn't generalise to a hotel's
    nightly rate, and what this project checks instead.
    """
    if signal.destination_iata is None or signal.price is None:
        return False
    if signal.deal_lead == "hotel":
        return is_hotel_deal_worthy(signal)
    if signal.is_tier_1:
        return True
    is_deal, _discount_percent = flight_deal_discount(signal)
    return is_deal


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
