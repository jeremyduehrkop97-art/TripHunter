"""Affiliate links for the weekly "Travel Hack" tips
(dispatch/weekly_tips.py).

CORRECTION (2026-10): an earlier version of this module assumed
Travelpayouts only covers flights/hotels and built a separate set of
per-service env vars for that reason. Verified via travelpayouts.com's
own offer pages (travelpayouts.com/en/offers/{kiwi,airalo,yesim,tiqets}-
affiliate-program/) that it is actually a much broader network - 60+
travel programs under ONE account, including Airalo, Yesim, AirHelp,
Tiqets and Klook by name - so eSIM/compensation/tickets commission now
flows under the SAME TRAVELPAYOUTS_MARKER as every other link this
project builds, via Travelpayouts' own click-redirect
(monetization.link_builder.travelpayouts_wrap). Compensair specifically
was not confirmed by name in that research, but is grouped with AirHelp
under the same tip/env var, so it inherits the same default - Compensair
itself is not Travelpayouts-exclusive, and nothing here claims it is.

Default per tip, in this order:
  1. The matching env var below, if set - a specific partner link you
     configured yourself (e.g. a direct, non-Travelpayouts deal), used
     exactly as given, never re-wrapped.
  2. Otherwise, the tip's plain public site wrapped in the Travelpayouts
     click-redirect if TRAVELPAYOUTS_MARKER is set.
  3. Otherwise, the plain public site, untracked.
Never a placeholder/dead URL at any step.

A configured override URL must be https; anything else is ignored (a
typo can never turn into a broken or unsafe button).
"""

from __future__ import annotations

import os

# Importing config.py triggers its .env-loading side effect at import time -
# same pattern as upsell.py/affiliate.py.
import trip_hunter.config  # noqa: F401
from trip_hunter.monetization.link_builder import travelpayouts_wrap

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
    """Link for the "lokales Datenpaket statt Roaming" tip (Yesim/Airalo)."""
    configured = _https_url(_env(ESIM_AFFILIATE_URL_ENV))
    return configured or travelpayouts_wrap(DEFAULT_ESIM_URL)


def flight_compensation_affiliate_url() -> str:
    """Link for the "Entschädigung bei Flugverspätung" tip (Compensair/AirHelp)."""
    configured = _https_url(_env(FLIGHT_COMPENSATION_AFFILIATE_URL_ENV))
    return configured or travelpayouts_wrap(DEFAULT_FLIGHT_COMPENSATION_URL)


def skip_line_tickets_affiliate_url() -> str:
    """Link for the "Skip-the-Line Tickets" tip (Tiqets/Klook)."""
    configured = _https_url(_env(SKIP_LINE_TICKETS_AFFILIATE_URL_ENV))
    return configured or travelpayouts_wrap(DEFAULT_SKIP_LINE_TICKETS_URL)
