"""Formats Deal objects into Markdown for an email alert / newsletter draft.

Presentation layer only - reads a Deal, never recomputes savings, score,
or baseline. Every field it reads from a Deal can legitimately be None
(no accommodation matched, no score, no booking link, ...) - this module
never fabricates a value for a missing one; it says so instead ("kein
Direktlink verfügbar" etc.), the same "never guess, say so" convention the
rest of Trip Hunter follows (see "Baseline Problem" in
docs/PRODUCT_SPEC.md).

Output language is German: unlike the developer-facing CLIs
(record_price_snapshot.py etc., which print English status lines even in
this otherwise German-documented project), this module's output is the
actual customer-facing artifact - the newsletter/alert a subscriber reads.

Booking links are affiliate-decorated via monetization/affiliate.py before
rendering - see that module for the "no tag configured -> original URL"
fallback. Deal-type labels and baseline wording come from alerts/_shared.py,
shared with html_formatter.py and instant_alert_formatter.py so the three
channels can never say something different about the same Deal.
"""

from __future__ import annotations

from trip_hunter.alerts._shared import baseline_source_note, deal_type_label, fmt_date, nights_label, trip_nights
from trip_hunter.models import Deal
from trip_hunter.monetization.affiliate import add_affiliate_tag


def format_deal(deal: Deal) -> str:
    """Format ONE deal as a self-contained Markdown block."""
    flight = deal.flight
    nights = trip_nights(deal)

    lines: list[str] = []
    lines.append(
        f"## {flight.origin} → {flight.destination} · "
        f"{fmt_date(flight.departure_date)} – {fmt_date(flight.return_date)} "
        f"({nights_label(nights)})"
    )
    lines.append("")
    lines.append(f"**{deal_type_label(deal.deal_type)}**{_savings_suffix(deal)}")
    lines.append("")
    lines.append(f"Gesamtpreis: {deal.actual_total_price:.2f} {flight.currency}")
    lines.append("")

    lines.append(_format_flight_line(flight))
    if deal.accommodation is not None:
        lines.append(_format_accommodation_line(deal.accommodation))
    lines.append("")

    cta = _format_booking_links(deal)
    if cta:
        lines.append("**Jetzt buchen:**")
        lines.extend(cta)
        lines.append("")

    lines.append(f"_{baseline_source_note(deal)}_")

    return "\n".join(lines)


def format_newsletter(deals: list[Deal], *, title: str = "Trip Hunter — Wochenend-Radar") -> str:
    """Format a whole list of (already filtered) deals into one Markdown
    newsletter draft. Honest about an empty result - never pads a run with
    nothing to say.
    """
    if not deals:
        return f"# {title}\n\nKeine passenden Deals in diesem Lauf gefunden.\n"

    header = f"# {title}\n\n{len(deals)} passende{'r' if len(deals) == 1 else ''} Deal" + (
        "" if len(deals) == 1 else "s"
    ) + " gefunden:\n"
    blocks = [format_deal(deal) for deal in deals]
    return header + "\n\n---\n\n".join(blocks) + "\n"


def _savings_suffix(deal: Deal) -> str:
    if deal.savings_absolute is None or deal.savings_percentage is None:
        return " — Ersparnis nicht verfügbar"
    return f" — Ersparnis: {deal.savings_absolute:.0f} {deal.flight.currency} ({deal.savings_percentage:.0%})"


def _format_flight_line(flight) -> str:
    stops_label = "Nonstop" if flight.stops == 0 else f"{flight.stops} Stopp(s)"
    return f"**Flug:** {flight.price:.2f} {flight.currency} · {flight.airline} · {stops_label}"


def _format_accommodation_line(accommodation) -> str:
    rating_part = f" ({accommodation.rating:.1f} ★)" if accommodation.rating is not None else ""
    return f"**Hotel:** {accommodation.name}{rating_part} · {accommodation.total_price:.2f} {accommodation.currency}"


def _format_booking_links(deal: Deal) -> list[str]:
    links: list[str] = []
    flight_link = add_affiliate_tag(deal.flight.booking_link)
    if flight_link:
        links.append(f"- [Flug buchen]({flight_link})")
    else:
        links.append("- Flug: kein Direktlink verfügbar")

    if deal.accommodation is not None:
        hotel_link = add_affiliate_tag(deal.accommodation.booking_link)
        if hotel_link:
            links.append(f"- [Hotel buchen]({hotel_link})")
        else:
            links.append("- Hotel: kein Direktlink verfügbar")

    return links
