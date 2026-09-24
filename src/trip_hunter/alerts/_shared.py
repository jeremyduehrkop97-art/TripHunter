"""Shared, format-agnostic presentation helpers for the Markdown, HTML, and
instant-alert formatters. Deal-type labels and baseline-source wording must
never drift between channels, so they live here once instead of being
copied into deal_formatter.py / html_formatter.py / instant_alert_formatter.py
separately.

Still presentation only: computes no deal, score, or baseline itself.
"""

from __future__ import annotations

from trip_hunter.models import BaselineSource, Deal, DealType

DEAL_TYPE_LABELS: dict[DealType, str] = {
    DealType.COMBINED_TRIP_DROP: "Top-Kombi-Deal",
    DealType.FLIGHT_DROP: "Günstiger Flug",
    DealType.HOTEL_DROP: "Günstiges Hotel",
    DealType.ERROR_FARE: "Möglicher Error Fare",
    DealType.UNUSUALLY_LOW: "Auffällig günstig",
    DealType.BASELINE_UNAVAILABLE: "Kein Vergleichswert verfügbar",
    DealType.PRICE_INCOMPLETE: "Preis unbestätigt",
}


def deal_type_label(deal_type: DealType) -> str:
    """A customer-friendly German label - falls back to the raw enum value
    for any (hypothetical, future) unmapped DealType rather than crashing.
    """
    return DEAL_TYPE_LABELS.get(deal_type, deal_type.value)


def baseline_source_note(deal: Deal) -> str:
    """Which comparison value a deal is measured against, and how much to
    trust it - see "Baseline Problem" in docs/PRODUCT_SPEC.md.
    """
    if deal.baseline_source is BaselineSource.OWN_HISTORICAL_BASELINE:
        if deal.historical_baseline is not None:
            median = deal.historical_baseline.statistics.median
            return f"Vergleichswert: eigene Preishistorie (Median: {median:.2f} {deal.flight.currency})"
        return "Vergleichswert: eigene Preishistorie"
    if deal.baseline_source is BaselineSource.PROVIDER_PRICE_INSIGHT:
        source = deal.price_insight.source if deal.price_insight is not None else "Provider"
        return f"Vergleichswert: Preis-Einschätzung von {source} (keine eigene Historie)"
    if deal.baseline_source is BaselineSource.ABSOLUTE_FLOOR_TRIGGER:
        return "Vergleichswert: Festpreis-Schwelle (Error-Fare-Trigger, keine Preishistorie nötig)"
    return "Vergleichswert: nicht verfügbar"


def nights_label(nights: int) -> str:
    return f"{nights} {'Nacht' if nights == 1 else 'Nächte'}"


def trip_nights(deal: Deal) -> int:
    """Trip length in nights, from the flight's own dates - works whether
    or not an accommodation offer is attached (same convention as
    engine/deal_filters.py)."""
    return (deal.flight.return_date - deal.flight.departure_date).days


def fmt_date(value) -> str:
    return value.strftime("%d.%m.%Y")
