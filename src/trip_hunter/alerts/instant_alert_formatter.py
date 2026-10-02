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

from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from trip_hunter.alerts._shared import deal_type_label, fmt_date, nights_label, trip_nights
from trip_hunter.alerts.airport_names import city_name, flag_emoji
from trip_hunter.alerts.destination_context import destination_context
from trip_hunter.engine.alert_tier import AlertTier, classify_alert_tier
from trip_hunter.engine.feed_sensor import DealSignal, parse_travel_date_range
from trip_hunter.engine.flexible_dates import generate_example_windows, hero_window
from trip_hunter.models import Deal, DealType
from trip_hunter.monetization.affiliate import add_affiliate_tag
from trip_hunter.alerts.destination_images import destination_image_url
from trip_hunter.monetization.flight_price_guide import flight_price_guide_for
from trip_hunter.monetization.hotel_price_guide import hotel_nightly_guide_price
from trip_hunter.monetization.link_builder import (
    build_deal_sheet_url,
    build_flight_link,
    build_generic_search_link,
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
WEEKEND_MAX_NIGHTS = 3  # <= 4 calendar days
_THURSDAY, _FRIDAY, _SATURDAY, _SUNDAY, _MONDAY = 3, 4, 5, 6, 0

COMFORT_TIME_LINE = "✨ Angenehme Flugzeiten (ab 09:00 Uhr)"
WEEKEND_BADGE = "⚡️ Wochenend-Trip"
WEEKEND_BADGE_ONE_DAY = "⚡️ Perfekt fürs Wochenende (nur 1 Urlaubstag)"


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
    """A trip over a weekend of at most 4 calendar days: out on Thursday,
    Friday or Saturday, back on the Sunday or Monday (Do-So, Fr-So, Fr-Mo,
    Sa-So, Sa-Mo). A Friday -> Sunday-of-next-week trip, or Thursday ->
    Monday (5 days), is not a weekend trip."""
    flight = deal.flight
    return (
        flight.departure_date.weekday() in (_THURSDAY, _FRIDAY, _SATURDAY)
        and flight.return_date.weekday() in (_SUNDAY, _MONDAY)
        and 1 <= trip_nights(deal) <= WEEKEND_MAX_NIGHTS
    )


def vacation_days_needed(deal: Deal) -> int:
    """Working days (Mon-Fri) the trip covers, first and last day included -
    i.e. how many days off it takes: Fr-So 1, Sa-Mo 1, Do-So 2, Fr-Mo 2,
    Sa-So 0. Public holidays are not known and not considered."""
    start, end = deal.flight.departure_date, deal.flight.return_date
    days = (end - start).days + 1
    return sum(1 for offset in range(days) if (start + timedelta(days=offset)).weekday() < 5)


def weekend_badge(deal: Deal) -> str | None:
    """The header badge for a weekend trip - None for any other trip:
    "⚡️ Perfekt fürs Wochenende (nur 1 Urlaubstag)" if one day off is
    enough, else "⚡️ Wochenend-Trip"."""
    if not _is_weekend_trip(deal):
        return None
    return WEEKEND_BADGE_ONE_DAY if vacation_days_needed(deal) == 1 else WEEKEND_BADGE


def comfort_highlights(deal: Deal) -> list[str]:
    """The feature lines under the date line that apply to `deal`
    (possibly none): the weekend badge first, then the comfortable
    departure time."""
    lines: list[str] = []
    badge = weekend_badge(deal)
    if badge is not None:
        lines.append(badge)
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


# --- feed-radar signals ("Deal-Radar": unverified third-party hints) ------------------

_SIGNAL_DISCLAIMER = "⚠️ Feed-Hinweis: Preise können sich minütlich ändern."

# A small, explicit allowlist (never guessed) of airline names that
# sometimes appear right in a feed title - used only to fill the optional
# "🛫 Details" line, never to decide anything about the deal itself.
_AIRLINE_NAMES = (
    "Ryanair", "Eurowings", "Lufthansa", "easyJet", "Wizz Air", "Condor", "Vueling", "TUI fly",
    "Air India", "Etihad Airways", "Etihad", "Emirates", "Qatar Airways", "Thai Airways",
    "Turkish Airlines", "British Airways", "KLM", "Air France", "Swiss", "Austrian Airlines",
    "Iberia", "Air Serbia", "Air China", "China Eastern", "Oman Air", "Air Arabia",
    "American Airlines", "United Airlines", "Delta", "Aer Lingus", "Norwegian", "SAS", "Finnair",
    "ITA Airways", "LOT", "Blue Air", "Volotea", "Singapore Airlines", "Cathay Pacific", "ANA",
    "JAL", "Korean Air",
)
_AIRLINE_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(name) for name in _AIRLINE_NAMES) + r")(?!\w)", re.IGNORECASE)
_NONSTOP_RE = re.compile(r"(?<!\w)(?:non-?stop|direktflug|direkte?\s+fl[üu]ge?)(?!\w)", re.IGNORECASE)
_HOTEL_INCLUDED_RE = re.compile(r"(?<!\w)(?:inkl\.?\s+hotel|mit\s+hotel|hotel\s+inklusive|pauschalreise|package)(?!\w)", re.IGNORECASE)


def _flight_detail(title: str) -> str | None:
    """"Nonstop mit Lufthansa" / "Nonstop" / "Ryanair" - only what the
    title itself actually names (the explicit airline allowlist above, or
    a nonstop/direct keyword); None omits the "🛫 Details" line entirely
    rather than showing a guess."""
    airline_match = _AIRLINE_RE.search(title)
    nonstop = _NONSTOP_RE.search(title) is not None
    airline = airline_match.group(0) if airline_match else None
    if nonstop and airline:
        return f"Nonstop mit {airline}"
    return airline or ("Nonstop" if nonstop else None)


def _accommodation_note(signal: DealSignal) -> str:
    """"Hotel inkl." only if the title actually says so. Otherwise a
    concrete nightly guide price (monetization/hotel_price_guide.py) -
    still honestly "book this separately" (never claims a firm combo
    total the way the flexible combo teaser does, since a Tier-1 signal
    reaching this plain layout means booking the flight first and waiting
    is the actual advice, see ERROR_FARE_TIP).

    The bare "Optional zubuchbar" placeholder this used to fall back to
    for an uncovered destination is gone - feed_radar.py's hard IATA gate
    (is_pushworthy) now requires a resolved destination_iata for every
    signal it lets through at all, and hotel_nightly_guide_price never
    returns None for one any more (see its own docstring), so that branch
    is unreachable via the real dispatch pipeline. The one-line fallback
    below exists only so this function itself can never crash/return
    nothing if ever called directly outside that pipeline (e.g. a test
    signal with no destination_iata at all)."""
    if _HOTEL_INCLUDED_RE.search(signal.title):
        return "Hotel inkl."
    nightly = hotel_nightly_guide_price(signal.destination_iata)
    if nightly is None:
        return "Hotel separat buchen"
    return f"{_HOTEL_SEARCH_LABEL} ab ca. {_fmt_price(nightly, 'EUR')}/Nacht (separat buchen, Richtwert)"


def _signal_header(signal: DealSignal) -> str:
    origins = " / ".join(html.escape(city_name(code)) for code in signal.origins)
    destination = city_name(signal.destination_iata) if signal.destination_iata else (signal.destination or "?")
    return f"✈️ <b>{origins} nach {html.escape(destination)}</b>"


_FALLBACK_TRAVEL_DATES = "Flexible Reisetermine verfügbar"
_FALLBACK_FLIGHT_DETAIL = "Hin- & Rückflug inklusive"


# --- "Urlaubspiraten model": flexible-date flight+hotel combo teaser ------------
#
# A feed signal with no exact date (only a rough month, or nothing at all)
# gets a richer teaser than the plain 4-line layout above: 3-4 concrete
# EXAMPLE travel windows (engine/flexible_dates.py) combined with a hotel
# guide price (monetization/hotel_price_guide.py) into an illustrative
# "from X € p.P." combo headline - the same shape Urlaubspiraten and
# similar deal blogs use. Both inputs are openly documented estimates
# (never a live quote), and every combo price line says so; see both
# modules' docstrings for exactly what "estimate" means here and why. A
# destination/price this can't honestly be built for (see
# _signal_combo_estimate) simply keeps the plain layout above instead.

# A generic "look at 4-star hotels here" search hint, deliberately not a
# specific hotel name - this project has no real accommodation data for a
# feed-radar signal at all (unlike the sampler's own SerpApi-priced Deals),
# so naming one specific hotel would itself be a fabrication.
_HOTEL_SEARCH_LABEL = "4-Sterne Hotel"

_MONTHS_SHORT_DE = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")


def _short_date_de(value: date) -> str:
    """"12. Nov." - the compact German date used only for this teaser's
    example dates (deliberately shorter than _shared.fmt_date's full
    "12.11.2026", since these are illustrative dates among several, not
    the one confirmed date of a real booking)."""
    return f"{value.day}. {_MONTHS_SHORT_DE[value.month - 1]}."


@dataclass(frozen=True)
class _ComboEstimate:
    windows: tuple[tuple[date, date], ...]
    lead_window: tuple[date, date]
    # Whether `lead_window` also happens to be the cheapest (fewest-nights)
    # of `windows` - for long-haul destinations it deliberately isn't (see
    # engine/flexible_dates.py's "HERO NIGHTS PREFERENCE"), so wording that
    # implies "nothing cheaper is shown" ("ab"/"Günstigstes Beispiel") must
    # check this rather than assume it.
    is_cheapest: bool
    nightly_guide_price: int
    flight_pp: int
    hotel_pp: int
    combo_total_pp: int


def _signal_combo_estimate(signal: DealSignal) -> _ComboEstimate | None:
    """The flexible-date combo estimate for `signal`, or None if it can't
    honestly be built - an exact date is already known
    (parse_travel_date_range succeeds, so there is nothing "flexible" to
    fan out), the destination never resolved to a real IATA code, the feed
    named no price at all, or the destination has no hotel guide-price
    tier (monetization/hotel_price_guide.py) - never a guessed number for
    an uncovered destination.

    Also never for a Tier-1 (error fare) signal: ERROR_FARE_TIP already
    advises booking the flight FIRST and waiting 24-48h before touching
    accommodation at all (the airline might cancel) - a "X Nächte inkl.
    Hotel ab Y €" combo hero pushing an immediate hotel booking would
    directly contradict that advice for the one signal type where it
    matters most.

    `signal_deal_sheet_url` and `_signal_body_lines` both call this so the
    message and the deal sheet can never disagree on the numbers.

    Never for a HOTEL-lead signal either (deal_lead="hotel") - that has
    its own, mirror-image reverse combo, see
    _signal_hotel_combo_estimate; `signal.price` there is a hotel nightly
    rate, not a flight price, so running it through this function would
    silently miscompute (and mislabel) everything."""
    if signal.deal_lead == "hotel" or signal.is_tier_1 or signal.price is None or not signal.destination_iata:
        return None
    if parse_travel_date_range(signal.travel_dates) is not None:
        return None
    nightly = hotel_nightly_guide_price(signal.destination_iata)
    if nightly is None:
        return None
    windows = generate_example_windows(signal.destination_iata)
    lead = hero_window(windows, signal.destination_iata)
    lead_nights = (lead[1] - lead[0]).days
    fewest_nights = min((return_ - departure).days for departure, return_ in windows)
    flight_pp = _round_euros(signal.price)
    hotel_pp = _round_euros(nightly * lead_nights / HOTEL_GUESTS)
    return _ComboEstimate(
        windows=windows,
        lead_window=lead,
        is_cheapest=lead_nights == fewest_nights,
        nightly_guide_price=nightly,
        flight_pp=flight_pp,
        hotel_pp=hotel_pp,
        combo_total_pp=flight_pp + hotel_pp,
    )


def _combo_body_lines(combo: _ComboEstimate) -> list[str]:
    dep, ret = combo.lead_window
    nights = (ret - dep).days
    # "ab"/"Günstigstes Beispiel" ("from"/"cheapest example") only when the
    # hero really is the cheapest of the shown windows - for a long-haul
    # destination it's deliberately the longest instead (see
    # engine/flexible_dates.py), so claiming "nothing cheaper is shown"
    # would be false; "für"/"Beispiel" ("for"/"example") makes no such claim.
    price_word, example_label = ("ab", "Günstigstes Beispiel") if combo.is_cheapest else ("für", "Beispiel")
    return [
        f"🌴 {nights_label(nights)} inkl. 4★ Hotel {price_word} {_fmt_price(combo.combo_total_pp, 'EUR')} p.P.!",
        f"({example_label}: {_short_date_de(dep)} – {_short_date_de(ret)})",
        "",
        f"🛫 Flug: Hin- & Rückflug ab {_fmt_price(combo.flight_pp, 'EUR')}",
        f"🏨 Hotel: {_HOTEL_SEARCH_LABEL} ab ca. {_fmt_price(combo.nightly_guide_price, 'EUR')}/Nacht "
        f"({_fmt_price(combo.hotel_pp, 'EUR')} p.P., Richtwert)",
        "🗓 Weitere Termine: Mehrere Beispiel-Reisezeiten verfügbar!",
    ]


# --- "Hotel-Drop inkl. Flug": the reverse combo for a HOTEL-lead signal ---------
#
# The mirror image of the "Urlaubspiraten model" above: there, a real
# flight price gets an ESTIMATED hotel nightly rate added on top; here, a
# real hotel nightly rate (the feed's own ".../Nacht" price - engine/
# feed_sensor.py's _extract_hotel_nightly_price) gets an ESTIMATED flight
# price added on top (monetization/flight_price_guide.py). Same example-
# window machinery (engine/flexible_dates.py) for "how many nights, which
# dates" either way, so the two teasers can never drift on that logic.

# A hotel-lead title rarely names any DACH departure airport at all (a
# hotel offer is origin-agnostic) - Frankfurt, the largest DACH hub and
# Lufthansa's main long-haul base, is the honest representative default
# for display/link-building ONLY (never stored in DealSignal.origins
# itself, which stays empty - truthful about what the feed actually
# said - see DealSignal's own docstring).
DEFAULT_HOTEL_DEAL_ORIGIN = "FRA"

_HOTEL_STAR_RE = re.compile(r"(\d)\s*(?:[★*]|-?\s*sterne\b)", re.IGNORECASE)


def _hotel_category_label(title: str) -> str:
    """"5★ Resort" / "4★ Villa" / "Hotel" - whatever `title` itself names
    (star rating, if any, plus resort/villa/hotel), never a specific
    hotel name (a feed title names the category, not which exact
    property - matching _HOTEL_SEARCH_LABEL's own "never invent a name"
    reasoning above)."""
    star_match = _HOTEL_STAR_RE.search(title)
    stars = f"{star_match.group(1)}★ " if star_match else ""
    # Not word-bounded for resort/villa: German compounds routinely glue
    # these onto a prefix ("Luxusresort", "Ferienresort", "Strandvilla").
    if re.search(r"resort", title, re.IGNORECASE):
        kind = "Resort"
    elif re.search(r"villa", title, re.IGNORECASE):
        kind = "Villa"
    else:
        kind = "Hotel"
    return f"{stars}{kind}"


@dataclass(frozen=True)
class _HotelComboEstimate:
    windows: tuple[tuple[date, date], ...]
    lead_window: tuple[date, date]
    nightly_hotel_price: int  # the feed's own real nightly rate, rounded
    flight_guide_price: int  # monetization/flight_price_guide.py estimate
    hotel_pp: int
    combo_total_pp: int


def _signal_hotel_combo_estimate(signal: DealSignal) -> _HotelComboEstimate | None:
    """The hotel-first reverse combo for `signal`, or None if it can't
    honestly be built - not a HOTEL-lead signal at all, or the feed named
    no nightly price (engine/feed_sensor.py's _extract_hotel_nightly_price
    already refuses to guess one from an unmarked price)."""
    if signal.deal_lead != "hotel" or signal.price is None:
        return None
    if not (signal.destination or signal.destination_iata):
        return None
    windows = generate_example_windows(signal.destination_iata)
    lead = hero_window(windows, signal.destination_iata)
    nights = (lead[1] - lead[0]).days
    hotel_pp = _round_euros(signal.price * nights / HOTEL_GUESTS)
    flight_guide = flight_price_guide_for(signal.destination_iata)
    return _HotelComboEstimate(
        windows=windows,
        lead_window=lead,
        nightly_hotel_price=_round_euros(signal.price),
        flight_guide_price=flight_guide,
        hotel_pp=hotel_pp,
        combo_total_pp=hotel_pp + flight_guide,
    )


def _hotel_body_lines(signal: DealSignal, combo: _HotelComboEstimate) -> list[str]:
    dep, ret = combo.lead_window
    nights = (ret - dep).days
    category = _hotel_category_label(signal.title)
    kind = category.split(" ")[-1]  # "5★ Resort" -> "Resort", for the 🌴 line
    origin_code = signal.origins[0] if signal.origins else DEFAULT_HOTEL_DEAL_ORIGIN
    origin = html.escape(city_name(origin_code))
    destination = city_name(signal.destination_iata) if signal.destination_iata else (signal.destination or "?")
    return [
        f"🏨 <b>{html.escape(category)} LUXUS-HOTEL DROP</b>",
        f"✈️ {origin} nach {html.escape(destination)}",
        "",
        f"🌴 {nights_label(nights)} im {kind} inkl. Flug ab {_fmt_price(combo.combo_total_pp, 'EUR')} p.P.!",
        f"(Bester Termin: {_short_date_de(dep)} – {_short_date_de(ret)})",
        "",
        f"🏨 Hotel: {html.escape(category)} ab {_fmt_price(combo.nightly_hotel_price, 'EUR')}/Nacht "
        f"({_fmt_price(combo.hotel_pp, 'EUR')} p.P.)",
        f"🛫 Flug: Hin- & Rückflug zubuchbar ab ca. {_fmt_price(combo.flight_guide_price, 'EUR')}",
        "💥 Ersparnis: Hotel stark rabattiert ggü. Normalpreis!",
    ]


def _signal_body_lines(signal: DealSignal) -> list[str]:
    """Shared body for the VIP message and the Free teaser.

    A signal with a known exact date keeps this project's fixed layout -
    every line ALWAYS appears, with a fixed fallback in place of anything
    the feed title didn't name, so a signal alert can never look
    incomplete ("Frankfurt nach Bali" missing its Reisezeit/Flug lines
    entirely was exactly this bug):
        ✈️ <Abflugstadt> nach <Zielstadt>
        🗓 Reisezeit: ...          (or "Flexible Reisetermine verfügbar")
        💥 Preis: ab <Preis> € p.P.
        🛫 Flug: ...               (or "Hin- & Rückflug inklusive")
        🏨 Unterkunft: ...
    "p.P." is the source's own headline price as printed - unlike a Deal
    built from our own search, a feed signal never confirms whether that
    figure is genuinely per person; shown as such anyway to match this
    project's one fixed price-label convention everywhere else.

    A signal with NO exact date instead gets the "Urlaubspiraten model"
    flexible combo teaser (_combo_body_lines) whenever it honestly can
    (_signal_combo_estimate) - a richer hero price plus several example
    dates - falling back to the plain layout above otherwise.

    A HOTEL-lead signal (deal_lead="hotel") skips all of the above
    entirely for its own "Hotel-Drop inkl. Flug" reverse-combo layout
    (_hotel_body_lines) - checked FIRST, since its header/banner are
    completely different (no ERROR_FARE_BANNER, no ✈️-first header).
    """
    hotel_combo = _signal_hotel_combo_estimate(signal)
    if hotel_combo is not None:
        return _hotel_body_lines(signal, hotel_combo)

    lines = [ERROR_FARE_BANNER] if signal.is_tier_1 else []
    lines.append(_signal_header(signal))
    lines.append("")

    combo = _signal_combo_estimate(signal)
    if combo is not None:
        lines.extend(_combo_body_lines(combo))
        return lines

    if signal.travel_dates:
        lines.append(f"🗓 Reisezeit: {html.escape(signal.travel_dates)}")
    else:
        lines.append(f"🗓 Reisezeit: {_FALLBACK_TRAVEL_DATES}")
    if signal.price is not None:
        lines.append(f"💥 Preis: ab {_fmt_price(_round_euros(signal.price), 'EUR')} p.P.")
    detail = _flight_detail(signal.title)
    if detail:
        lines.append(f"🛫 Flug: {html.escape(detail)}")
    else:
        lines.append(f"🛫 Flug: {_FALLBACK_FLIGHT_DETAIL}")
    lines.append(f"🏨 Unterkunft: {_accommodation_note(signal)}")
    return lines


_HOTEL_SIGNAL_DISCLAIMER = "⚠️ Feed-Hinweis: Hotelpreise und Flugverfügbarkeit können sich minütlich ändern."


def format_signal_alert(signal: DealSignal) -> str:
    """VIP message for a feed-radar signal: the fixed layout above, plus
    the "prices change fast" disclaimer and, for an error fare, the
    book-the-flight-first tip. The button (`signal_keyboards`) always
    opens our own deal sheet - never the third-party source article."""
    lines = _signal_body_lines(signal)
    disclaimer = _HOTEL_SIGNAL_DISCLAIMER if signal.deal_lead == "hotel" else _SIGNAL_DISCLAIMER
    lines += ["", disclaimer]
    if signal.is_tier_1:
        lines.append(ERROR_FARE_TIP)
    return "\n".join(lines)


def format_signal_teaser(signal: DealSignal) -> str:
    """Free-channel teaser: the same fixed layout, but the source is
    never named and its link never appears anywhere in the message."""
    return "\n".join([*_signal_body_lines(signal), "", "🔒 Quelle & Deal-Link im VIP-Kanal"])


def signal_deal_sheet_url(signal: DealSignal) -> str | None:
    """URL of our own in-app deal sheet (web/deal.html) for a feed-radar
    signal - never the third-party source article, so no foreign link
    ever ends up behind a button. None if the sheet is disabled or the
    destination is unknown.

    Flight link: if `signal.travel_dates` names a concrete day-range
    ("12.10.–19.10.2026" - parse_travel_date_range), it's a real
    `build_flight_link` search for those exact dates - Aviasales/
    Skyscanner via the configured Travelpayouts marker, same as every
    other flight link this project builds, so a feed-radar deal earns
    commission too. A feed headline naming only a month, or nothing at
    all falls back to the flexible-date combo (_signal_combo_estimate) if
    one can honestly be built - the sheet then also carries a "windows"
    matrix of the other example dates (build_deal_sheet_url), each with
    its own real, dated flight/hotel links. Failing that too (an
    uncovered destination), it's a plain dateless Google Flights search
    instead (never a fabricated date) - deal.html's own date line is
    simply omitted then.

    Hotel link: even outside the combo case (a Tier-1 error fare, which
    _signal_combo_estimate always excludes - see its docstring - or a
    destination with no example-window coverage at all), a destination
    with a hotel guide-price tier still gets a real hotel search link -
    dated (and its price/total computed) if an exact date is known, else
    a plain dateless Google Hotels search - rather than no hotel link at
    all. Matches _accommodation_note's "no more bare 'Optional
    zubuchbar'" fix in the message text.

    A HOTEL-lead signal (deal_lead="hotel") is handled entirely separately
    (_hotel_combo_deal_sheet_url) - it never requires `signal.origins` at
    all (see DEFAULT_HOTEL_DEAL_ORIGIN), only a destination.
    """
    destination_text = city_name(signal.destination_iata) if signal.destination_iata else signal.destination
    if not destination_text:
        return None
    destination_query = signal.destination_iata or destination_text

    hotel_combo = _signal_hotel_combo_estimate(signal)
    if hotel_combo is not None:
        origin_code = signal.origins[0] if signal.origins else DEFAULT_HOTEL_DEAL_ORIGIN
        return _hotel_combo_deal_sheet_url(
            signal, hotel_combo, city_name(origin_code), destination_text, destination_query, origin_code
        )
    if signal.deal_lead == "hotel":
        return None  # hotel-lead but no honest combo possible - never fabricate one

    if not signal.origins:
        return None
    origin_city = city_name(signal.origins[0])

    combo = _signal_combo_estimate(signal)
    if combo is not None:
        return _combo_deal_sheet_url(signal, combo, origin_city, destination_text, destination_query)

    nightly = hotel_nightly_guide_price(signal.destination_iata)
    hotel_link: str | None = None
    hotel_price: float | None = None
    total_price = signal.price

    date_range = parse_travel_date_range(signal.travel_dates)
    if date_range is not None:
        departure_date, return_date = date_range
        flight_link = build_flight_link(signal.origins[0], destination_query, departure_date, return_date)
        if nightly is not None:
            hotel_link = build_hotel_link(_HOTEL_SEARCH_LABEL, destination_text, departure_date, return_date)
            nights = (return_date - departure_date).days
            hotel_price = _round_euros(nightly * nights / HOTEL_GUESTS)
            if signal.price is not None:
                total_price = _round_euros(signal.price) + hotel_price
    else:
        departure_date = return_date = None
        flight_link = build_generic_search_link(origin_city, destination_text)
        if nightly is not None:
            hotel_link = build_hotel_link(_HOTEL_SEARCH_LABEL, destination_text, provider="google")

    return build_deal_sheet_url(
        flight_link=flight_link,
        hotel_link=hotel_link,
        origin_city=origin_city,
        destination_city=destination_text,
        destination_code=signal.destination_iata or "",
        flag=flag_emoji(signal.destination_iata) if signal.destination_iata else "",
        departure_date=departure_date,
        return_date=return_date,
        flight_price=signal.price,
        hotel_price=hotel_price,
        total_price=total_price,
        hotel_name=_HOTEL_SEARCH_LABEL if hotel_link else "",
        image_url=destination_image_url(signal.destination_iata) if signal.destination_iata else None,
    )


def _combo_deal_sheet_url(
    signal: DealSignal,
    combo: _ComboEstimate,
    origin_city: str,
    destination_text: str,
    destination_query: str,
) -> str | None:
    """The deal sheet for a flexible-date combo signal: the header shows
    the cheapest example window (real, dated flight+hotel links), and
    every example window (including that same cheapest one, so the matrix
    is complete on its own) travels in the "windows" parameter as its own
    dated, real, markered links plus its own combo total."""
    windows = []
    for departure, return_ in combo.windows:
        nights = (return_ - departure).days
        hotel_pp = _round_euros(combo.nightly_guide_price * nights / HOTEL_GUESTS)
        windows.append(
            {
                "dep": departure.isoformat(),
                "ret": return_.isoformat(),
                "tp": combo.flight_pp + hotel_pp,
                "fl": build_flight_link(signal.origins[0], destination_query, departure, return_),
                "hl": build_hotel_link(_HOTEL_SEARCH_LABEL, destination_text, departure, return_),
            }
        )
    lead_departure, lead_return = combo.lead_window
    return build_deal_sheet_url(
        flight_link=build_flight_link(signal.origins[0], destination_query, lead_departure, lead_return),
        hotel_link=build_hotel_link(_HOTEL_SEARCH_LABEL, destination_text, lead_departure, lead_return),
        origin_city=origin_city,
        destination_city=destination_text,
        destination_code=signal.destination_iata or "",
        flag=flag_emoji(signal.destination_iata) if signal.destination_iata else "",
        departure_date=lead_departure,
        return_date=lead_return,
        flight_price=combo.flight_pp,
        hotel_price=combo.hotel_pp,
        total_price=combo.combo_total_pp,
        hotel_name=_HOTEL_SEARCH_LABEL,
        image_url=destination_image_url(signal.destination_iata),
        windows=windows,
    )


def _hotel_combo_deal_sheet_url(
    signal: DealSignal,
    combo: _HotelComboEstimate,
    origin_city: str,
    destination_text: str,
    destination_query: str,
    origin_code: str,
) -> str | None:
    """The deal sheet for a hotel-first reverse-combo signal: the mirror
    image of _combo_deal_sheet_url - a real, dated Booking.com search for
    the feed's own hotel category (🏨 button) plus an estimated, dated
    Aviasales/Skyscanner flight search from DEFAULT_HOTEL_DEAL_ORIGIN or
    whatever real origin the title did happen to name (✈️ button), same
    "windows" matrix mechanism for the other example dates."""
    category = _hotel_category_label(signal.title)
    windows = []
    for departure, return_ in combo.windows:
        nights = (return_ - departure).days
        hotel_pp = _round_euros(combo.nightly_hotel_price * nights / HOTEL_GUESTS)
        windows.append(
            {
                "dep": departure.isoformat(),
                "ret": return_.isoformat(),
                "tp": hotel_pp + combo.flight_guide_price,
                "fl": build_flight_link(origin_code, destination_query, departure, return_),
                "hl": build_hotel_link(category, destination_text, departure, return_),
            }
        )
    lead_departure, lead_return = combo.lead_window
    return build_deal_sheet_url(
        flight_link=build_flight_link(origin_code, destination_query, lead_departure, lead_return),
        hotel_link=build_hotel_link(category, destination_text, lead_departure, lead_return),
        origin_city=origin_city,
        destination_city=destination_text,
        destination_code=signal.destination_iata or "",
        flag=flag_emoji(signal.destination_iata) if signal.destination_iata else "",
        departure_date=lead_departure,
        return_date=lead_return,
        flight_price=combo.flight_guide_price,
        hotel_price=combo.hotel_pp,
        total_price=combo.combo_total_pp,
        hotel_name=category,
        image_url=destination_image_url(signal.destination_iata) if signal.destination_iata else None,
        windows=windows,
    )


_DEAL_BUTTON_TEXT = "⚡️ Jetzt Deal buchen"


def signal_keyboards(signal: DealSignal) -> list[dict]:
    """VIP keyboards for a feed-radar signal, best first: a Mini-App
    button opening our deal sheet, then the same URL as a plain button
    (Telegram only allows web_app buttons in private chats, so a channel
    is expected to reject the first one - dispatch/telegram.py retries
    with the next). Empty if the sheet couldn't be built (no destination),
    which the signal shouldn't even have reached given `is_pushworthy`."""
    sheet = signal_deal_sheet_url(signal)
    if sheet is None:
        return []
    return [
        {"inline_keyboard": [[{"text": _DEAL_BUTTON_TEXT, "web_app": {"url": sheet}}]]},
        {"inline_keyboard": [[{"text": _DEAL_BUTTON_TEXT, "url": sheet}]]},
    ]


def signal_share_text(signal: DealSignal) -> str:
    """Share text for a signal: destination and price only, plus the
    invite link - never the source article. For a HOTEL-lead signal,
    `signal.price` is a nightly rate, not a trip total - "Bali ab 45 €"
    would badly undersell/mislead a friend reading it as the whole trip,
    so this uses the combo total (hotel+flight) instead, same as the
    message itself shows."""
    city = city_name(signal.destination_iata) if signal.destination_iata else (signal.destination or "")
    hotel_combo = _signal_hotel_combo_estimate(signal)
    if city and hotel_combo is not None:
        what = f"{city} ab {_fmt_price(hotel_combo.combo_total_pp, 'EUR')} inkl. Flug"
    elif city and signal.price is not None:
        what = f"{city} ab {_fmt_price(_round_euros(signal.price), 'EUR')}"
    else:
        what = city or "einen Deal"
    return f"Schau mal, Trip Hunter hat gerade {what} gefunden! ✈️ Hier ist der Deal: {free_channel_invite_url()}"


def signal_free_keyboard(signal: DealSignal) -> dict:
    """Free teaser buttons for a signal: VIP upsell, share, explainer."""
    return {
        "inline_keyboard": [
            [{"text": _UPSELL_BUTTON_TEXT, "url": vip_subscription_url()}],
            [{"text": _SHARE_BUTTON_TEXT, "url": build_share_url(signal_share_text(signal))}],
            [{"text": _FAQ_BUTTON_TEXT, "url": faq_url()}],
        ]
    }


def format_delayed_signal_alert(signal: DealSignal, delay_hours: int) -> str:
    """FREE_CHANNEL_MODE=delayed_full for a signal: the full VIP message,
    prefixed with the note that VIP saw it `delay_hours` earlier."""
    note = f"⏱ Dieser Hinweis ging vor {delay_hours} Std. an den VIP-Kanal – dort gibt es Deals sofort."
    return note + "\n" + format_signal_alert(signal)
