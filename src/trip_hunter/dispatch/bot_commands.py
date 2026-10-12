"""Interactive Telegram bot commands: /start, /airports, /settings open an
inline-keyboard menu where a user toggles which DACH departure airports
they want personal 1:1 deal DMs for (user_preference_repository.py;
dispatch/telegram.py's own DM fan-out step reads that same table).

ARCHITECTURE - READ THIS FIRST: every other part of Trip Hunter is a
short-lived process triggered by a GitHub Actions cron schedule (scan,
send, exit) - there is no persistently running server anywhere in this
project (see docs/ARCHITECTURE.md's "Why Python" stance against
unnecessary infrastructure). Responding to a button tap "sofort"
(instantly) would need either a persistent process or a webhook behind a
real HTTPS endpoint - neither exists in this project's actual hosting
(GitHub Pages for the static site, GitHub Actions for everything else).
This module instead POLLS Telegram's own `getUpdates` long-poll endpoint
once per run (run_once/main, `.github/workflows/bot_commands.yml`, every
5 minutes - GitHub Actions' shortest supported cron interval) and exits.
A button tap is therefore answered within, worst case, ONE SCHEDULE
INTERVAL (~5 minutes), never truly instantly - this is a deliberate,
clearly-documented adaptation to this project's real infrastructure, not
an oversight. A genuinely instant response would need a different hosting
model entirely (a webhook behind an always-on endpoint) - out of scope
here; flagged, not silently built as if it were sub-second.

PURE LOGIC vs I/O: `handle_update` (and everything it calls) takes
`send_fn`/`edit_keyboard_fn`/`answer_callback_fn` as injected callables,
exactly like every other dispatch module in this project (feed_radar.py's
`dispatch_fn`, etc.) - so the whole menu/toggle/button-label flow is
tested without a single real HTTP request. `run_once`/`main` are the only
functions that wire up the real Telegram HTTP calls.

OFFSET PERSISTENCE: Telegram's `getUpdates` is a consuming read - calling
it with `offset=N` acknowledges every update up to N-1 as handled, so
Telegram never redelivers them. The last processed update_id is
persisted in the same shared SQLite file as everything else in this
project (own small table, `bot_update_state`) so it survives between
separate GitHub Actions runs via the same cache this project already
restores data/trip_hunter.db from.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Callable

import requests

import trip_hunter.config  # noqa: F401 - .env-loading side effect, same pattern as dispatch/telegram.py
from trip_hunter.alerts.airport_names import city_name
from trip_hunter.price_history_repository import DEFAULT_DB_PATH
from trip_hunter.user_preference_repository import SETTINGS_MENU_AIRPORTS, UserPreferenceRepository

_START_COMMANDS = ("/start", "/airports", "/settings")

_SELECT_ALL_CALLBACK = "airport:select_all"
_DESELECT_ALL_CALLBACK = "airport:deselect_all"
_DONE_CALLBACK = "airport:done"
_TOGGLE_PREFIX = "airport:"  # + IATA code, e.g. "airport:FRA"

_SELECT_ALL_TEXT = "🌟 Alle auswählen"
_DESELECT_ALL_TEXT = "❌ Alle abwählen"
_DONE_TEXT = "💾 Fertig & Speichern"

_WELCOME_TEXT = (
    "✈️ <b>Deine persönlichen Deal-Alarme</b>\n\n"
    "Wähle unten deine DACH-Abflughäfen - du bekommst Deals ab diesen "
    "Flughäfen künftig direkt als Privatnachricht, zusätzlich zum VIP-/"
    "Free-Kanal. Ein Tap schaltet einen Flughafen um; Änderungen werden "
    "sofort gespeichert, \"💾 Fertig & Speichern\" schließt nur das Menü."
)
_DONE_TEXT_REPLY = "✅ Gespeichert! Du bekommst Deals jetzt nur noch für deine ausgewählten Abflughäfen per DM."
_DESELECT_ALL_REPLY = "⏸️ Persönliche Deal-DMs pausiert. Tippe auf einen Flughafen oder \"🌟 Alle auswählen\", um sie wieder zu aktivieren."
_SELECT_ALL_REPLY = "🌟 Alle DACH-Flughäfen aktiv - du bekommst Deals ab jedem von ihnen per DM."


# --- pure keyboard-building logic (no I/O) ---------------------------------------


def _airport_label(code: str, *, selected: bool) -> str:
    mark = "✅" if selected else "⬜"
    return f"{mark} {city_name(code)} ({code})"


def _is_selected(code: str, origins: list[str] | None) -> bool:
    """`origins` is get_user_origins' own return shape: None means every
    airport (including this one) is selected."""
    return origins is None or code in origins


def build_airport_settings_keyboard(chat_id: int, repo: UserPreferenceRepository) -> dict:
    """The settings menu's inline keyboard: one toggle button per
    SETTINGS_MENU_AIRPORTS entry (2 per row, in the task's own requested
    pairing), then the select-all/deselect-all row, then the "Fertig &
    Speichern" row on its own."""
    origins = repo.get_user_origins(chat_id)
    toggle_rows = [
        [
            {"text": _airport_label(code, selected=_is_selected(code, origins)), "callback_data": f"{_TOGGLE_PREFIX}{code}"}
            for code in pair
        ]
        for pair in zip(SETTINGS_MENU_AIRPORTS[0::2], SETTINGS_MENU_AIRPORTS[1::2])
    ]
    return {
        "inline_keyboard": [
            *toggle_rows,
            [{"text": _SELECT_ALL_TEXT, "callback_data": _SELECT_ALL_CALLBACK},
             {"text": _DESELECT_ALL_TEXT, "callback_data": _DESELECT_ALL_CALLBACK}],
            [{"text": _DONE_TEXT, "callback_data": _DONE_CALLBACK}],
        ]
    }


def handle_update(
    update: dict,
    repo: UserPreferenceRepository,
    *,
    send_fn: Callable[[int, str, dict | None], bool],
    edit_keyboard_fn: Callable[[int, int, dict], bool],
    answer_callback_fn: Callable[[str, str], bool],
) -> None:
    """Process exactly ONE Telegram Update object - a /start-like command
    (send the welcome text + settings keyboard) or a callback_query (a
    button tap: apply the toggle/select-all/deselect-all/done action,
    then edit the SAME message's keyboard in place so the user sees the
    new state, and always answerCallbackQuery so Telegram stops showing
    the tapped button's loading spinner). Unknown/irrelevant updates
    (e.g. a plain chat message that isn't one of the start commands) are
    silently ignored - this bot only ever reacts to what it explicitly
    understands, never guesses at freeform text."""
    message = update.get("message")
    if message is not None:
        text = (message.get("text") or "").strip()
        chat_id = message.get("chat", {}).get("id")
        if chat_id is not None and text in _START_COMMANDS:
            send_fn(chat_id, _WELCOME_TEXT, build_airport_settings_keyboard(chat_id, repo))
        return

    callback = update.get("callback_query")
    if callback is None:
        return
    data = callback.get("data") or ""
    callback_id = callback.get("id") or ""
    source_message = callback.get("message") or {}
    chat_id = source_message.get("chat", {}).get("id")
    message_id = source_message.get("message_id")
    if chat_id is None or message_id is None:
        answer_callback_fn(callback_id, "")
        return

    reply = ""
    if data == _SELECT_ALL_CALLBACK:
        repo.select_all(chat_id)
        reply = _SELECT_ALL_REPLY
    elif data == _DESELECT_ALL_CALLBACK:
        repo.deselect_all(chat_id)
        reply = _DESELECT_ALL_REPLY
    elif data == _DONE_CALLBACK:
        reply = _DONE_TEXT_REPLY
    elif data.startswith(_TOGGLE_PREFIX):
        code = data[len(_TOGGLE_PREFIX):]
        if code in SETTINGS_MENU_AIRPORTS:
            repo.toggle_origin(chat_id, code)
    else:
        answer_callback_fn(callback_id, "")
        return

    answer_callback_fn(callback_id, reply)
    if data != _DONE_CALLBACK:  # "Fertig" only closes/acknowledges - the keyboard itself never needs a refresh
        edit_keyboard_fn(chat_id, message_id, build_airport_settings_keyboard(chat_id, repo))


# --- real Telegram I/O (only these call the network) -----------------------------

_API_BASE_URL = "https://api.telegram.org"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_LONG_POLL_TIMEOUT_SECONDS = 25.0  # one run's whole getUpdates wait - see module docstring on polling cadence

_CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS bot_update_state (
    id INTEGER PRIMARY KEY CHECK (id = 0),
    last_update_id INTEGER NOT NULL
)
"""


def _get_last_update_id(db_path: Path | str) -> int | None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(_CREATE_STATE_SQL)
        row = connection.execute("SELECT last_update_id FROM bot_update_state WHERE id = 0").fetchone()
    return row[0] if row else None


def _set_last_update_id(db_path: Path | str, update_id: int) -> None:
    with sqlite3.connect(Path(db_path)) as connection:
        connection.execute(_CREATE_STATE_SQL)
        connection.execute(
            "INSERT INTO bot_update_state (id, last_update_id) VALUES (0, ?) "
            "ON CONFLICT (id) DO UPDATE SET last_update_id = excluded.last_update_id",
            (update_id,),
        )


def _call_api(bot_token: str, method: str, payload: dict, *, session: requests.Session, timeout_seconds: float) -> dict | None:
    """POST/GET one Bot API call, returning Telegram's own `result`, or
    None on any failure - never raises, never prints the token (same
    secret-safety guarantee as dispatch/telegram.py)."""
    url = f"{_API_BASE_URL}/bot{bot_token}/{method}"
    try:
        response = session.post(url, data=payload, timeout=timeout_seconds)
    except requests.exceptions.Timeout:
        print(f"Bot-Kommandos: {method} fehlgeschlagen (Timeout).")
        return None
    except requests.exceptions.RequestException as exc:
        print(f"Bot-Kommandos: {method} fehlgeschlagen (Netzwerkfehler: {type(exc).__name__}).")
        return None
    if response.status_code != 200:
        print(f"Bot-Kommandos: {method} fehlgeschlagen (HTTP {response.status_code}).")
        return None
    try:
        body = response.json()
    except ValueError:
        print(f"Bot-Kommandos: {method} - Antwort war kein gültiges JSON.")
        return None
    if not body.get("ok"):
        print(f"Bot-Kommandos: {method} - API meldet Fehler: {body.get('description', body)}")
        return None
    return body.get("result")


def run_once(
    bot_token: str,
    *,
    repo: UserPreferenceRepository | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
    session: requests.Session | None = None,
    timeout_seconds: float = _LONG_POLL_TIMEOUT_SECONDS,
) -> int:
    """One real polling cycle: fetch whatever updates arrived since the
    last run (offset persisted in `db_path`), process each via
    handle_update with the real Telegram HTTP calls wired in, persist the
    new offset, return how many updates were processed. Never raises - a
    single bad update is logged and skipped, the rest of the batch still
    runs."""
    resolved_repo = repo if repo is not None else UserPreferenceRepository(db_path)
    resolved_session = session or requests.Session()
    last_update_id = _get_last_update_id(db_path)
    params: dict = {"timeout": int(timeout_seconds)}
    if last_update_id is not None:
        params["offset"] = last_update_id + 1

    updates = _call_api(
        bot_token, "getUpdates", params, session=resolved_session,
        timeout_seconds=timeout_seconds + _DEFAULT_TIMEOUT_SECONDS,
    )
    if not updates:
        print("Bot-Kommandos: keine neuen Updates.")
        return 0

    def send_fn(chat_id: int, text: str, keyboard: dict | None) -> bool:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if keyboard is not None:
            import json as _json
            payload["reply_markup"] = _json.dumps(keyboard, ensure_ascii=False)
        return _call_api(bot_token, "sendMessage", payload, session=resolved_session, timeout_seconds=_DEFAULT_TIMEOUT_SECONDS) is not None

    def edit_keyboard_fn(chat_id: int, message_id: int, keyboard: dict) -> bool:
        import json as _json
        payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": _json.dumps(keyboard, ensure_ascii=False)}
        return _call_api(bot_token, "editMessageReplyMarkup", payload, session=resolved_session, timeout_seconds=_DEFAULT_TIMEOUT_SECONDS) is not None

    def answer_callback_fn(callback_query_id: str, text: str) -> bool:
        payload = {"callback_query_id": callback_query_id, "text": text}
        return _call_api(bot_token, "answerCallbackQuery", payload, session=resolved_session, timeout_seconds=_DEFAULT_TIMEOUT_SECONDS) is not None

    processed = 0
    highest_update_id = last_update_id
    for update in updates:
        update_id = update.get("update_id")
        try:
            handle_update(update, resolved_repo, send_fn=send_fn, edit_keyboard_fn=edit_keyboard_fn, answer_callback_fn=answer_callback_fn)
            processed += 1
        except Exception as exc:  # noqa: BLE001 - one bad update must never lose the rest of the batch or the offset
            print(f"Bot-Kommandos: Update übersprungen ({type(exc).__name__}).")
        if update_id is not None and (highest_update_id is None or update_id > highest_update_id):
            highest_update_id = update_id

    if highest_update_id is not None and highest_update_id != last_update_id:
        _set_last_update_id(db_path, highest_update_id)
    print(f"Bot-Kommandos: {processed} Update(s) verarbeitet.")
    return processed


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="python -m trip_hunter.dispatch.bot_commands",
        description="One polling cycle for /start, /airports, /settings and the airport-toggle menu.",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (.github/workflows/bot_commands.yml, every 5 minutes)."""
    from trip_hunter.dispatch.telegram import get_bot_token

    _build_parser().parse_args(argv)
    print("TRIP HUNTER — BOT COMMANDS")
    bot_token = get_bot_token()
    if not bot_token:
        print("Bot-Kommandos: Telegram nicht konfiguriert.")
        return 0
    return run_once(bot_token)


if __name__ == "__main__":
    main()
