"""Affiliate links for the weekly "Travel Hack" tips
(dispatch/weekly_tips.py).

CORRECTION (2026-10): an earlier version of this module assumed
Travelpayouts only covers flights/hotels and built a separate set of
per-service env vars for that reason. Verified via travelpayouts.com's
own offer pages (travelpayouts.com/en/offers/{kiwi,airalo,yesim,tiqets}-
affiliate-program/) that it is actually a much broader network - 60+
travel programs under ONE account, including Airalo, Yesim, AirHelp,
Tiqets and Klook by name - so this module briefly wrapped each tip's
plain public site in Travelpayouts' own generic click-redirect
(monetization.link_builder.travelpayouts_wrap) under TRAVELPAYOUTS_MARKER.

CORRECTION #2 (2026-10, verified LIVE): that redirect itself turned out
to be broken - a real click failed with "promo_id is empty".
Travelpayouts' own docs confirm that click-redirect endpoint requires a
`promo_id` specific to a confirmed program/campaign, which this project
never had a verified value for (and will not fabricate one for - same
"never present an unverified external identifier as if confirmed" rule
this project applies everywhere else). `travelpayouts_wrap` and its use
here are removed entirely. Compensair specifically was never confirmed
by name in the original research either way, but is grouped with AirHelp
under the same tip/env var regardless.

Default per tip, in this order:
  1. The matching env var below, if set - a specific partner link you
     configured yourself (a direct partner deal, Travelpayouts or not),
     used EXACTLY as given, never wrapped, never parsed further.
  2. Otherwise, the tip's plain public site, untracked but always real
     and working - never a broken "monetized-looking" redirect that
     errors out instead of earning anything.
Never a placeholder/dead URL at any step.

A configured override URL must be https; anything else is ignored (a
typo can never turn into a broken or unsafe button).
"""

from __future__ import annotations

import os

# Importing config.py triggers its .env-loading side effect at import time -
# same pattern as upsell.py/affiliate.py.
import trip_hunter.config  # noqa: F401

ESIM_AFFILIATE_URL_ENV = "ESIM_AFFILIATE_URL"
FLIGHT_COMPENSATION_AFFILIATE_URL_ENV = "FLIGHT_COMPENSATION_AFFILIATE_URL"
SKIP_LINE_TICKETS_AFFILIATE_URL_ENV = "SKIP_LINE_TICKETS_AFFILIATE_URL"

# Plain public sites - live, real services, just untracked until a
# marker/override applies one of the paths above.
DEFAULT_ESIM_URL = "https://www.airalo.com/"
DEFAULT_FLIGHT_COMPENSATION_URL = "https://www.airhelp.com/"
DEFAULT_SKIP_LINE_TICKETS_URL = "https://www.tiqets.com/"


def _env(name: str) -> str | None:
    return (os.environ.get(name) or "").strip() or None


def _https_url(value: str | None) -> str | None:
    return value if value and value.lower().startswith("https://") and " " not in value else None


def esim_affiliate_url() -> str:
    """Link for the "lokales Datenpaket statt Roaming" tip (Yesim/Airalo) -
    ESIM_AFFILIATE_URL if configured, used exactly as given, else the
    plain, always-working Airalo site."""
    configured = _https_url(_env(ESIM_AFFILIATE_URL_ENV))
    return configured or DEFAULT_ESIM_URL


def flight_compensation_affiliate_url() -> str:
    """Link for the "Entschädigung bei Flugverspätung" tip (Compensair/
    AirHelp) - FLIGHT_COMPENSATION_AFFILIATE_URL if configured, used
    exactly as given, else the plain, always-working AirHelp site."""
    configured = _https_url(_env(FLIGHT_COMPENSATION_AFFILIATE_URL_ENV))
    return configured or DEFAULT_FLIGHT_COMPENSATION_URL


def skip_line_tickets_affiliate_url() -> str:
    """Link for the "Skip-the-Line Tickets" tip (Tiqets/Klook) -
    SKIP_LINE_TICKETS_AFFILIATE_URL if configured, used exactly as given,
    else the plain, always-working Tiqets site."""
    configured = _https_url(_env(SKIP_LINE_TICKETS_AFFILIATE_URL_ENV))
    return configured or DEFAULT_SKIP_LINE_TICKETS_URL
