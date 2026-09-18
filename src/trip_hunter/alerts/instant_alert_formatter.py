"""Formats Deal objects into extremely compact plaintext pushes for instant
messengers (WhatsApp / Telegram) - one deal, one message, meant for Flash
Deal / Error Fare style push notifications.

Deliberately a DIFFERENT voice from deal_formatter.py's Markdown/
html_formatter.py's HTML: those are the warm, branded newsletter; this is
the punchy, scannable alert-bot style ("FLIGHT DROP" not "Günstiger
Flug") - matching how real flight-deal-alert channels actually read.
Still built on the exact same shared baseline/label data
(alerts/_shared.py) and the same affiliate-decorated links
(monetization/affiliate.py), so no channel can ever disagree about the
underlying facts, only about tone.

Emojis are used deliberately here (and only here among the three
formatters) - this channel exists specifically to be an eye-catching push
notification, and the product brief for this exact format calls for them.

`format_instant_alerts` returns a LIST of separate message strings, not one
joined block: messenger pushes are dispatched one at a time, never as a
single digest (that's what the newsletter is for).
"""

from __future__ import annotations

from trip_hunter.alerts._shared import fmt_date, nights_label, trip_nights
from trip_hunter.models import Deal, DealType
from trip_hunter.monetization.affiliate import add_affiliate_tag

_ALERT_EMOJI: dict[DealType, str] = {
    DealType.COMBINED_TRIP_DROP: "🔥",
    DealType.ERROR_FARE: "🚨",
    DealType.FLIGHT_DROP: "🚨",
    DealType.HOTEL_DROP: "🏨",
    DealType.UNUSUALLY_LOW: "💡",
    DealType.BASELINE_UNAVAILABLE: "❓",
    DealType.PRICE_INCOMPLETE: "❓",
}
_DEFAULT_ALERT_EMOJI = "📣"


def _deal_type_headline(deal_type: DealType) -> str:
    """"FLIGHT_DROP" -> "FLIGHT DROP" - the alert-bot style this channel
    uses on purpose, distinct from the newsletter's translated labels."""
    return deal_type.value.replace("_", " ")


def format_instant_alert(deal: Deal) -> str:
    """Format ONE deal as a single, compact messenger-ready message."""
    flight = deal.flight
    emoji = _ALERT_EMOJI.get(deal.deal_type, _DEFAULT_ALERT_EMOJI)
    headline = _deal_type_headline(deal.deal_type)

    lines: list[str] = [
        f"{emoji} {headline}: {flight.origin} → {flight.destination} für "
        f"{flight.price:.2f} {flight.currency}{_pct_suffix(deal)}"
    ]
    lines.append(f"{fmt_date(flight.departure_date)}–{fmt_date(flight.return_date)} · {nights_label(trip_nights(deal))}")

    if deal.accommodation is not None:
        lines.append(f"🏨 {deal.accommodation.name} · {deal.accommodation.total_price:.2f} {deal.accommodation.currency}")
        lines.append(f"💰 Gesamt: {deal.actual_total_price:.2f} {flight.currency}")

    for link_line in _link_lines(deal):
        lines.append(link_line)

    return "\n".join(lines)


def format_instant_alerts(deals: list[Deal]) -> list[str]:
    """One separate message per deal - never a combined digest. Preserves
    input order; an empty input returns an empty list (nothing to push)."""
    return [format_instant_alert(deal) for deal in deals]


def _pct_suffix(deal: Deal) -> str:
    """Marketing framing: a real saving (savings_percentage >= 0) is shown
    as a negative delta, e.g. "(-44%)" ("price is down 44%"). A Deal whose
    deal_type was earned on flight-level savings alone can still end up
    with a NEGATIVE overall savings_percentage once the hotel side is
    combined (see trip_combiner.py) - i.e. genuinely priced ABOVE the
    baseline overall. Prepending another "-" there would double the sign
    ("(--5%)" - a real bug this exact case caught); show "(+5%)" instead,
    which is both correct and more honest than the previous glitch.
    """
    if deal.savings_percentage is None:
        return ""
    value = deal.savings_percentage
    if value >= 0:
        return f" (-{value:.0%})"
    return f" (+{-value:.0%})"


def _link_lines(deal: Deal) -> list[str]:
    # Unlike deal_formatter.py / html_formatter.py, a missing booking link
    # is simply omitted here (no "kein Direktlink verfügbar" line) - a
    # deliberate compactness tradeoff for this channel, not a hidden
    # fallback: nothing false is stated, the line just isn't worth the
    # space in a push notification. See test_instant_alert_formatter.py.
    lines: list[str] = []
    flight_link = add_affiliate_tag(deal.flight.booking_link)
    if flight_link:
        lines.append(f"👉 {flight_link}")

    if deal.accommodation is not None:
        hotel_link = add_affiliate_tag(deal.accommodation.booking_link)
        if hotel_link:
            lines.append(f"👉 {hotel_link}")

    return lines
