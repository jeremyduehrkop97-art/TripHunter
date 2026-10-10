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
from trip_hunter.engine.feed_sensor import FEED_SOURCES, DealSignal, parse_travel_date_range
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


# Dezent cabin-class badge - only for the two classes
# engine/route_benchmark.get_route_benchmark actually judges against their
# OWN (much higher) Business-tier benchmark, never for "economy"/
# "premium_economy" (those are judged against the plain Economy
# benchmark - see that module's docstring - so a distinct badge there
# would overstate what was actually verified).
_CABIN_CLASS_BADGES: dict[str, str] = {
    "business": "👔 Business Class Deal",
    "first": "👔 First Class Deal",
}


def _cabin_class_badge(signal: DealSignal) -> str | None:
    return _CABIN_CLASS_BADGES.get(signal.cabin_class)


_FALLBACK_TRAVEL_DATES = "Flexible Reisetermine verfügbar"
_FALLBACK_FLIGHT_DETAIL = "Hin- & Rückflug inklusive"


# A generic "look at 4-star hotels here" search hint, deliberately not a
# specific hotel name - this project has no real accommodation data for a
# feed-radar signal at all (unlike the sampler's own SerpApi-priced Deals),
# so naming one specific hotel would itself be a fabrication.
_HOTEL_SEARCH_LABEL = "4-Sterne Hotel"


# --- "Hotel-Drop inkl. Flug": the HOTEL-lead signal's own body -----------------
#
# A real hotel nightly rate (the feed's own ".../Nacht" price - engine/
# feed_sensor.py's _extract_hotel_nightly_price) with an ESTIMATED flight
# price added on top (monetization/flight_price_guide.py) - but ONLY when
# a real, exact date is actually named in the feed (parse_travel_date_range);
# never a fabricated window any more (engine/flexible_dates.py's own
# "FORMER USE, NOW RETIRED" - a fabricated date fed into a real, dated
# search link caused exactly the price mismatch on click-through this
# project now avoids). No real date at all means a plain nightly-rate
# line, never an invented multi-night total.

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
class _HotelPriceSummary:
    nightly: int  # the feed's own real nightly rate, rounded - always set
    # The rest are only ever set together, and only when signal.travel_dates
    # names a REAL, exact date range (parse_travel_date_range) - never a
    # fabricated one. All None together means "nightly rate only, no
    # honest combo total exists".
    departure: date | None = None
    return_date: date | None = None
    flight_guide_price: int | None = None  # monetization/flight_price_guide.py estimate
    hotel_pp: int | None = None  # nightly * real nights / HOTEL_GUESTS
    combo_total_pp: int | None = None  # hotel_pp + flight_guide_price


def _hotel_price_summary(signal: DealSignal) -> _HotelPriceSummary | None:
    """The honest price picture for a HOTEL-lead `signal`, or None if it
    can't honestly be built at all - not a HOTEL-lead signal, or the feed
    named no nightly price (engine/feed_sensor.py's
    _extract_hotel_nightly_price already refuses to guess one from an
    unmarked price). Always includes the feed's own real nightly rate;
    the (hotel+flight) combo total ONLY when signal.travel_dates names a
    real, exact date range - never a fabricated window (see this module's
    "Hotel-Drop inkl. Flug" section header for why)."""
    if signal.deal_lead != "hotel" or signal.price is None:
        return None
    nightly = _round_euros(signal.price)
    date_range = parse_travel_date_range(signal.travel_dates)
    if date_range is None:
        return _HotelPriceSummary(nightly=nightly)
    departure, return_date = date_range
    nights = (return_date - departure).days
    flight_guide = flight_price_guide_for(signal.destination_iata)
    hotel_pp = _round_euros(signal.price * nights / HOTEL_GUESTS)
    return _HotelPriceSummary(
        nightly=nightly, departure=departure, return_date=return_date,
        flight_guide_price=flight_guide, hotel_pp=hotel_pp, combo_total_pp=hotel_pp + flight_guide,
    )


def _hotel_signal_body_lines(signal: DealSignal, summary: _HotelPriceSummary) -> list[str]:
    category = _hotel_category_label(signal.title)
    kind = category.split(" ")[-1]  # "5★ Resort" -> "Resort", for the 🌴 line
    origin_code = signal.origins[0] if signal.origins else DEFAULT_HOTEL_DEAL_ORIGIN
    origin = html.escape(city_name(origin_code))
    destination = city_name(signal.destination_iata) if signal.destination_iata else (signal.destination or "?")
    lines = [
        f"🏨 <b>{html.escape(category)} LUXUS-HOTEL DROP</b>",
        f"✈️ {origin} nach {html.escape(destination)}",
        "",
    ]
    if summary.combo_total_pp is not None:
        nights = (summary.return_date - summary.departure).days
        lines.append(
            f"🌴 {nights_label(nights)} im {kind} inkl. Flug ab {_fmt_price(summary.combo_total_pp, 'EUR')} p.P.!"
        )
        lines.append(f"(Reisezeit: {html.escape(signal.travel_dates)})")
        lines.append("")
        lines.append(
            f"🏨 Hotel: {html.escape(category)} ab {_fmt_price(summary.nightly, 'EUR')}/Nacht "
            f"({_fmt_price(summary.hotel_pp, 'EUR')} p.P.)"
        )
        lines.append(f"🛫 Flug: Hin- & Rückflug zubuchbar ab ca. {_fmt_price(summary.flight_guide_price, 'EUR')}")
    else:
        # No real date named at all - the honest, plain nightly rate only,
        # never an invented multi-night total or "Bester Termin".
        lines.append(f"🏨 {kind}: ab {_fmt_price(summary.nightly, 'EUR')}/Nacht")
        lines.append("🛫 Flug: separat buchen")
    lines.append("💥 Ersparnis: Hotel stark rabattiert ggü. Normalpreis!")
    return lines


def _signal_body_lines(signal: DealSignal) -> list[str]:
    """Shared body for the VIP message and the Free teaser.

    A signal with a known exact date keeps this project's fixed layout -
    every line ALWAYS appears, with a fixed fallback in place of anything
    the feed title didn't name, so a signal alert can never look
    incomplete ("Frankfurt nach Bali" missing its Reisezeit/Flug lines
    entirely was exactly this bug). A detected Business/First-Class fare
    (signal.cabin_class, see engine/feed_sensor._extract_cabin_class) adds
    one dezent extra badge line right before the header (_cabin_class_badge)
    - never for plain Economy/Premium Economy, which keep this exact layout:
        [👔 Business Class Deal]   (only for cabin_class "business"/"first")
        ✈️ <Abflugstadt> nach <Zielstadt>
        🗓 Reisezeit: ...          (or "Flexible Reisetermine verfügbar")
        💥 Preis: ab <Preis> € p.P.
        🛫 Flug: ...               (or "Hin- & Rückflug inklusive")
        🏨 Unterkunft: ...
    "p.P." is the source's own headline price as printed - unlike a Deal
    built from our own search, a feed signal never confirms whether that
    figure is genuinely per person; shown as such anyway to match this
    project's one fixed price-label convention everywhere else.

    A signal with NO exact date keeps this exact same plain layout -
    "Flexible Reisetermine verfügbar" in place of a specific Reisezeit,
    the real signal.price as printed, never a fabricated example date (see
    engine/flexible_dates.py's "FORMER USE, NOW RETIRED" for why this
    module used to fan an undated signal out into a richer "Urlaubspiraten
    model" combo teaser with its own fabricated date, and no longer does).

    A HOTEL-lead signal (deal_lead="hotel") skips all of the above
    entirely for its own "Hotel-Drop inkl. Flug" layout
    (_hotel_signal_body_lines) - checked FIRST, since its header/banner are
    completely different (no ERROR_FARE_BANNER, no ✈️-first header).
    """
    hotel_summary = _hotel_price_summary(signal)
    if hotel_summary is not None:
        return _hotel_signal_body_lines(signal, hotel_summary)

    lines = [ERROR_FARE_BANNER] if signal.is_tier_1 else []
    badge = _cabin_class_badge(signal)
    if badge:
        lines.append(badge)
    lines.append(_signal_header(signal))
    lines.append("")

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


def _original_deal_link(signal: DealSignal) -> str | None:
    """`signal.link`, but ONLY for a signal that genuinely came from one
    of our registered feed sources (engine/feed_sensor.FEED_SOURCES) - an
    internal/synthetic source (e.g. engine/daily_scanner.py's own
    source="daily_scanner", whose `link` is a placeholder page on our own
    domain, never a real third-party article) has no "original deal" to
    send anyone to, so this is None for it rather than a dead/fake link.
    Also None for a non-https link - same "https only" rule as every
    other outbound link this project builds (see web/deal.html's own
    safeUrl), never relaxed just because this one skips the usual host
    allowlist check (deal.html's SOURCE_LINK_HOSTS does that instead)."""
    if signal.source not in FEED_SOURCES or not signal.link or not signal.link.startswith("https://"):
        return None
    return signal.link


def signal_deal_sheet_url(signal: DealSignal, *, include_source_link: bool = True) -> str | None:
    """URL of our own in-app deal sheet (web/deal.html) for a feed-radar
    signal. None if the sheet is disabled or the destination is unknown.

    `include_source_link=False` omits the "sl" param (see "Source link"
    below) even for a signal that would otherwise get one -
    signal_keyboards passes this through its own `include_original_deal`
    for dispatch/telegram.py's delayed_full Free-queue item, which must
    never carry the source domain anywhere, not even inside this sheet's
    own query string.

    Flight link: if `signal.travel_dates` names a concrete day-range
    ("12.10.–19.10.2026" - parse_travel_date_range), it's a real
    `build_flight_link` search for those exact dates - Aviasales/
    Skyscanner via the configured Travelpayouts marker, same as every
    other flight link this project builds, so a feed-radar deal earns
    commission too. A feed headline naming only a month, or nothing at
    all gets a plain DATELESS Google Flights search instead
    (build_generic_search_link) - never a fabricated single date. This
    project used to fan an undated signal out into a richer "flexible
    combo" teaser with its own fabricated example date (see
    engine/flexible_dates.py's "FORMER USE, NOW RETIRED") - removed, since
    a real, dated search link built from that fabricated date could (and
    did) show a completely different price than the one actually
    advertised.

    Hotel link: a destination with a hotel guide-price tier still gets a
    real hotel search link either way - dated (and its price/total
    computed) if an exact date is known, else a plain dateless Google
    Hotels search - rather than no hotel link at all. Matches
    _accommodation_note's "no more bare 'Optional zubuchbar'" fix in the
    message text.

    Source link: `_original_deal_link(signal)` travels in as `source_link`
    (deal.html's own "Zum Original-Deal" button) whenever the signal has
    one - the verified, real-price source, regardless of which of the
    above branches the rest of the sheet took.

    A HOTEL-lead signal (deal_lead="hotel") is handled entirely separately
    below - it never requires `signal.origins` at all (see
    DEFAULT_HOTEL_DEAL_ORIGIN), only a destination, and returns None
    outright with no real date known at all (never a fabricated one -
    see _hotel_price_summary).
    """
    destination_text = city_name(signal.destination_iata) if signal.destination_iata else signal.destination
    if not destination_text:
        return None
    destination_query = signal.destination_iata or destination_text
    source_link = _original_deal_link(signal) if include_source_link else None

    hotel_summary = _hotel_price_summary(signal)
    if hotel_summary is not None:
        if hotel_summary.combo_total_pp is None:
            return None  # hotel-lead, no real date at all - no honest sheet, never fabricate one
        origin_code = signal.origins[0] if signal.origins else DEFAULT_HOTEL_DEAL_ORIGIN
        category = _hotel_category_label(signal.title)
        return build_deal_sheet_url(
            flight_link=build_flight_link(
                origin_code, destination_query, hotel_summary.departure, hotel_summary.return_date
            ),
            hotel_link=build_hotel_link(
                category, destination_text, hotel_summary.departure, hotel_summary.return_date
            ),
            origin_city=city_name(origin_code),
            destination_city=destination_text,
            destination_code=signal.destination_iata or "",
            flag=flag_emoji(signal.destination_iata) if signal.destination_iata else "",
            departure_date=hotel_summary.departure,
            return_date=hotel_summary.return_date,
            flight_price=hotel_summary.flight_guide_price,
            hotel_price=hotel_summary.hotel_pp,
            total_price=hotel_summary.combo_total_pp,
            hotel_name=category,
            source_link=source_link,
            image_url=destination_image_url(signal.destination_iata) if signal.destination_iata else None,
        )

    if not signal.origins:
        return None
    origin_city = city_name(signal.origins[0])

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
        source_link=source_link,
        image_url=destination_image_url(signal.destination_iata) if signal.destination_iata else None,
    )


_DEAL_BUTTON_TEXT = "⚡️ Jetzt Deal buchen"
_ORIGINAL_DEAL_BUTTON_TEXT = "🔗 Zum Original-Deal"


def signal_keyboards(signal: DealSignal, *, include_original_deal: bool = True) -> list[dict]:
    """VIP keyboards for a feed-radar signal. At most two rows:
    1. our own deal sheet (⚡️ Jetzt Deal buchen) - the ONE dominant,
       primary CTA, a Mini-App button first (Telegram only allows
       web_app buttons in private chats, so a channel is expected to
       reject the first variant - dispatch/telegram.py retries with the
       plain-url variant), omitted when no honest sheet can be built at
       all;
    2. the feed's own real article (🔗 Zum Original-Deal), whenever the
       signal genuinely has one (_original_deal_link) - always a
       SUBORDINATE second row, never above the monetized booking button
       above (the task's own explicit conversion/branding fix - an
       earlier version of this function had these the other way round);
       always a plain url button (a third-party site is never one of
       our own registered Mini Apps).
    Empty only if NEITHER exists - not reached via is_pushworthy's own
    pipeline for any registered-feed-source signal, since every such
    signal has a real `link`.

    `include_original_deal=False` builds the SAME keyboards without row 2
    AND without the deal sheet's own "sl" param (signal_deal_sheet_url's
    `include_source_link`) - dispatch/telegram.py's own "Free channel
    never gets any source link, immediate or delayed" rule (see
    free_keyboard/signal_free_keyboard, which already never included a
    source link) needs this for its delayed_full queue item, which
    otherwise reuses these exact VIP keyboards verbatim."""
    sheet = signal_deal_sheet_url(signal, include_source_link=include_original_deal)
    original = _original_deal_link(signal) if include_original_deal else None

    def rows(sheet_button: dict | None) -> list[list[dict]]:
        result = [[sheet_button]] if sheet_button is not None else []
        if original:
            result.append([{"text": _ORIGINAL_DEAL_BUTTON_TEXT, "url": original}])
        return result

    if sheet is None:
        return [{"inline_keyboard": rows(None)}] if original else []
    return [
        {"inline_keyboard": rows({"text": _DEAL_BUTTON_TEXT, "web_app": {"url": sheet}})},
        {"inline_keyboard": rows({"text": _DEAL_BUTTON_TEXT, "url": sheet})},
    ]


def signal_share_text(signal: DealSignal) -> str:
    """Share text for a signal: destination and price only, plus the
    invite link - never the source article. For a HOTEL-lead signal,
    `signal.price` is a nightly rate, not a trip total - "Bali ab 45 €"
    would badly undersell/mislead a friend reading it as the whole trip,
    so this uses the (hotel+flight) combo total instead whenever a real
    date makes one honestly available (same as the message itself shows),
    else the plain nightly rate, clearly labelled per night."""
    city = city_name(signal.destination_iata) if signal.destination_iata else (signal.destination or "")
    hotel_summary = _hotel_price_summary(signal)
    if city and hotel_summary is not None and hotel_summary.combo_total_pp is not None:
        what = f"{city} ab {_fmt_price(hotel_summary.combo_total_pp, 'EUR')} inkl. Flug"
    elif city and hotel_summary is not None:
        what = f"{city} ab {_fmt_price(hotel_summary.nightly, 'EUR')}/Nacht"
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
