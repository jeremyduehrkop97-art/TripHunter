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

PARSE MODE: every message here is Telegram-HTML (dispatch/telegram.py sends
parse_mode="HTML") so the total price can be bold. Consequently every
dynamic value (hotel name, URLs, destination text) goes through
`html.escape` and static text uses entities ("&amp;") - a raw "&" or "<"
would make Telegram reject the whole message.

`format_instant_alerts` returns a LIST of separate message strings, not one
joined block: messenger pushes are dispatched one at a time, never as a
single digest (that's what the newsletter is for).

`format_teaser_alert` is the Free-channel twin of `format_instant_alert`
(see dispatch/telegram.py's dual-channel routing, `dispatch_deal_alert`):
same headline/price-highlight lines, but the actual flight/hotel booking
links are withheld and replaced with a VIP-upgrade CTA pointing at the
real Stripe Payment Links (`_VIP_MONTHLY_CHECKOUT_URL` /
`_VIP_YEARLY_CHECKOUT_URL`) - the Free channel earns clicks on the teaser
itself, VIP channel members get the uncensored, affiliate-tagged booking
links immediately. Both share `_alert_body_lines` so the two channels can
never drift on the underlying facts (route, price, savings), only on
whether booking links are attached.

`_alert_body_lines` also appends a short, atmospheric destination blurb
(alerts/destination_context.py) as its own paragraph, after the price
lines - identical on both channels, since it's part of the shared body,
not either channel's link/CTA section.
"""

from __future__ import annotations

import html

from trip_hunter.alerts._shared import fmt_date, nights_label, trip_nights
from trip_hunter.alerts.destination_context import destination_context
from trip_hunter.engine.alert_tier import AlertTier, classify_alert_tier
from trip_hunter.models import Deal, DealType
from trip_hunter.monetization.affiliate import add_affiliate_tag

# Real, live Stripe Payment Links - same two links used by
# web/index.html's #pricing section. Kept as literals here too (same "no
# build step, no templating, find/replace is enough" reasoning documented
# in that file's trailing design-notes comment) rather than duplicated
# into config.py: these are public checkout URLs, not secrets, and are
# already embedded directly in the public landing page HTML.
_VIP_MONTHLY_CHECKOUT_URL = "https://buy.stripe.com/00w00ke0x5FX6jOfHCbMQ02"
_VIP_YEARLY_CHECKOUT_URL = "https://buy.stripe.com/3cIdRa9Kh4BTgYsanibMQ01"

ERROR_FARE_BANNER = "🚨 ERROR FARE: Kann sich minütlich ändern – extrem schnell buchen!"
ERROR_FARE_TIP = (
    "💡 Tipp: Erst den Flug buchen, Buchungsbestätigung abwarten und Unterkünfte "
    "erst 24–48h später final buchen (falls die Airline storniert)."
)

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
    """Format ONE deal as a single, compact messenger-ready message,
    including affiliate-tagged booking links - the VIP-channel voice."""
    lines = _alert_body_lines(deal)
    lines.extend(_link_lines(deal))
    if _is_tier_1(deal):
        lines.append(ERROR_FARE_TIP)
    return "\n".join(lines)


def format_instant_alerts(deals: list[Deal]) -> list[str]:
    """One separate message per deal - never a combined digest. Preserves
    input order; an empty input returns an empty list (nothing to push)."""
    return [format_instant_alert(deal) for deal in deals]


def format_teaser_alert(deal: Deal) -> str:
    """The Free-channel twin of `format_instant_alert`: same headline and
    price-highlight lines, but no flight/hotel booking links - replaced
    with a VIP-upgrade CTA linking to the real Stripe checkout pages."""
    lines = _alert_body_lines(deal)
    lines.extend(_vip_upgrade_lines())
    return "\n".join(lines)


def _is_tier_1(deal: Deal) -> bool:
    return classify_alert_tier(deal) is AlertTier.TIER_1_ERROR_FARE


def _vip_upgrade_lines() -> list[str]:
    """The Free-channel's VIP-upgrade CTA: real, live Stripe Payment
    Links, not flight/hotel booking links - a Free-channel member unlocks
    the actual booking links (see format_instant_alert/_link_lines) by
    subscribing via one of these. Not affiliate-tagged (add_affiliate_tag
    is for outbound flight/hotel booking links, not our own checkout
    pages)."""
    return [
        "🔒 Sofortige Buchungslinks für Flug &amp; Hotel im VIP-Kanal freischalten:",
        f"👉 VIP Monats-Pass (7,99 €): {html.escape(_VIP_MONTHLY_CHECKOUT_URL)}",
        f"👉 VIP Jahres-Pass (49 € – spare 49%): {html.escape(_VIP_YEARLY_CHECKOUT_URL)}",
    ]


def _alert_body_lines(deal: Deal) -> list[str]:
    """Headline + date + (if present) hotel/total lines - everything
    `format_instant_alert` and `format_teaser_alert` share. Booking links
    (or their absence) are each caller's own concern, appended after."""
    flight = deal.flight
    emoji = _ALERT_EMOJI.get(deal.deal_type, _DEFAULT_ALERT_EMOJI)
    headline = _deal_type_headline(deal.deal_type)

    lines: list[str] = [ERROR_FARE_BANNER] if _is_tier_1(deal) else []
    lines += [
        f"{emoji} {headline}: {flight.origin} → {flight.destination} für "
        f"{flight.price:.2f} {flight.currency}{_pct_suffix(deal)}"
    ]
    lines.append(f"{fmt_date(flight.departure_date)}–{fmt_date(flight.return_date)} · {nights_label(trip_nights(deal))}")

    if deal.accommodation is not None:
        lines.extend(_price_block_lines(deal))

    # Blank line before the atmospheric blurb - a real paragraph break,
    # not another bullet, so it reads as editorial copy rather than one
    # more data row. Shared by both channels (see module docstring) so
    # Free and VIP can never drift on destination tone.
    lines.append("")
    lines.append(f"📍 {html.escape(destination_context(flight.destination))}")

    return lines


# Hotel prices are for a room for this many guests - mirrors the adults=2
# default of providers/serpapi_hotels_client.py.
HOTEL_GUESTS = 2

_CURRENCY_SYMBOL = {"EUR": "€"}
_PRICE_RULE = "━" * 20


def _fmt_price(amount: float, currency: str) -> str:
    """German price style: "79 €", "79,50 €" (whole amounts drop the
    decimals). Unknown currencies keep their ISO code."""
    text = f"{amount:.0f}" if round(amount, 2) == round(amount) else f"{amount:.2f}".replace(".", ",")
    return f"{text} {_CURRENCY_SYMBOL.get(currency, currency)}"


def _price_block_lines(deal: Deal) -> list[str]:
    """Flight, hotel and total as separate, aligned lines with the total
    in bold - all per person. Only for deals with a hotel - a flight-only
    deal has no breakdown or total to show (its price is already in the
    headline, per person as returned by the provider).

    The flight price is for 1 adult; the hotel price is a whole room for
    HOTEL_GUESTS (SerpApi Google Hotels, adults=2 by default), so the
    per-person total is flight + hotel / HOTEL_GUESTS. This is display
    only - Deal.actual_total_price (flight + whole hotel) stays the basis
    for filters and budgets.
    """
    flight, hotel = deal.flight, deal.accommodation
    nights = trip_nights(deal)
    hotel_per_person = hotel.total_price / HOTEL_GUESTS
    total_per_person = flight.price + hotel_per_person
    return [
        f"✈️ Flug: {_fmt_price(flight.price, flight.currency)} p.P.",
        f"🏨 {html.escape(hotel.name)} ({nights} {'Nacht' if nights == 1 else 'Nächte'}): "
        f"{_fmt_price(hotel.total_price, hotel.currency)} "
        f"({_fmt_price(hotel_per_person, hotel.currency)} p.P.)",
        _PRICE_RULE,
        f"💰 <b>GESAMTPREIS: {_fmt_price(total_per_person, flight.currency)} p.P.</b>",
    ]


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
        lines.append(f"👉 {html.escape(flight_link)}")

    if deal.accommodation is not None:
        hotel_link = add_affiliate_tag(deal.accommodation.booking_link)
        if hotel_link:
            lines.append(f"👉 {html.escape(hotel_link)}")

    return lines
