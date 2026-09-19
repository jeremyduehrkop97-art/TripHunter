"""Sends a Deal, formatted via alerts/instant_alert_formatter.py, to
Telegram as a push notification.

Configuration: TRIP_HUNTER_TELEGRAM_BOT_TOKEN / TRIP_HUNTER_TELEGRAM_CHAT_ID
(see .env.example). Both are new since the Trip Hunter rename - no legacy
VACATION_HUNTER_* fallback needed (see config.py's _env() for that pattern,
used only where a pre-rename value could actually exist).

Defensive by design, matching this project's "never crash the pipeline"
convention (see providers/errors.py): missing credentials, a network
timeout, a non-2xx HTTP status, or a Telegram-level API error all result
in a printed, transparent fallback and a `False` return - never a raised
exception. A single failed dispatch must never take down a larger run
that sends several alerts.

SECRET SAFETY: the bot token is embedded in the request URL itself (that's
how the Telegram Bot API works - not a header, not a body field). This
module NEVER prints that URL, and never prints a raw caught exception's
str() either, since requests/urllib3 exception messages commonly embed
the request URL - printing str(exc) here would leak the token into logs
exactly the way providers/errors.py's docstring warns against for API
keys. Only the exception's type name is logged.
"""

from __future__ import annotations

import os

import requests

# Importing config.py (even though we only use os.environ directly below)
# triggers its .env-loading side effect at import time - see
# monetization/affiliate.py for the identical pattern and rationale.
import trip_hunter.config  # noqa: F401
from trip_hunter.alerts.instant_alert_formatter import format_instant_alert
from trip_hunter.models import Deal

_BOT_TOKEN_ENV_VAR = "TRIP_HUNTER_TELEGRAM_BOT_TOKEN"
_CHAT_ID_ENV_VAR = "TRIP_HUNTER_TELEGRAM_CHAT_ID"
_API_BASE_URL = "https://api.telegram.org"
_DEFAULT_TIMEOUT_SECONDS = 10.0


def get_bot_token() -> str | None:
    """The configured Telegram bot token, or None if unset/empty. Never
    print this value."""
    return os.environ.get(_BOT_TOKEN_ENV_VAR) or None


def get_chat_id() -> str | None:
    return os.environ.get(_CHAT_ID_ENV_VAR) or None


def send_telegram_alert(
    deal: Deal,
    bot_token: str | None = None,
    chat_id: str | None = None,
    *,
    session: requests.Session | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Format `deal` via instant_alert_formatter.format_instant_alert(...)
    and send it as a Telegram message.

    `bot_token`/`chat_id` default to the environment (get_bot_token() /
    get_chat_id()) when not passed explicitly. If either is still missing,
    the formatted message is printed as a transparent fallback (so the
    alert is at least visible somewhere) and this returns False without
    attempting any network call.

    Returns True only on a confirmed successful send (HTTP 200 with
    Telegram's own {"ok": true} in the response body). Every other
    outcome - missing credentials, a request exception, a non-200 status,
    an unparseable response, or a Telegram-level {"ok": false} - prints a
    short, secret-free reason and returns False. Never raises.
    """
    resolved_token = bot_token if bot_token is not None else get_bot_token()
    resolved_chat_id = chat_id if chat_id is not None else get_chat_id()
    message = format_instant_alert(deal)

    if not resolved_token or not resolved_chat_id:
        print(
            "Telegram nicht konfiguriert (TRIP_HUNTER_TELEGRAM_BOT_TOKEN / "
            "TRIP_HUNTER_TELEGRAM_CHAT_ID fehlt) - Fallback-Ausgabe:"
        )
        print(message)
        return False

    url = f"{_API_BASE_URL}/bot{resolved_token}/sendMessage"
    payload = {"chat_id": resolved_chat_id, "text": message}
    http = session or requests.Session()

    try:
        response = http.post(url, data=payload, timeout=timeout_seconds)
    except requests.exceptions.Timeout:
        print("Telegram-Versand fehlgeschlagen (Timeout).")
        return False
    except requests.exceptions.RequestException as exc:
        # Never print str(exc) - requests/urllib3 exception messages
        # commonly embed the request URL, which contains the bot token.
        print(f"Telegram-Versand fehlgeschlagen (Netzwerkfehler: {type(exc).__name__}).")
        return False

    if response.status_code != 200:
        print(f"Telegram-Versand fehlgeschlagen (HTTP {response.status_code}): {response.text[:300]}")
        return False

    try:
        body = response.json()
    except ValueError:
        print("Telegram-Versand fehlgeschlagen (Antwort war kein gültiges JSON).")
        return False

    if not body.get("ok"):
        print(f"Telegram-Versand fehlgeschlagen (API meldet Fehler): {body}")
        return False

    print("Telegram-Alert gesendet.")
    return True
