"""Settings of the Free-channel upsell funnel (all optional, all env vars).

- VIP_SUBSCRIPTION_URL: where "⚡️ Jetzt Deal buchen (VIP freischalten)"
  leads (e.g. a Stripe checkout or a bot deep link). Fallback: the bot's
  start link `https://t.me/<TELEGRAM_BOT_USERNAME>?start=vip` if the bot
  name is set, else the landing page's pricing section (both plans).
- FAQ_URL: "ℹ️ Wie funktioniert Trip Hunter?" - fallback: the landing
  page's "how it works" section.
- FREE_CHANNEL_MODE: "teaser" (default: a masked teaser right away) or
  "delayed_full" (the full alert, sent to Free only after
  FREE_CHANNEL_DELAY_HOURS, default 24 - VIP always gets it immediately).
  Anything else counts as "teaser".

A configured URL must be https; anything else is ignored (a typo can never
turn into a broken or unsafe button).
"""

from __future__ import annotations

import os
import re

# Importing config.py triggers its .env loading (same pattern as affiliate.py).
import trip_hunter.config  # noqa: F401

LANDING_PAGE_URL = "https://jeremyduehrkop97-art.github.io/TripHunter/"

VIP_SUBSCRIPTION_URL_ENV = "VIP_SUBSCRIPTION_URL"
TELEGRAM_BOT_USERNAME_ENV = "TELEGRAM_BOT_USERNAME"
FAQ_URL_ENV = "FAQ_URL"
FREE_CHANNEL_MODE_ENV = "FREE_CHANNEL_MODE"
FREE_CHANNEL_DELAY_HOURS_ENV = "FREE_CHANNEL_DELAY_HOURS"

MODE_TEASER = "teaser"
MODE_DELAYED_FULL = "delayed_full"
DEFAULT_DELAY_HOURS = 24

_BOT_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")


def _env(name: str) -> str | None:
    return (os.environ.get(name) or "").strip() or None


def _https_url(value: str | None) -> str | None:
    return value if value and value.lower().startswith("https://") and " " not in value else None


def vip_subscription_url() -> str:
    """Target of the Free channel's "VIP freischalten" button."""
    configured = _https_url(_env(VIP_SUBSCRIPTION_URL_ENV))
    if configured:
        return configured
    bot = (_env(TELEGRAM_BOT_USERNAME_ENV) or "").lstrip("@")
    if _BOT_NAME_RE.fullmatch(bot):
        return f"https://t.me/{bot}?start=vip"
    return f"{LANDING_PAGE_URL}#pricing"


def faq_url() -> str:
    """Target of the "Wie funktioniert Trip Hunter?" button."""
    return _https_url(_env(FAQ_URL_ENV)) or f"{LANDING_PAGE_URL}#how"


def free_channel_mode() -> str:
    mode = (_env(FREE_CHANNEL_MODE_ENV) or "").lower()
    return MODE_DELAYED_FULL if mode == MODE_DELAYED_FULL else MODE_TEASER


def free_channel_delay_hours() -> int:
    """Delay before the Free channel gets the full alert (>= 1 hour)."""
    try:
        hours = int(_env(FREE_CHANNEL_DELAY_HOURS_ENV) or DEFAULT_DELAY_HOURS)
    except ValueError:
        return DEFAULT_DELAY_HOURS
    return max(1, hours)
