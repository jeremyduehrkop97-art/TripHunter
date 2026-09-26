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
the same header, badge and price lines, but only the ROUGH travel period,
no hotel name and no booking link - "🔒 Hotel & Buchungslinks im VIP-Kanal" -
and two upsell buttons (`free_keyboard`: VIP checkout, explainer) instead of
the deal sheet. `format_delayed_alert` is FREE_CHANNEL_MODE=delayed_full:
the complete alert, sent to Free later. Both share `_alert_body_lines`, so
no channel can drift on the underlying facts (route, price, savings).

`_alert_body_lines` also appends a short, atmospheric destination blurb
(alerts/destination_context.py) as its own paragraph, after the price
lines - identical on both channels, since it's part of the shared body,
not either channel's link/CTA section.
"""

from __future__ import annotations

import html
import re

from datetime import time
from decimal import ROUND_HALF_UP, Decimal

from trip_hunter.alerts._shared import deal_type_label, fmt_date, nights_label, trip_nights
from trip_hunter.alerts.airport_names import city_name, flag_emoji
from trip_hunter.alerts.destination_context import destination_context
from trip_hunter.engine.alert_tier import AlertTier, classify_alert_tier
from trip_hunter.models import Deal, DealType
from trip_hunter.monetization.affiliate import add_affiliate_tag
from trip_hunter.alerts.destination_images import destination_image_url
from trip_hunter.monetization.link_builder import (
    build_deal_sheet_url,
    build_flight_link,
    build_hotel_link,
    build_share_url,
)
from trip_hunter.monetization.upsell import faq_url, free_channel_invite_url, vip_subscription_url

PRICE_DROP_BANNER = "📉 <b>PREISSTURZ: Flug nochmals günstiger!</b>"

ERROR_FARE_BANNER = "🚨 ERROR FARE: Kann sich minütlich ändern – extrem schnell buchen!"
ERROR_FARE_TIP = (
    "💡 Tipp: Erst den Flug buchen, Buchungsbestätigung abwarten und Unterkünfte "
    "erst 24–48h später final buchen (falls die Airline storniert)."
)


def format_instant_alert(deal: Deal, *, link_lines: bool = True) -> str:
    """Format ONE deal as a single, compact messenger-ready message - the
    VIP-channel voice. By default it ends with the provider's own
    affiliate-tagged booking links ("👉 ..." lines). The Telegram VIP
    channel passes `link_lines=False` and sends `alert_buttons(deal)` as
    inline buttons instead, so the same links don't appear twice."""
    lines = _alert_body_lines(deal)
    if link_lines:
        lines.extend(_link_lines(deal))
    if _is_tier_1(deal):
        lines.append(ERROR_FARE_TIP)
    return "\n".join(lines)


_FLIGHT_BUTTON_TEXT = "✈️ Flug prüfen"
_HOTEL_BUTTON_TEXT = "🏨 Hotel ansehen"


def alert_buttons(deal: Deal) -> list[list[dict[str, str]]]:
    """The inline-keyboard rows (Telegram `inline_keyboard`) for a VIP
    alert: one row with "✈️ Flug prüfen" and - if the deal has a hotel -
    "🏨 Hotel ansehen". Links come from monetization/link_builder.py and
    are affiliate-tracked only if the matching env vars are set. "prüfen"
    on purpose: the link is a search for this trip, the price may have
    moved. Never used for the Free teaser - links are the VIP feature."""
    flight = deal.flight
    row = [
        {
            "text": _FLIGHT_BUTTON_TEXT,
            "url": build_flight_link(
                flight.origin, flight.destination, flight.departure_date, flight.return_date
            ),
        }
    ]
    if deal.accommodation is not None:
        hotel = deal.accommodation
        row.append(
            {
                "text": _HOTEL_BUTTON_TEXT,
                "url": build_hotel_link(
                    hotel.name, _search_city(flight.destination), hotel.check_in, hotel.check_out
                ),
            }
        )
    return [row]


def deal_sheet_url(deal: Deal) -> str | None:
    """URL of the in-app deal sheet (web/deal.html) for `deal`, carrying the
    same numbers as the alert text (per person, whole euros, total = sum of
    the rounded parts) and the same two booking links as the buttons; None
    if the sheet is disabled (DEAL_SHEET_URL=off)."""
    flight, hotel = deal.flight, deal.accommodation
    flight_pp = _round_euros(flight.price)
    hotel_pp = _round_euros(hotel.total_price / HOTEL_GUESTS) if hotel is not None else None
    saving = deal.savings_percentage
    return build_deal_sheet_url(
        flight_link=build_flight_link(flight.origin, flight.destination, flight.departure_date, flight.return_date),
        hotel_link=(
            build_hotel_link(hotel.name, _search_city(flight.destination), hotel.check_in, hotel.check_out)
            if hotel is not None
            else None
        ),
        origin_city=city_name(flight.origin),
        destination_city=city_name(flight.destination),
        destination_code=flight.destination,
        flag=flag_emoji(flight.destination),
        departure_date=flight.departure_date,
        return_date=flight.return_date,
        flight_price=flight_pp,
        hotel_price=hotel_pp,
        total_price=flight_pp + (hotel_pp or 0),
        hotel_name=hotel.name if hotel is not None else "",
        savings_percent=round(saving * 100) if saving is not None and saving >= 0.01 else None,
        image_url=destination_image_url(flight.destination),
    )


def _total_per_person(deal: Deal) -> int:
    """Flight + hotel share per person, whole euros - the number in
    "GESAMTPREIS", the deal button and the share text."""
    total = _round_euros(deal.flight.price)
    if deal.accommodation is not None:
        total += _round_euros(deal.accommodation.total_price / HOTEL_GUESTS)
    return total


def alert_keyboards(deal: Deal) -> list[dict]:
    """Inline keyboards for a VIP alert, best first. The sender tries them
    in order and moves on only when Telegram rejects the buttons:

    1. ONE dominant Mini-App button "👉 Deal sichern (182 € p.P.)"
       (`web_app` - Telegram only allows these in private chats, so a
       channel is expected to reject it),
    2. the same single button as a plain URL button to the deal sheet
       (opens the page in Telegram's in-app browser),
    3. the two direct booking buttons (`alert_buttons`) - also the only
       option when the sheet is disabled.
    """
    keyboards: list[dict] = []
    sheet = deal_sheet_url(deal)
    if sheet is not None:
        label = f"👉 Deal sichern ({_fmt_price(_total_per_person(deal), deal.flight.currency)} p.P.)"
        keyboards.append({"inline_keyboard": [[{"text": label, "web_app": {"url": sheet}}]]})
        keyboards.append({"inline_keyboard": [[{"text": label, "url": sheet}]]})
    keyboards.append({"inline_keyboard": alert_buttons(deal)})
    return keyboards


def _search_city(code: str) -> str:
    """City name for a search query: "Faro (Algarve)" -> "Faro"."""
    return re.sub(r"\s*\(.*?\)", "", city_name(code)).strip()


def format_instant_alerts(deals: list[Deal]) -> list[str]:
    """One separate message per deal - never a combined digest. Preserves
    input order; an empty input returns an empty list (nothing to push)."""
    return [format_instant_alert(deal) for deal in deals]


def format_teaser_alert(deal: Deal) -> str:
    """The Free-channel teaser: destination, departure city, saving badge,
    only the rough travel period ("Oktober 2026, 5 Nächte") and the price
    picture - but neither the hotel's name, nor the exact dates, nor any
    booking link (those are the VIP feature, see `free_keyboard` for the
    upsell buttons). Same header/badge/price-drop/tier-1 lines as the VIP
    alert, so the two can't drift on facts."""
    lines = _alert_body_lines(deal, teaser=True)
    lines.append(_LOCK_LINE_HOTEL if deal.accommodation is not None else _LOCK_LINE_FLIGHT_ONLY)
    return "\n".join(lines)


def format_delayed_alert(deal: Deal, delay_hours: int) -> str:
    """The FREE_CHANNEL_MODE=delayed_full message: the complete VIP alert
    (no link lines - buttons carry the links), prefixed with a note that VIP
    saw it `delay_hours` earlier."""
    note = f"⏱ Dieser Deal ging vor {delay_hours} Std. an den VIP-Kanal – dort gibt es Deals sofort."
    return note + "\n" + format_instant_alert(deal, link_lines=False)


_LOCK_LINE_HOTEL = "🔒 Hotel &amp; Buchungslinks im VIP-Kanal"
_LOCK_LINE_FLIGHT_ONLY = "🔒 Buchungslinks im VIP-Kanal"

_UPSELL_BUTTON_TEXT = "⚡️ Jetzt Deal buchen (VIP freischalten)"
_FAQ_BUTTON_TEXT = "ℹ️ Wie funktioniert Trip Hunter?"


_SHARE_BUTTON_TEXT = "📲 Mit Reise-Buddy teilen"


def share_text(deal: Deal) -> str:
    """The message the share button pre-fills: destination, per-person
    total and the Free channel's invite link - and nothing else. In
    particular no booking link, deal-sheet URL or hotel name ever goes in
    here: what a friend gets is the way to JOIN, not the deal itself."""
    total = _total_per_person(deal)
    return (
        f"Schau mal, Trip Hunter hat gerade {city_name(deal.flight.destination)} für "
        f"{_fmt_price(total, deal.flight.currency)} p.P. gefunden! ✈️🏨 "
        f"Hier ist der Deal: {free_channel_invite_url()}"
    )


def free_keyboard(deal: Deal) -> dict:
    """Inline keyboard under every Free-channel teaser, one button per row:
    the VIP upsell, the word-of-mouth share button, the explainer. Plain
    URL buttons (valid in channels); no booking link is ever behind them."""
    return {
        "inline_keyboard": [
            [{"text": _UPSELL_BUTTON_TEXT, "url": vip_subscription_url()}],
            [{"text": _SHARE_BUTTON_TEXT, "url": build_share_url(share_text(deal))}],
            [{"text": _FAQ_BUTTON_TEXT, "url": faq_url()}],
        ]
    }


_MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


def rough_period(deal: Deal) -> str:
    """The travel period without exact days: "Oktober 2026, 5 Nächte"; a
    trip crossing a month or year boundary names both ("Oktober–November
    2026", "Dezember 2026–Januar 2027")."""
    start, end = deal.flight.departure_date, deal.flight.return_date
    first = f"{_MONTHS_DE[start.month - 1]} {start.year}"
    if (end.year, end.month) == (start.year, start.month):
        period = first
    elif end.year == start.year:
        period = f"{_MONTHS_DE[start.month - 1]}–{_MONTHS_DE[end.month - 1]} {start.year}"
    else:
        period = f"{first}–{_MONTHS_DE[end.month - 1]} {end.year}"
    return f"{period}, {nights_label(trip_nights(deal))}"


def _is_tier_1(deal: Deal) -> bool:
    return classify_alert_tier(deal) is AlertTier.TIER_1_ERROR_FARE


def _alert_body_lines(deal: Deal, *, teaser: bool = False) -> list[str]:
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
        (
            f"🗓 {rough_period(deal)}"
            if teaser
            else f"{fmt_date(flight.departure_date)}–{fmt_date(flight.return_date)} · {nights_label(trip_nights(deal))}"
        ),
        *comfort_highlights(deal),
    ]
    lines.extend(_price_block_lines(deal, mask_hotel=teaser))

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


def _price_block_lines(deal: Deal, *, mask_hotel: bool = False) -> list[str]:
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
    hotel_label = "Hotel im VIP-Kanal" if mask_hotel else html.escape(hotel.name)
    return [
        flight_line,
        f"🏨 {hotel_label}: <b>{_fmt_price(hotel_pp, hotel.currency)}</b> p.P. (DZ)",
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
