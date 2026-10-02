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
    discount threshold (never Tier-1, never price_cap_for - a hotel's
    nightly rate isn't comparable to either);
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
from trip_hunter.engine.flexible_dates import LONG_HAUL_DESTINATIONS, MID_HAUL_DESTINATIONS
from trip_hunter.feed_seen_repository import FeedSeenRepository, deal_key, url_key
from trip_hunter.price_history_repository import DEFAULT_DB_PATH

FAST_SOURCE_NAMES = ("mydealz", "fly4free", "travel-dealz", "urlaubspiraten")
RADAR_MAX_AGE = timedelta(hours=24)
MAX_PUSHES_PER_RUN = 5

# Absolute price ceilings for a REGULAR (non-Tier-1) feed signal, per the
# same three-tier destination classification engine/flexible_dates.py
# already uses for realistic trip length - "how far is this destination"
# is answered the same way everywhere in this project, rather than by two
# independently-curated distance ideas that could quietly disagree.
# Anything over its tier's ceiling isn't a real "Schnäppchen" (bargain) by
# this radar's own standard, however clean the parsing - EXCEPT a Tier-1
# error fare, which is recognised on its own terms (keyword/category, or
# an even lower absolute floor - see feed_sensor._tier_1_reasons) and is
# never subject to this cap at all, not merely given a higher one.
SHORT_HAUL_PRICE_CAP = 80.0
MID_HAUL_PRICE_CAP = 280.0
LONG_HAUL_PRICE_CAP = 620.0


def price_cap_for(destination_iata: str | None) -> float:
    """The absolute EUR ceiling a non-Tier-1 FLIGHT-lead signal to
    `destination_iata` must stay under (see is_hotel_deal_worthy for the
    separate hotel-lead threshold). A destination with no resolved IATA
    code at all gets the strictest (short-haul) cap rather than a
    guessed, more generous one - the same "when in doubt, don't push it"
    convention this module's implausible-short-hop guard already
    follows."""
    if destination_iata in LONG_HAUL_DESTINATIONS:
        return LONG_HAUL_PRICE_CAP
    if destination_iata in MID_HAUL_DESTINATIONS:
        return MID_HAUL_PRICE_CAP
    return SHORT_HAUL_PRICE_CAP


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

    A regular (non-Tier-1) FLIGHT-lead signal also has to clear its
    destination's price_cap_for ceiling - a channel that promises "echte
    Knaller-Angebote" (real bargains) shouldn't post a 1.123 € "deal"
    just because parsing happened to succeed. Tier-1 error fares ARE
    exempt from this cap (they're recognised on their own, stricter terms
    - see price_cap_for's docstring), so a genuine sub-40 €/sub-250 €
    error fare is never blocked by it.

    A HOTEL-lead signal (deal_lead="hotel") is never Tier-1 and never
    subject to price_cap_for at all - see is_hotel_deal_worthy's
    docstring for why a euro ceiling doesn't generalise to a hotel's
    nightly rate, and what this project checks instead.
    """
    if signal.destination_iata is None or signal.price is None:
        return False
    if signal.deal_lead == "hotel":
        return is_hotel_deal_worthy(signal)
    return signal.is_tier_1 or signal.price <= price_cap_for(signal.destination_iata)


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
