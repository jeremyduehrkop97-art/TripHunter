"""Sends a Deal, formatted via alerts/instant_alert_formatter.py, to
Telegram as a push notification.

Configuration: TRIP_HUNTER_TELEGRAM_BOT_TOKEN / TRIP_HUNTER_TELEGRAM_CHAT_ID
(see .env.example). Both are new since the Trip Hunter rename - no legacy
VACATION_HUNTER_* fallback needed (see config.py's _env() for that pattern,
used only where a pre-rename value could actually exist).

DUAL-CHANNEL ROUTING (Free/VIP): TELEGRAM_FREE_CHAT_ID / TELEGRAM_VIP_CHAT_ID
configure two separate destination channels for `dispatch_deal_alert` (the
production entry point daily_sampler.py uses). Deliberately plain
TELEGRAM_* names, not TRIP_HUNTER_-prefixed like the other env vars here -
matches how the channel IDs were handed over. VIP gets the full-detail
alert (format_instant_alert - real, affiliate-tagged booking links) for
every alert tier; Free gets the teaser (format_teaser_alert - same price
highlights, no links, plus a VIP-upgrade hint) for Tier 1/2 only - Tier 3
("Good Deal") is VIP-exclusive, see engine/alert_tier.py. If neither is configured, `dispatch_deal_alert`
falls back to the single legacy TRIP_HUNTER_TELEGRAM_CHAT_ID channel via
`send_telegram_alert` - existing single-channel setups keep working
unchanged. `send_telegram_alert` itself is untouched (still the low-level
"format + send one message to one chat" primitive used directly by
dispatch/test_dispatch.py) - dual-channel routing is a layer on top of it,
not a replacement.

Defensive by design, matching this project's "never crash the pipeline"
convention (see providers/errors.py): missing credentials, a network
timeout, a non-2xx HTTP status, or a Telegram-level API error all result
in a printed, transparent fallback and a `False` return - never a raised
exception. A single failed dispatch must never take down a larger run
that sends several alerts - and, for `dispatch_deal_alert`, a failure on
one channel (e.g. VIP) never prevents the other (Free) from still being
attempted.

PHOTOS: `dispatch_deal_alert` sends each channel's text as the caption of a
destination photo (sendPhoto, alerts/destination_images.py) - VIP clear,
Free blurred via Telegram's `has_spoiler`. If the photo can't be sent (a
Telegram-side fetch error, a network failure, or a caption over
Telegram's 1024-character limit) it falls back to the plain-text
sendMessage, so an alert is never lost because of its picture. The
legacy single-channel path (`send_telegram_alert`) stays text-only.

SECRET SAFETY: the bot token is embedded in the request URL itself (that's
how the Telegram Bot API works - not a header, not a body field). This
module NEVER prints that URL, and never prints a raw caught exception's
str() either, since requests/urllib3 exception messages commonly embed
the request URL - printing str(exc) here would leak the token into logs
exactly the way providers/errors.py's docstring warns against for API
keys. Only the exception's type name is logged. Channel IDs (free/vip/
default chat_id) are not secrets - Telegram channel/chat IDs carry no
credential value on their own - so they're printed freely where useful
(unlike the token).
"""

from __future__ import annotations

import os

import requests

# Importing config.py (even though we only use os.environ directly below)
# triggers its .env-loading side effect at import time - see
# monetization/affiliate.py for the identical pattern and rationale.
import trip_hunter.config  # noqa: F401
from trip_hunter.alerts.destination_images import destination_image_url
from trip_hunter.alerts.instant_alert_formatter import format_instant_alert, format_teaser_alert
from trip_hunter.engine.alert_tier import classify_alert_tier, is_free_channel_eligible
from trip_hunter.models import Deal

_BOT_TOKEN_ENV_VAR = "TRIP_HUNTER_TELEGRAM_BOT_TOKEN"
_CHAT_ID_ENV_VAR = "TRIP_HUNTER_TELEGRAM_CHAT_ID"
_FREE_CHAT_ID_ENV_VAR = "TELEGRAM_FREE_CHAT_ID"
_VIP_CHAT_ID_ENV_VAR = "TELEGRAM_VIP_CHAT_ID"
_API_BASE_URL = "https://api.telegram.org"
_DEFAULT_TIMEOUT_SECONDS = 10.0
# Telegram's hard limit for a photo caption; longer text can't go via sendPhoto.
_MAX_CAPTION_LENGTH = 1024


def get_bot_token() -> str | None:
    """The configured Telegram bot token, or None if unset/empty. Never
    print this value."""
    return os.environ.get(_BOT_TOKEN_ENV_VAR) or None


def get_chat_id() -> str | None:
    return os.environ.get(_CHAT_ID_ENV_VAR) or None


def get_free_chat_id() -> str | None:
    return os.environ.get(_FREE_CHAT_ID_ENV_VAR) or None


def get_vip_chat_id() -> str | None:
    return os.environ.get(_VIP_CHAT_ID_ENV_VAR) or None


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

    return _post_message(resolved_token, resolved_chat_id, message, session=session, timeout_seconds=timeout_seconds)


def _post_message(
    bot_token: str,
    chat_id: str,
    message: str,
    *,
    session: requests.Session | None,
    timeout_seconds: float,
) -> bool:
    """Low-level send of an already-formatted `message` to one chat.
    Assumes `bot_token`/`chat_id` are both already known (callers own the
    "missing credentials" decision - see `send_telegram_alert` and
    `dispatch_deal_alert`). Same error handling/secret-safety guarantees
    as documented on the module: never raises, never prints the token.
    """
    return _call_api(
        bot_token, "sendMessage", {"chat_id": chat_id, "text": message},
        session=session, timeout_seconds=timeout_seconds,
    )


def _post_photo_alert(
    bot_token: str,
    chat_id: str,
    message: str,
    photo_url: str,
    *,
    spoiler: bool,
    session: requests.Session | None,
    timeout_seconds: float,
) -> bool:
    """Send `message` as the caption of the photo at `photo_url`
    (sendPhoto); `spoiler` blurs the photo until tapped. On ANY photo
    failure (or a caption over Telegram's limit) falls back to the plain
    text `_post_message`, so the alert still arrives."""
    if len(message) <= _MAX_CAPTION_LENGTH:
        payload = {"chat_id": chat_id, "photo": photo_url, "caption": message}
        if spoiler:
            payload["has_spoiler"] = "true"
        if _call_api(bot_token, "sendPhoto", payload, session=session, timeout_seconds=timeout_seconds):
            return True
        print("Bild-Versand fehlgeschlagen - Fallback auf reinen Text.")
    else:
        print("Caption zu lang für sendPhoto - Fallback auf reinen Text.")
    return _post_message(bot_token, chat_id, message, session=session, timeout_seconds=timeout_seconds)


def _call_api(
    bot_token: str,
    method: str,
    payload: dict[str, str],
    *,
    session: requests.Session | None,
    timeout_seconds: float,
) -> bool:
    url = f"{_API_BASE_URL}/bot{bot_token}/{method}"
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


def dispatch_deal_alert(
    deal: Deal,
    bot_token: str | None = None,
    *,
    free_chat_id: str | None = None,
    vip_chat_id: str | None = None,
    default_chat_id: str | None = None,
    session: requests.Session | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Dual-channel production entry point: routes one Deal to the Free
    and/or VIP Telegram channels, per the module docstring's "DUAL-CHANNEL
    ROUTING" section.

    `free_chat_id`/`vip_chat_id`/`default_chat_id` default to the
    environment (get_free_chat_id() / get_vip_chat_id() / get_chat_id())
    when not passed explicitly - mirrors send_telegram_alert's convention.

    - VIP channel (if configured): the full-detail alert
      (format_instant_alert - real, affiliate-tagged booking links) for
      EVERY alert tier (see engine/alert_tier.py).
    - Free channel (if configured): the teaser (format_teaser_alert - same
      price highlights, no booking links, plus a VIP-upgrade hint) - but
      only for Tier 1 (Error Fare) and Tier 2 (Combined Drop) deals.
      Tier 3 ("Good Deal" - UNUSUALLY_LOW/HOTEL_DROP) is VIP-exclusive:
      solid but non-urgent savings keep VIP subscribers engaged with
      steady content without spamming the Free channel on every minor
      deal (see engine/alert_tier.py's is_free_channel_eligible()).
    - Neither configured: falls back to the single legacy `default_chat_id`
      channel via send_telegram_alert (full-detail alert, no tier
      filtering - there's no Free/VIP distinction to enforce on a single
      shared channel) - preserves pre-dual-channel single-chat setups
      unchanged.

    Each configured channel is attempted independently - a failed VIP send
    never prevents the Free send from being attempted, and vice versa.
    Returns True iff at least one channel send succeeded (a Tier-3 deal
    with only a Free channel configured - no VIP - sends nothing and
    returns False; that's expected, not a bug).
    """
    resolved_token = bot_token if bot_token is not None else get_bot_token()
    resolved_free = free_chat_id if free_chat_id is not None else get_free_chat_id()
    resolved_vip = vip_chat_id if vip_chat_id is not None else get_vip_chat_id()

    if not resolved_free and not resolved_vip:
        resolved_default = default_chat_id if default_chat_id is not None else get_chat_id()
        return send_telegram_alert(
            deal, bot_token=resolved_token, chat_id=resolved_default,
            session=session, timeout_seconds=timeout_seconds,
        )

    if not resolved_token:
        print(
            "Telegram nicht konfiguriert (TRIP_HUNTER_TELEGRAM_BOT_TOKEN fehlt) - "
            "Fallback-Ausgabe:"
        )
        print(format_instant_alert(deal))
        return False

    tier = classify_alert_tier(deal)
    photo_url = destination_image_url(deal.flight.destination)
    dispatched = False

    if resolved_vip:
        print(f"VIP-Kanal ({resolved_vip}): volle Detailtiefe inkl. Direktlinks. [{tier}]")
        if _post_photo_alert(
            resolved_token, resolved_vip, format_instant_alert(deal), photo_url,
            spoiler=False, session=session, timeout_seconds=timeout_seconds,
        ):
            dispatched = True

    if resolved_free:
        if is_free_channel_eligible(tier):
            print(f"Free-Kanal ({resolved_free}): Teaser ohne Direktlinks. [{tier}]")
            if _post_photo_alert(
                resolved_token, resolved_free, format_teaser_alert(deal), photo_url,
                spoiler=True, session=session, timeout_seconds=timeout_seconds,
            ):
                dispatched = True
        else:
            print(f"Free-Kanal: übersprungen (Tier {tier} ist VIP-exklusiv).")

    return dispatched
