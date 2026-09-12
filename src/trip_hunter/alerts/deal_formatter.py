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
"""

from __future__ import annotations

from trip_hunter.models import BaselineSource, Deal, DealType

_DEAL_TYPE_LABELS: dict[DealType, str] = {
    DealType.COMBINED_TRIP_DROP: "Top-Kombi-Deal",
    DealType.FLIGHT_DROP: "Günstiger Flug",
    DealType.HOTEL_DROP: "Günstiges Hotel",
    DealType.ERROR_FARE: "Möglicher Error Fare",
    DealType.UNUSUALLY_LOW: "Auffällig günstig",
    DealType.BASELINE_UNAVAILABLE: "Kein Vergleichswert verfügbar",
    DealType.PRICE_INCOMPLETE: "Preis unbestätigt",
}


def format_deal(deal: Deal) -> str:
    """Format ONE deal as a self-contained Markdown block."""
    flight = deal.flight
    nights = (flight.return_date - flight.departure_date).days

    lines: list[str] = []
    lines.append(
        f"## {flight.origin} → {flight.destination} · "
        f"{_fmt_date(flight.departure_date)} – {_fmt_date(flight.return_date)} "
        f"({nights} {'Nacht' if nights == 1 else 'Nächte'})"
    )
    lines.append("")
    lines.append(f"**{_DEAL_TYPE_LABELS.get(deal.deal_type, deal.deal_type.value)}**"
                 f"{_savings_suffix(deal)}")
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

    lines.append(f"_{_baseline_source_note(deal)}_")

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
    if deal.flight.booking_link:
        links.append(f"- [Flug buchen]({deal.flight.booking_link})")
    else:
        links.append("- Flug: kein Direktlink verfügbar")

    if deal.accommodation is not None:
        if deal.accommodation.booking_link:
            links.append(f"- [Hotel buchen]({deal.accommodation.booking_link})")
        else:
            links.append("- Hotel: kein Direktlink verfügbar")

    return links


def _baseline_source_note(deal: Deal) -> str:
    if deal.baseline_source is BaselineSource.OWN_HISTORICAL_BASELINE:
        if deal.historical_baseline is not None:
            median = deal.historical_baseline.statistics.median
            return f"Vergleichswert: eigene Preishistorie (Median: {median:.2f} {deal.flight.currency})"
        return "Vergleichswert: eigene Preishistorie"
    if deal.baseline_source is BaselineSource.PROVIDER_PRICE_INSIGHT:
        source = deal.price_insight.source if deal.price_insight is not None else "Provider"
        return f"Vergleichswert: Preis-Einschätzung von {source} (keine eigene Historie)"
    return "Vergleichswert: nicht verfügbar"


def _fmt_date(value) -> str:
    return value.strftime("%d.%m.%Y")
