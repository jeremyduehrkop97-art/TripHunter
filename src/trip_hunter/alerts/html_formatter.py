"""Formats Deal objects into email-ready HTML with inline CSS - a
"weekly newsletter" you can hand directly to an email send API/service.

Inline styles ONLY, deliberately: many email clients (Gmail, Outlook)
strip or ignore a `<style>` block in `<head>`, so every visual rule here
lives in a `style="..."` attribute on the element it affects. Single-
column, mobile-friendly layout with a fixed max-width container - no
external stylesheet, no external images, no JavaScript (none of that
survives most email clients anyway).

Every user-visible string interpolated into markup is HTML-escaped
(html.escape) before insertion - defensive by habit, even though today's
data all comes from our own trusted providers.

Same shared presentation rules as deal_formatter.py (alerts/_shared.py:
deal-type labels, baseline wording) and the same affiliate-link decoration
(monetization/affiliate.py) - see those modules' docstrings.
"""

from __future__ import annotations

from html import escape as _esc

from trip_hunter.alerts._shared import baseline_source_note, deal_type_label, fmt_date, nights_label, trip_nights
from trip_hunter.models import Deal, DealType
from trip_hunter.monetization.affiliate import add_affiliate_tag

_FONT_STACK = "Arial, Helvetica, sans-serif"

_BADGE_STYLE: dict[DealType, str] = {
    DealType.COMBINED_TRIP_DROP: "background:#0a7d34;color:#ffffff;",
    DealType.ERROR_FARE: "background:#b3261e;color:#ffffff;",
    DealType.FLIGHT_DROP: "background:#1a73e8;color:#ffffff;",
    DealType.HOTEL_DROP: "background:#7b3fe4;color:#ffffff;",
    DealType.UNUSUALLY_LOW: "background:#e8a33d;color:#1b1206;",
}
_DEFAULT_BADGE_STYLE = "background:#666666;color:#ffffff;"

_SCORE_BADGE_STYLE = "background:#f0f0f0;color:#333333;"

_CTA_BUTTON_STYLE = (
    "display:inline-block;background:#0a7d34;color:#ffffff;padding:10px 20px;"
    "border-radius:6px;text-decoration:none;font-weight:bold;font-family:" + _FONT_STACK + ";"
    "font-size:14px;margin:4px 8px 4px 0;"
)
_CTA_DISABLED_STYLE = (
    "display:inline-block;color:#999999;padding:10px 0;font-family:" + _FONT_STACK + ";font-size:13px;"
)


def _badge(text: str, style: str) -> str:
    return (
        f'<span style="display:inline-block;{style}font-size:12px;font-weight:bold;'
        f'padding:4px 10px;border-radius:12px;margin:0 6px 6px 0;font-family:{_FONT_STACK};">'
        f"{_esc(text)}</span>"
    )


def format_deal_html(deal: Deal) -> str:
    """Format ONE deal as a self-contained HTML card (a <div>...</div>
    block, no <html>/<body> wrapper - see format_newsletter_html for the
    full document)."""
    flight = deal.flight
    nights = trip_nights(deal)

    badges = _badge(deal_type_label(deal.deal_type), _BADGE_STYLE.get(deal.deal_type, _DEFAULT_BADGE_STYLE))
    if deal.score is not None:
        badges += _badge(f"Score: {deal.score.total}/100", _SCORE_BADGE_STYLE)

    savings_html = (
        f'<p style="margin:0 0 12px 0;font-size:16px;font-weight:bold;color:#0a7d34;font-family:{_FONT_STACK};">'
        f"Ersparnis: {deal.savings_absolute:.0f} {_esc(flight.currency)} ({deal.savings_percentage:.0%})</p>"
        if deal.savings_absolute is not None and deal.savings_percentage is not None
        else f'<p style="margin:0 0 12px 0;font-size:14px;color:#999999;font-family:{_FONT_STACK};">'
        "Ersparnis nicht verfügbar</p>"
    )

    rows = [_flight_row(flight)]
    if deal.accommodation is not None:
        rows.append(_accommodation_row(deal.accommodation))
    breakdown_html = (
        f'<table role="presentation" style="width:100%;border-collapse:collapse;margin:0 0 12px 0;">'
        + "".join(rows)
        + "</table>"
    )

    cta_html = _cta_buttons(deal)

    return f"""<div style="border:1px solid #dddddd;border-radius:8px;padding:16px;margin:0 0 16px 0;font-family:{_FONT_STACK};">
  <div>{badges}</div>
  <h2 style="margin:12px 0 4px 0;font-size:20px;color:#111111;font-family:{_FONT_STACK};">{_esc(flight.origin)} &rarr; {_esc(flight.destination)}</h2>
  <p style="margin:0 0 8px 0;color:#555555;font-size:14px;font-family:{_FONT_STACK};">{fmt_date(flight.departure_date)} &ndash; {fmt_date(flight.return_date)} ({nights_label(nights)})</p>
  {savings_html}
  {breakdown_html}
  {cta_html}
  <p style="margin:12px 0 0 0;font-size:12px;color:#999999;font-family:{_FONT_STACK};">{_esc(baseline_source_note(deal))}</p>
</div>"""


def format_newsletter_html(deals: list[Deal], *, title: str = "Trip Hunter — Wochenend-Radar") -> str:
    """Format a whole list of (already filtered) deals into one complete,
    self-contained HTML document ready to hand to an email send API.
    """
    escaped_title = _esc(title)
    if not deals:
        body = (
            f'<p style="font-family:{_FONT_STACK};font-size:15px;color:#555555;">'
            "Keine passenden Deals in diesem Lauf gefunden.</p>"
        )
    else:
        count_label = f"{len(deals)} passende{'r' if len(deals) == 1 else ''} Deal" + (
            "" if len(deals) == 1 else "s"
        )
        intro = (
            f'<p style="font-family:{_FONT_STACK};font-size:15px;color:#555555;margin:0 0 20px 0;">'
            f"{count_label} gefunden:</p>"
        )
        body = intro + "".join(format_deal_html(deal) for deal in deals)

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escaped_title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f4;">
<div style="max-width:600px;margin:0 auto;padding:24px 16px;font-family:{_FONT_STACK};">
<h1 style="font-size:24px;color:#111111;margin:0 0 20px 0;font-family:{_FONT_STACK};">{escaped_title}</h1>
{body}
</div>
</body>
</html>
"""


def _flight_row(flight) -> str:
    stops_label = "Nonstop" if flight.stops == 0 else f"{flight.stops} Stopp(s)"
    text = f"Flug: {flight.price:.2f} {flight.currency} &middot; {_esc(flight.airline)} &middot; {stops_label}"
    return f'<tr><td style="padding:8px;background:#f9f9f9;font-size:14px;font-family:{_FONT_STACK};">{text}</td></tr>'


def _accommodation_row(accommodation) -> str:
    rating_part = f" ({accommodation.rating:.1f} &#9733;)" if accommodation.rating is not None else ""
    text = (
        f"Hotel: {_esc(accommodation.name)}{rating_part} &middot; "
        f"{accommodation.total_price:.2f} {accommodation.currency}"
    )
    return f'<tr><td style="padding:8px;background:#f9f9f9;font-size:14px;font-family:{_FONT_STACK};">{text}</td></tr>'


def _cta_buttons(deal: Deal) -> str:
    buttons = []
    flight_link = add_affiliate_tag(deal.flight.booking_link)
    if flight_link:
        buttons.append(f'<a href="{_esc(flight_link)}" style="{_CTA_BUTTON_STYLE}">Flug buchen</a>')
    else:
        buttons.append(f'<span style="{_CTA_DISABLED_STYLE}">Flug: kein Direktlink verfügbar</span>')

    if deal.accommodation is not None:
        hotel_link = add_affiliate_tag(deal.accommodation.booking_link)
        if hotel_link:
            buttons.append(f'<a href="{_esc(hotel_link)}" style="{_CTA_BUTTON_STYLE}">Hotel buchen</a>')
        else:
            buttons.append(f'<span style="{_CTA_DISABLED_STYLE}">Hotel: kein Direktlink verfügbar</span>')

    return f'<p style="margin:0 0 8px 0;">{"".join(buttons)}</p>'
