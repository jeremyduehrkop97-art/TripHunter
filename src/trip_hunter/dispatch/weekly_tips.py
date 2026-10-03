"""Weekly "Travel Hack" tip: one rotating, high-value travel tip sent to
BOTH the Free and VIP Telegram channels once a week (Sunday 19:00 Europe
time - see .github/workflows/weekly_tips.yml). Not a deal, not time-
sensitive - pure value-add content to keep both channels active between
actual deal alerts.

ROTATION (weekly_tip_repository.py): the tip after whichever one was sent
last, wrapping back to the first after the last - a single row in the
same SQLite file as the rest of this project's state (restored between
GitHub Actions runs together with it, like feed_seen_repository.py and
free_queue_repository.py), so the sequence survives across weeks without
ever repeating the same tip twice in a row. `next_tip_index` is a pure
function (no I/O), so the rotation order itself is trivially testable
without a database.

CONTENT (TIPS below): 3 independently useful tips, each with its own
affiliate link (monetization/travel_hack_affiliate.py - NOT Travelpayouts,
which only covers flights/hotels; see that module's own docstring for
why these are a separate set of real, env-configurable partner links).

FRAMING: identical core content on both channels - only the wrapper
differs. VIP gets the tip framed as a "Insider-Tipp für Member" (it
already is one, no upsell needed - this IS the VIP channel). Free gets
the exact same tip plus one closing line pointing at the VIP channel for
the actual deals/error fares, matching every other Free-channel funnel
element in this project (never a second booking link, just the upsell).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from trip_hunter.monetization.travel_hack_affiliate import (
    esim_affiliate_url,
    flight_compensation_affiliate_url,
    skip_line_tickets_affiliate_url,
)
from trip_hunter.monetization.upsell import vip_subscription_url
from trip_hunter.weekly_tip_repository import WeeklyTipRepository


@dataclass(frozen=True)
class TravelHackTip:
    key: str
    header: str  # already "<b>...</b>" - static copy, never user input
    body: str
    cta_text: str
    affiliate_url: Callable[[], str]


TIPS: tuple[TravelHackTip, ...] = (
    TravelHackTip(
        key="esim",
        header="<b>📡 Insider-Tipp für Member: Kein Roaming-Schock mehr!</b>",
        body=(
            "Bevor es ins Ausland geht: Hol dir ein lokales Datenpaket per eSIM "
            "(z. B. über Yesim oder Airalo) – in 2 Minuten eingerichtet, oft "
            "für wenige Euro pro GB, ganz ohne Roaming-Gebühren oder neue SIM-Karte."
        ),
        cta_text="📡 eSIM jetzt sichern",
        affiliate_url=esim_affiliate_url,
    ),
    TravelHackTip(
        key="flight_compensation",
        header="<b>💶 Insider-Tipp für Member: Geld zurück bei Flugverspätung!</b>",
        body=(
            "Hatte dein Flug in den letzten 3 Jahren über 3 Stunden Verspätung, wurde "
            "annulliert oder wurdest du nicht geboardet? Dann stehen dir nach EU-Recht "
            "bis zu 600 € Entschädigung zu – rückwirkend. Dienste wie Compensair "
            "oder AirHelp prüfen das in 2 Minuten kostenlos und übernehmen den "
            "Rechtsstreit mit der Airline."
        ),
        cta_text="💶 Jetzt kostenlos prüfen",
        affiliate_url=flight_compensation_affiliate_url,
    ),
    TravelHackTip(
        key="skip_line_tickets",
        header="<b>🎫 Insider-Tipp für Member: Keine Zeit in der Warteschlange verschwenden!</b>",
        body=(
            "Ob Eiffelturm, Sagrada Família oder Kolosseum: Mit Skip-the-Line-Tickets "
            "(z. B. über Tiqets oder Klook) gehst du direkt rein, statt Stunden in "
            "der Sonne zu stehen – mehr Zeit für den eigentlichen Urlaub."
        ),
        cta_text="🎫 Tickets sichern",
        affiliate_url=skip_line_tickets_affiliate_url,
    ),
)

FREE_CHANNEL_UPSELL_LINE = "⚡️ Die schnellsten Deals & Error Fares gibt es im VIP-Kanal."
_VIP_UPSELL_BUTTON_TEXT = "⚡️ Jetzt VIP werden"


def next_tip_index(last_index: int | None, tip_count: int) -> int:
    """The next rotation index after `last_index`, wrapping back to 0
    after the last tip - None (never sent before) starts at 0."""
    if last_index is None:
        return 0
    return (last_index + 1) % tip_count


def select_weekly_tip(repo: WeeklyTipRepository) -> tuple[int, TravelHackTip]:
    """The (index, tip) due this week, per the repository's rotation
    state. Does not record anything itself - dispatch_weekly_tip records
    once a send has actually been attempted, so a pure "what would be
    sent" check (e.g. a dry run) never silently advances the rotation."""
    state = repo.last_sent()
    index = next_tip_index(state.last_index if state else None, len(TIPS))
    return index, TIPS[index]


def format_vip_tip(tip: TravelHackTip) -> str:
    """VIP framing: the "Insider-Tipp für Member" header already says
    this is a member perk - no further line needed."""
    return f"{tip.header}\n\n{tip.body}"


def format_free_tip(tip: TravelHackTip) -> str:
    """Free framing: identical content plus one closing line pointing at
    the VIP channel - the only difference from the VIP version."""
    return f"{tip.header}\n\n{tip.body}\n\n{FREE_CHANNEL_UPSELL_LINE}"


def tip_keyboard(tip: TravelHackTip) -> dict:
    """The one CTA button every channel gets - the tip's own affiliate
    link (never Travelpayouts - see monetization/travel_hack_affiliate.py)."""
    return {"inline_keyboard": [[{"text": tip.cta_text, "url": tip.affiliate_url()}]]}


def free_tip_keyboard(tip: TravelHackTip) -> dict:
    """Free channel: the same CTA, plus the VIP upsell underneath -
    matching every other Free-channel funnel element in this project
    (never a second booking link, just the one upsell)."""
    return {
        "inline_keyboard": [
            [{"text": tip.cta_text, "url": tip.affiliate_url()}],
            [{"text": _VIP_UPSELL_BUTTON_TEXT, "url": vip_subscription_url()}],
        ]
    }


def dispatch_weekly_tip(
    repo: WeeklyTipRepository,
    *,
    send_fn: Callable[[str, str, list[dict]], bool],
    free_chat_id: str | None,
    vip_chat_id: str | None,
    now: datetime | None = None,
) -> TravelHackTip | None:
    """Send this week's tip to whichever of the Free/VIP channels is
    configured, then advance the rotation - unconditionally, even if a
    send fails: this is a low-stakes weekly content post, not a
    perishable deal, and re-sending the SAME tip next week over one
    transient failure would cost the rotation more (a repeated tip) than
    it gains. Returns the tip sent, or None if NEITHER channel is
    configured at all - nothing was sent, so the rotation is also not
    advanced (that would just burn through tips with nobody seeing them)."""
    if not free_chat_id and not vip_chat_id:
        print("Weekly-Tip: kein Kanal konfiguriert - nichts gesendet.")
        return None

    index, tip = select_weekly_tip(repo)
    if vip_chat_id:
        send_fn(format_vip_tip(tip), vip_chat_id, [tip_keyboard(tip)])
    if free_chat_id:
        send_fn(format_free_tip(tip), free_chat_id, [free_tip_keyboard(tip)])
    repo.record_sent(index, now=now)
    print(f"Weekly-Tip gesendet: {tip.key}")
    return tip


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="python -m trip_hunter.dispatch.weekly_tips",
        description="Send this week's rotating travel-hack tip to the Free/VIP channels.",
    )


def main(argv: list[str] | None = None) -> TravelHackTip | None:
    """CLI entry point (the weekly workflow)."""
    from trip_hunter.dispatch.telegram import get_bot_token, get_free_chat_id, get_vip_chat_id, send_text_message
    from trip_hunter.price_history_repository import DEFAULT_DB_PATH

    _build_parser().parse_args(argv)
    print("TRIP HUNTER — WEEKLY TRAVEL HACK TIP")
    bot_token = get_bot_token()
    if not bot_token:
        print("Weekly-Tip: Telegram nicht konfiguriert.")
        return None

    def send(text: str, chat_id: str, keyboards: list[dict]) -> bool:
        return send_text_message(text, chat_id, bot_token, reply_markups=keyboards)

    repo = WeeklyTipRepository(DEFAULT_DB_PATH)
    return dispatch_weekly_tip(
        repo, send_fn=send, free_chat_id=get_free_chat_id(), vip_chat_id=get_vip_chat_id(),
        now=datetime.now(timezone.utc),
    )


if __name__ == "__main__":
    main()
