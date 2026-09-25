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

COMFORT HIGHLIGHTS (`comfort_highlights`): a deal that happens to have a
relaxed outbound departure or a proper weekend slot gets a small feature
line under the date line. Purely additive - nothing is ever filtered or
demoted for lacking one, and a deal with neither keeps the plain layout.

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

from datetime import time
from decimal import ROUND_HALF_UP, Decimal

from trip_hunter.alerts._shared import deal_type_label, fmt_date, nights_label, trip_nights
from trip_hunter.alerts.airport_names import city_name, flag_emoji
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

PRICE_DROP_BANNER = "📉 <b>PREISSTURZ: Flug nochmals günstiger!</b>"

ERROR_FARE_BANNER = "🚨 ERROR FARE: Kann sich minütlich ändern – extrem schnell buchen!"
ERROR_FARE_TIP = (
    "💡 Tipp: Erst den Flug buchen, Buchungsbestätigung abwarten und Unterkünfte "
    "erst 24–48h später final buchen (falls die Airline storniert)."
)


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
    """Header + badge + date + cost breakdown + destination blurb -
    everything `format_instant_alert` and `format_teaser_alert` share.
    Booking links (or their absence) are each caller's own concern,
    appended after.

    A price-drop update alert (`deal.previous_alert_price`) gets a
    prominent "PREISSTURZ" tag and the difference on top, above the
    header. The price appears ONLY in the cost breakdown, never in the header.
    The second line is the Tier-1 error-fare banner for Tier 1 deals,
    otherwise the savings badge. Comfort highlights (if any) follow the
    date line.
    """
    flight = deal.flight
    lines: list[str] = [*_price_drop_lines(deal)]
    lines += [
        f"{flag_emoji(flight.destination)} "
        f"<b>{html.escape(city_name(flight.origin))} nach {html.escape(city_name(flight.destination))}</b>",
        ERROR_FARE_BANNER if _is_tier_1(deal) else _badge_line(deal),
        f"{fmt_date(flight.departure_date)}–{fmt_date(flight.return_date)} · {nights_label(trip_nights(deal))}",
        *comfort_highlights(deal),
    ]
    lines.extend(_price_block_lines(deal))

    # Blank line before the atmospheric blurb - a real paragraph break,
    # not another bullet, so it reads as editorial copy rather than one
    # more data row. Shared by both channels (see module docstring) so
    # Free and VIP can never drift on destination tone.
    lines.append("")
    lines.append(f"📍 {html.escape(destination_context(flight.destination))}")

    return lines


# Comfort highlights - see module docstring.
COMFORT_DEPARTURE_FROM = time(9, 0)
COMFORT_DEPARTURE_UNTIL = time(14, 0)
WEEKEND_MAX_NIGHTS = 3
_FRIDAY, _SATURDAY, _SUNDAY, _MONDAY = 4, 5, 6, 0

COMFORT_TIME_LINE = "✨ Angenehme Flugzeiten (ab 09:00 Uhr)"
WEEKEND_LINE = "🌴 Wochenend-Trip"


def _is_comfort_departure(departure_time: str | None) -> bool:
    """Outbound departure between 09:00 and 14:00 (inclusive). A missing
    or unparsable time is simply not a highlight - never an error."""
    if not departure_time:
        return False
    try:
        parsed = time.fromisoformat(departure_time)
    except ValueError:
        return False
    return COMFORT_DEPARTURE_FROM <= parsed <= COMFORT_DEPARTURE_UNTIL


def _is_weekend_trip(deal: Deal) -> bool:
    """Out on a Friday or Saturday, back on the Sunday or Monday, at most
    WEEKEND_MAX_NIGHTS nights (a Friday->Sunday-of-next-week trip is not
    a weekend trip)."""
    flight = deal.flight
    return (
        flight.departure_date.weekday() in (_FRIDAY, _SATURDAY)
        and flight.return_date.weekday() in (_SUNDAY, _MONDAY)
        and 1 <= trip_nights(deal) <= WEEKEND_MAX_NIGHTS
    )


def comfort_highlights(deal: Deal) -> list[str]:
    """The feature lines that apply to `deal` (possibly none), weekend
    first."""
    lines: list[str] = []
    if _is_weekend_trip(deal):
        lines.append(WEEKEND_LINE)
    if _is_comfort_departure(deal.flight.departure_time):
        lines.append(COMFORT_TIME_LINE)
    return lines


# Hotel prices are for a room for this many guests - mirrors the adults=2
# default of providers/serpapi_hotels_client.py.
HOTEL_GUESTS = 2

_CURRENCY_SYMBOL = {"EUR": "€"}
_PRICE_RULE = "─" * 15  # short enough not to wrap on a phone


def _price_drop_lines(deal: Deal) -> list[str]:
    """The very top of a price-drop UPDATE alert: a prominent tag plus the
    difference to the flight price of the previous alert for this exact
    connection ("War 100 € → jetzt 79 € p.P. (-21 €, -21 %)"). Nothing for
    a first alert - or if the previous price isn't actually higher."""
    previous = deal.previous_alert_price
    if previous is None or previous <= deal.flight.price:
        return []
    now, was = _round_euros(deal.flight.price), _round_euros(previous)
    currency = deal.flight.currency
    return [
        PRICE_DROP_BANNER,
        f"War {_fmt_price(was, currency)} → jetzt <b>{_fmt_price(now, currency)}</b> p.P. "
        f"(-{_fmt_price(was - now, currency)}, -{(previous - deal.flight.price) / previous:.0%})",
    ]


def _round_euros(amount: float) -> int:
    """Commercial rounding to whole euros (181.5 -> 182). Python's round()
    is banker's rounding (102.5 -> 102), which would look wrong here."""
    return int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _fmt_price(amount: int, currency: str) -> str:
    return f"{amount} {_CURRENCY_SYMBOL.get(currency, currency)}"


def _badge_line(deal: Deal) -> str:
    """The savings badge. A real saving is "-45% günstiger als sonst". A
    deal without a (positive) overall saving - none computed, or the hotel
    side pushed the trip above baseline (see trip_combiner.py) - never
    claims one; it shows the deal-type label instead."""
    value = deal.savings_percentage
    if value is not None and value >= 0.01:
        return f"💥 <b>-{value:.0%} günstiger als sonst</b>"
    return f"💥 <b>{html.escape(deal_type_label(deal.deal_type))}</b>"


def _price_block_lines(deal: Deal) -> list[str]:
    """The cost breakdown, per person, whole euros. With a hotel: flight,
    hotel share, a rule and the bold total. Flight-only: just the flight
    line (no breakdown or total to show).

    The flight price is for 1 adult; the hotel price is a whole room for
    HOTEL_GUESTS (SerpApi Google Hotels, adults=2 by default), so the
    per-person total is flight + hotel / HOTEL_GUESTS. The total is the
    sum of the ROUNDED parts, so the printed numbers always add up. This
    is display only - Deal.actual_total_price (flight + whole hotel)
    stays the basis for filters and budgets.
    """
    flight, hotel = deal.flight, deal.accommodation
    flight_pp = _round_euros(flight.price)
    flight_line = f"✈️ Flug: <b>{_fmt_price(flight_pp, flight.currency)}</b> p.P."
    if hotel is None:
        return [flight_line]

    hotel_pp = _round_euros(hotel.total_price / HOTEL_GUESTS)
    return [
        flight_line,
        f"🏨 {html.escape(hotel.name)}: <b>{_fmt_price(hotel_pp, hotel.currency)}</b> p.P. (DZ)",
        _PRICE_RULE,
        f"💰 <b>GESAMTPREIS: {_fmt_price(flight_pp + hotel_pp, flight.currency)} p.P.</b>",
    ]


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
