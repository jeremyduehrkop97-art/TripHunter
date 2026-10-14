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
from trip_hunter.engine.feed_sensor import FEED_SOURCES, DealSignal, parse_month_only_window, parse_travel_date_range
from trip_hunter.engine.flexible_dates import default_representative_month, representative_month_window
from trip_hunter.models import Deal, DealType
from trip_hunter.monetization.affiliate import add_affiliate_tag
from trip_hunter.alerts.destination_images import destination_image_url
from trip_hunter.monetization.flight_price_guide import flight_price_guide_for
from trip_hunter.monetization.hotel_price_guide import hotel_nightly_guide_price
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


def _hotel_included_in_price(signal: DealSignal) -> bool:
    """True only if the feed TITLE ITSELF says the quoted price already
    includes a hotel ("... inkl. Hotel", "Pauschalreise", ...) - a stated
    fact, never a speculative estimate, so it is shown regardless of
    whether a real date is known (see _signal_body_lines: this is checked
    BEFORE the All-in-Paket/pure-flight split, which is only about
    whether THIS module can honestly compute its OWN separate hotel
    share - doing that on top of a price that already includes one would
    double-count the hotel)."""
    return bool(_HOTEL_INCLUDED_RE.search(signal.title))


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


# --- "All-in-Paket" vs. pure flight deal: EVERY flight-lead signal -------------
#
# STRICT RULE (reported bug, 2026-10): a flight-lead signal is EITHER a
# real All-in-Paket (a real date AND a resolved hotel guide price exist -
# combined Gesamtpreis, shown identically in the message and the deal
# sheet) OR a 100% pure flight deal (no resolvable destination/hotel tier
# at all - vanishingly rare via the real dispatch pipeline, see
# hotel_price_guide.py's own docstring) that never says one single word
# about a hotel/Unterkunft anywhere, text or deal sheet - never the
# previous middle ground (a message naming a hotel "ab ca. X€" while the
# deal sheet showed no hotel price at all behind it, or a "Hotel buchen"
# button with no price to back it - the actual reported inconsistency,
# "Wien nach Boston": message promised a hotel estimate, the sheet had
# none, yet still showed a dead second button for it).
#
# A real, day-precise date ("12.10.–19.10.2026") uses that REAL date.
# Anything else - a month only ("November 2026") or no date at all
# ("Flexible Reisetermine") - uses a REPRESENTATIVE EXAMPLE window
# instead (engine/flexible_dates.representative_month_window /
# default_representative_month - "Trip Hunter soll IMMER wie ein
# geniales All-in-Urlaubspaket wirken", 2026-10 product decision) rather
# than falling back to a bare flight deal. Explicitly labelled
# "Reisebeispiel" in the message (never "Reisezeit", which implies a real
# date) - see that module's own "REPRESENTATIVE MONTH WINDOW" docstring
# section for why this is safe, unlike the retired per-signal combo
# teaser it otherwise resembles.
#
# Applies to EVERY flight-lead signal, not only engine/daily_scanner.py's
# own - a feed-radar signal naming a real date (or now, a representative
# one) and a covered destination gets the exact same combined treatment,
# since build_deal_sheet_url (signal_deal_sheet_url below) already
# computes that same combined total regardless of source; the message
# text must never show LESS than the deal sheet it links to.


@dataclass(frozen=True)
class _FlightLeadPackage:
    nights: int
    flight_pp: int  # the signal's own flight price, rounded (real & live-verified only for source="daily_scanner")
    hotel_pp: int  # hotel_price_guide.py estimate for this exact night count
    combo_total_pp: int
    departure: date  # the real date, or a representative EXAMPLE date (see is_example)
    return_date: date
    is_example: bool  # True only for a month-only/dateless representative window
    month_label: str | None = None  # "November 2026" - the ORIGINAL text (or a synthesised one) - only set if is_example


def _flight_lead_package(signal: DealSignal) -> _FlightLeadPackage | None:
    """The (flight+hotel) package for a FLIGHT-lead signal - None only if
    no hotel guide price resolves at all (no destination_iata - via the
    real dispatch pipeline's hard IATA gate this is vanishingly rare, see
    hotel_price_guide.py's own docstring) or signal.price is missing;
    either means a 100% pure flight deal instead (_pure_flight_body_lines).

    A real, day-precise date (parse_travel_date_range) is used as-is. In
    EVERY other case - a month-only string, or no date at all - a
    deterministic REPRESENTATIVE EXAMPLE window is used instead
    (engine/flexible_dates.representative_month_window), explicitly
    flagged via `is_example`/`month_label` so the message can label it
    "Reisebeispiel" rather than claim it is the feed's own real date -
    see this section's own module-level comment for why that is safe
    here. Mirrors signal_deal_sheet_url's own identical logic, so the
    message and the deal sheet can never show two different pictures of
    the same signal."""
    if signal.price is None:
        return None
    nightly = hotel_nightly_guide_price(signal.destination_iata)
    if nightly is None:
        return None

    date_range = parse_travel_date_range(signal.travel_dates)
    if date_range is not None:
        departure, return_date = date_range
        is_example, month_label = False, None
    else:
        month_year = parse_month_only_window(signal.travel_dates)
        if month_year is not None:
            month_label = signal.travel_dates
        else:
            month_year = default_representative_month()
            month_label = f"{_MONTHS_DE[month_year[0] - 1]} {month_year[1]}"
        departure, return_date = representative_month_window(*month_year, destination_iata=signal.destination_iata)
        is_example = True

    nights = (return_date - departure).days
    flight_pp = _round_euros(signal.price)
    hotel_pp = _round_euros(nightly * nights / HOTEL_GUESTS)
    return _FlightLeadPackage(
        nights=nights, flight_pp=flight_pp, hotel_pp=hotel_pp, combo_total_pp=flight_pp + hotel_pp,
        departure=departure, return_date=return_date, is_example=is_example, month_label=month_label,
    )


def _all_in_paket_body_lines(signal: DealSignal, package: _FlightLeadPackage) -> list[str]:
    """The real All-in-Paket layout - the task's own exact phrasing:
        🌴 <Nächte> <Zielstadt> inkl. Flug & 4★ Hotel
        💰 Gesamtpreis: ab <Summe> € p.P.
        (Reisezeit: ...)                      - a real, exact date
          OR (Reisebeispiel: ..., z.B. ...)    - a representative example window
        ✈️ Flug: <Flugpreis> € p.P.
        🏨 Hotel: ca. <Hotelanteil> € p.P.
    Only engine/daily_scanner.py's own signals (source=="daily_scanner")
    get the "(live geprüft)" suffix on the flight line - that price alone
    is a real, verified SerpApi search result there; a feed-radar
    signal's price is still just the feed's own unverified headline
    figure, so it must never claim to be "live checked" too. If
    signal.matrix_windows was also verified (daily_scanner only), deal.html's
    own matrix (wired through signal_deal_sheet_url) is where the
    alternate dates actually live - this text only ever names the one
    (real or representative) date the alert itself is about."""
    destination = city_name(signal.destination_iata) if signal.destination_iata else (signal.destination or "?")
    lines = [ERROR_FARE_BANNER] if signal.is_tier_1 else []
    badge = _cabin_class_badge(signal)
    if badge:
        lines.append(badge)
    lines.append(_signal_header(signal))
    lines.append("")
    lines.append(f"🌴 {nights_label(package.nights)} {html.escape(destination)} inkl. Flug &amp; 4★ Hotel")
    lines.append(f"💰 Gesamtpreis: ab {_fmt_price(package.combo_total_pp, 'EUR')} p.P.")
    if package.is_example:
        example_range = f"{package.departure:%d.%m.}–{package.return_date:%d.%m.%Y}"
        lines.append(f"(Reisebeispiel: {html.escape(package.month_label or '')}, z.B. {example_range})")
    else:
        lines.append(f"(Reisezeit: {html.escape(signal.travel_dates or '')})")
    lines.append("")
    flight_suffix = " (live geprüft)" if signal.source == "daily_scanner" else ""
    lines.append(f"✈️ Flug: {_fmt_price(package.flight_pp, 'EUR')} p.P.{flight_suffix}")
    lines.append(f"🏨 Hotel: ca. {_fmt_price(package.hotel_pp, 'EUR')} p.P. (Richtwert, DZ)")
    if len(signal.matrix_windows) >= 2:
        lines.append(f"🗓 {len(signal.matrix_windows)} Termine verfügbar - alle im Deal-Sheet wählbar.")
    return lines


def _bundled_flight_body_lines(signal: DealSignal) -> list[str]:
    """The feed's OWN title already says the quoted price includes a
    hotel ("... inkl. Hotel", "Pauschalreise", ...) - a stated fact, so
    "Hotel inkl." is shown, but NEVER a separately computed hotel share
    on top of a price that already has one baked in (that would double-
    count it) - the plain single-price layout otherwise, same skeleton as
    a pure flight deal."""
    lines = [ERROR_FARE_BANNER] if signal.is_tier_1 else []
    badge = _cabin_class_badge(signal)
    if badge:
        lines.append(badge)
    lines.append(_signal_header(signal))
    lines.append("")
    lines.append(f"🗓 Reisezeit: {html.escape(signal.travel_dates) if signal.travel_dates else _FALLBACK_TRAVEL_DATES}")
    if signal.price is not None:
        lines.append(f"💥 Preis: ab {_fmt_price(_round_euros(signal.price), 'EUR')} p.P.")
    detail = _flight_detail(signal.title)
    lines.append(f"🛫 Flug: {html.escape(detail) if detail else _FALLBACK_FLIGHT_DETAIL}")
    lines.append("🏨 Unterkunft: Hotel inkl.")
    return lines


def _pure_flight_body_lines(signal: DealSignal) -> list[str]:
    """100% pure flight deal - either no real date is known, or no hotel
    guide price resolves (in practice this project's hard IATA gate means
    the latter almost never happens for a signal that reached dispatch at
    all - see hotel_price_guide.py's own docstring - so this is really
    "no real date" in nearly every real case). NOT ONE WORD about a
    hotel/Unterkunft anywhere in this layout - the exact, literal rule:
    a signal that can't honestly show a combined package must never
    gesture at a hotel it isn't actually pricing (the reported bug this
    fixes: a message naming "Hotel ab ca. X €" for a deal whose sheet had
    no hotel price - and still a dead "Hotel buchen" button - behind it
    at all)."""
    lines = [ERROR_FARE_BANNER] if signal.is_tier_1 else []
    badge = _cabin_class_badge(signal)
    if badge:
        lines.append(badge)
    lines.append(_signal_header(signal))
    lines.append("")
    lines.append(f"🗓 Reisezeit: {html.escape(signal.travel_dates) if signal.travel_dates else _FALLBACK_TRAVEL_DATES}")
    if signal.price is not None:
        lines.append(f"💥 Preis: ab {_fmt_price(_round_euros(signal.price), 'EUR')} p.P.")
    detail = _flight_detail(signal.title)
    lines.append(f"🛫 Flug: {html.escape(detail) if detail else _FALLBACK_FLIGHT_DETAIL}")
    return lines


def _signal_body_lines(signal: DealSignal) -> list[str]:
    """Shared body for the VIP message and the Free teaser. Dispatches to
    exactly one of four layouts, in this order:

    1. HOTEL-lead signal (deal_lead="hotel") - its own "Hotel-Drop inkl.
       Flug" layout (_hotel_signal_body_lines); completely different
       header/banner (no ERROR_FARE_BANNER, no ✈️-first header).
    2. The feed title itself says the price already includes a hotel
       (_hotel_included_in_price) - _bundled_flight_body_lines ("Hotel
       inkl.", never a separately computed share on top).
    3. A resolved hotel guide price exists (_flight_lead_package) - the
       All-in-Paket layout (_all_in_paket_body_lines): combined
       Gesamtpreis, transparent flight/hotel breakdown, identical to
       what the deal sheet shows. A real exact date is used as named; a
       month-only or dateless signal gets a deterministic, explicitly
       labelled "Reisebeispiel" window instead - this is now the VAST
       majority of flight-lead signals (hotel_price_guide.py's own
       docstring: a real, resolved destination_iata always gets an
       estimate, and feed_radar.py's hard IATA gate requires one).
    4. No hotel guide price resolves at all (no destination_iata) - a
       100% pure flight deal (_pure_flight_body_lines): NOT ONE WORD
       about a hotel anywhere (see that function's own docstring for the
       exact reported bug this prevents). Vanishingly rare via the real
       dispatch pipeline.

    Every layout keeps this project's "never incomplete" rule - a fixed
    fallback in place of anything the feed title didn't name
    ("Flexible Reisetermine verfügbar" / "Hin- & Rückflug inklusive"), so
    an alert can never look like a line silently vanished. A detected
    Business/First-Class fare (signal.cabin_class) adds one dezent extra
    badge line right before the header (_cabin_class_badge) in every
    layout except the hotel-lead one. "p.P." is the source's own headline
    price as printed - unlike a Deal built from our own search, a feed
    signal never confirms whether that figure is genuinely per person;
    shown as such anyway to match this project's one fixed price-label
    convention everywhere else.
    """
    hotel_summary = _hotel_price_summary(signal)
    if hotel_summary is not None:
        return _hotel_signal_body_lines(signal, hotel_summary)

    if _hotel_included_in_price(signal):
        return _bundled_flight_body_lines(signal)

    package = _flight_lead_package(signal)
    if package is not None:
        return _all_in_paket_body_lines(signal, package)

    return _pure_flight_body_lines(signal)


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
    signal_keyboards passes this through its own, identically-named
    parameter for dispatch/telegram.py's delayed_full Free-queue item,
    which must never carry the source domain anywhere, not even inside
    this sheet's own query string.

    Flight link: `build_flight_link` either way, via Aviasales/Skyscanner
    under the configured Travelpayouts marker, same as every other flight
    link this project builds. Dated whenever `_flight_lead_package`
    resolves a window (see "Hotel link" below) - the REAL date if
    `signal.travel_dates` names a concrete day-range
    (parse_travel_date_range), else a deterministic representative
    EXAMPLE window within the named month, or next month if no month is
    named at all (engine/flexible_dates.representative_month_window /
    default_representative_month - "Trip Hunter soll IMMER wie ein
    geniales All-in-Urlaubspaket wirken", 2026-10 product decision).
    Otherwise (no hotel tier resolves at all) `departure_date=None` -
    STILL a real, monetized ROUTE search (Aviasales' own pre-filled
    search form), never a blank, routeless provider homepage. This
    project used to fan an undated signal out into a richer "flexible
    combo" teaser with its own fabricated example date, UNLABELLED as
    such (see engine/flexible_dates.py's "FORMER USE, NOW RETIRED") -
    removed, since a real, dated search link built from that fabricated
    date could (and did) show a completely different price than the one
    actually advertised, with no warning anywhere that it was only an
    example. The representative-window case above differs precisely
    there: the message explicitly says "Reisebeispiel", so a different
    real price on click-through is never a broken promise.

    Hotel link: ONLY ever built together with a computed hotel_price -
    both need `_flight_lead_package` to resolve at all (a destination
    with a hotel guide-price tier - real date or representative example
    window, either way), so the message text and this sheet never
    disagree. Neither resolving at all means a 100% pure flight deal: no
    hotel_link, no hotel_price, no hotel_name at all - never a "Hotel
    buchen" button with no price behind it (the actual reported bug this
    fixes, "Wien nach Boston": the message named a hotel estimate, the
    sheet had neither a price NOR a reason to still show the button).

    Source link: `_original_deal_link(signal)` travels in as `source_link`
    (deal.html's own dezent "Deal-Details der Quelle ansehen" text link,
    never a Telegram button - see signal_keyboards for why) whenever the
    signal has one - the verified, real-price source, regardless of
    which of the above branches the rest of the sheet took.

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

    hotel_link: str | None = None
    hotel_price: float | None = None
    total_price = signal.price

    package = _flight_lead_package(signal)
    if package is not None:
        # The ONLY branch that ever sets hotel_link - ALWAYS together with
        # hotel_price, never one without the other (the actual reported
        # bug: a "Hotel buchen" button with no price behind it at all, or
        # a price with no button - STRICT RULE above, this module's own
        # "either a real combined package, or a 100% pure flight deal").
        # `package.departure`/`return_date` is the REAL date when one is
        # known, else a deterministic representative EXAMPLE window
        # (_flight_lead_package's own docstring) - either way, both
        # booking buttons search for exactly that window, real or example.
        departure_date, return_date = package.departure, package.return_date
        flight_link = build_flight_link(signal.origins[0], destination_query, departure_date, return_date)
        hotel_link = build_hotel_link(_HOTEL_SEARCH_LABEL, destination_text, departure_date, return_date)
        hotel_price = package.hotel_pp
        total_price = package.combo_total_pp
    else:
        # No hotel guide price resolves at all (no destination_iata - via
        # the real dispatch pipeline's hard IATA gate this is vanishingly
        # rare, see hotel_price_guide.py's own docstring) - a 100% pure
        # flight deal. Still uses a real date if one happens to be named,
        # otherwise a real, monetized but DATELESS route search
        # (build_flight_link's own departure_date=None path) - never a
        # blank, routeless homepage, and never a fabricated date either.
        # No hotel_link here either way (see STRICT RULE above).
        date_range = parse_travel_date_range(signal.travel_dates)
        departure_date, return_date = date_range if date_range is not None else (None, None)
        flight_link = build_flight_link(signal.origins[0], destination_query, departure_date, return_date)

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
        # ALWAYS together with hotel_price now (hotel_link's own comment
        # above) - never a hotel name, price row OR button without one
        # another to back it.
        hotel_name=_HOTEL_SEARCH_LABEL if hotel_price is not None else "",
        source_link=source_link,
        image_url=destination_image_url(signal.destination_iata) if signal.destination_iata else None,
        # Only ever non-empty for a daily_scanner signal (DealSignal.
        # matrix_windows' own docstring - a feed-radar signal never has
        # verified alternate dates to offer here).
        windows=list(signal.matrix_windows) if signal.matrix_windows else None,
    )


_DEAL_BUTTON_TEXT = "⚡️ Jetzt Deal buchen"


def signal_keyboards(signal: DealSignal, *, include_source_link: bool = True) -> list[dict]:
    """VIP keyboards for a feed-radar signal: EXACTLY one button, our own
    deal sheet (⚡️ Jetzt Deal buchen) - NEVER a second button linking
    straight to a third-party/competing source. An earlier version of
    this function added a subordinate "Zum Original-Deal" row; removed
    entirely per the explicit rule that the Telegram channel must never
    link directly to a competing portal - deal.html's own dezent, small
    "Deal-Details der Quelle ansehen" TEXT link (never a button) is where
    that information still lives instead (see signal_deal_sheet_url).

    A Mini-App button first (Telegram only allows web_app buttons in
    private chats, so a channel is expected to reject the first variant -
    dispatch/telegram.py retries with the plain-url variant), empty if no
    honest sheet can be built at all - not reached via is_pushworthy's
    own pipeline, which already requires a resolved destination.

    `include_source_link=False` only affects the deal sheet's own "sl"
    query param (signal_deal_sheet_url's own `include_source_link` -
    deal.html's text link, unrelated to this function having no button
    for it any more) - dispatch/telegram.py's "Free channel never gets
    any source link, immediate or delayed" rule needs this for its
    delayed_full queue item, which reuses these exact VIP keyboards
    verbatim."""
    sheet = signal_deal_sheet_url(signal, include_source_link=include_source_link)
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
