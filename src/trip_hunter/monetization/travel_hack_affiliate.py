"""Affiliate links for the weekly "Travel Hack" tips
(dispatch/weekly_tips.py).

NOT Travelpayouts: that network only covers flights and hotels (Aviasales/
Hotellook - see monetization/link_builder.py's own docstring), and has no
program for eSIM data, flight-delay compensation or skip-the-line tickets.
Those are each a separate, real affiliate program (Airalo/Yesim run their
own; Compensair/AirHelp and Tiqets/Klook likewise) - this module follows
the exact same pattern monetization/upsell.py already uses for its own
optional links: a configured env var if you have a real partner link for
it, else the plain public site (untracked, but always a working link,
never a placeholder/dead URL). Fill in the env vars once you've actually
signed up for each program; nothing here is invented.

A configured URL must be https; anything else is ignored (a typo can
never turn into a broken or unsafe button).
"""

from __future__ import annotations

import os

# Importing config.py triggers its .env-loading side effect at import time -
# same pattern as upsell.py/affiliate.py.
import trip_hunter.config  # noqa: F401

ESIM_AFFILIATE_URL_ENV = "ESIM_AFFILIATE_URL"
FLIGHT_COMPENSATION_AFFILIATE_URL_ENV = "FLIGHT_COMPENSATION_AFFILIATE_URL"
SKIP_LINE_TICKETS_AFFILIATE_URL_ENV = "SKIP_LINE_TICKETS_AFFILIATE_URL"

# Plain public sites - live, real services, just untracked until a real
# partner link is configured above.
DEFAULT_ESIM_URL = "https://www.airalo.com/"
DEFAULT_FLIGHT_COMPENSATION_URL = "https://www.airhelp.com/"
DEFAULT_SKIP_LINE_TICKETS_URL = "https://www.tiqets.com/"


def _env(name: str) -> str | None:
    return (os.environ.get(name) or "").strip() or None


def _https_url(value: str | None) -> str | None:
    return value if value and value.lower().startswith("https://") and " " not in value else None


def esim_affiliate_url() -> str:
    """Link for the "lokales Datenpaket statt Roaming" tip (Yesim/Airalo)."""
    return _https_url(_env(ESIM_AFFILIATE_URL_ENV)) or DEFAULT_ESIM_URL


def flight_compensation_affiliate_url() -> str:
    """Link for the "Entschädigung bei Flugverspätung" tip (Compensair/AirHelp)."""
    return _https_url(_env(FLIGHT_COMPENSATION_AFFILIATE_URL_ENV)) or DEFAULT_FLIGHT_COMPENSATION_URL


def skip_line_tickets_affiliate_url() -> str:
    """Link for the "Skip-the-Line Tickets" tip (Tiqets/Klook)."""
    return _https_url(_env(SKIP_LINE_TICKETS_AFFILIATE_URL_ENV)) or DEFAULT_SKIP_LINE_TICKETS_URL
