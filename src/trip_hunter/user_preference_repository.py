"""Which DACH departure airports a Telegram user wants personal 1:1 deal
DMs for (dispatch/bot_commands.py's settings menu; dispatch/telegram.py's
DM fan-out step).

SCHEMA: one row per chat_id that has ever interacted with /start or
/airports - a chat_id with NO ROW AT ALL has never opted in and is never
a DM recipient, full stop (get_users_for_origin never guesses "probably
wants everything" for a stranger who never started the bot).

"ALL AIRPORTS" (origins_csv empty or "*"): once a row DOES exist, an
empty/"*" origins_csv means "every DACH airport" - the task's own stated
rule, and also this repository's OWN default for a brand-new row (a user
who just pressed /start without touching any toggle yet should not miss
deals by accident).

THE "ALLE ABWÄHLEN" EDGE CASE (resolved, not left ambiguous): the task's
own two rules, read together, contradict each other - "empty origins_csv
means all airports active" AND "a 'deselect all' button must exist" can't
both be true if "deselect all" were implemented by storing an empty
origins_csv (that would ironically re-subscribe the user to everything).
Resolved here via the EXISTING, separate `is_active` column instead:
"Alle abwählen" (deselect_all) pauses the subscription (is_active=0)
rather than writing an empty/"*" csv - and toggle_origin applies the same
rule if a toggle would ever leave the explicit set empty (e.g. the user's
very last remaining airport). origins_csv itself therefore only ever
means "all" when it is empty/"*" AND the row is active.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from trip_hunter.price_history_repository import DEFAULT_DB_PATH

# The exact 10 DACH hubs the settings menu (dispatch/bot_commands.py) lists
# and toggles - "Alle auswählen" means exactly this set, never the wider
# engine/feed_sensor.DACH_ORIGINS allowlist (which also has SZG/INN/GVA,
# never shown as toggles at all, so a user could never deliberately pick
# or exclude them through this menu in the first place).
SETTINGS_MENU_AIRPORTS: tuple[str, ...] = ("FRA", "MUC", "BER", "HAM", "DUS", "CGN", "STR", "VIE", "ZRH", "BSL")

_ALL_SENTINEL = "*"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS user_preferences (
    chat_id INTEGER PRIMARY KEY,
    origins_csv TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
)
"""


def _parse_origins_csv(origins_csv: str) -> list[str] | None:
    """None means "all" (empty or "*" - the task's own stated rule)."""
    cleaned = (origins_csv or "").strip()
    if not cleaned or cleaned == _ALL_SENTINEL:
        return None
    return [code for code in cleaned.split(",") if code]


def _render_origins_csv(origins: list[str] | None) -> str:
    """The inverse of _parse_origins_csv - None/[]/["*"] all collapse to
    the one "*" sentinel (never guessed apart - see module docstring),
    an explicit list is stored sorted and deduplicated for a stable,
    reviewable column value."""
    if not origins or origins == [_ALL_SENTINEL]:
        return _ALL_SENTINEL
    return ",".join(sorted(set(code.strip().upper() for code in origins if code.strip())))


class UserPreferenceRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_CREATE_SQL)

    # --- reads ---------------------------------------------------------------

    def has_preferences(self, chat_id: int) -> bool:
        """True iff `chat_id` has ever interacted with /start or
        /airports at all - see module docstring's "SCHEMA" section for
        why this matters (a stranger with no row is never a DM
        recipient, even though an active row's empty origins_csv means
        "all")."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM user_preferences WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return row is not None

    def get_user_origins(self, chat_id: int) -> list[str] | None:
        """The resolved origin codes `chat_id` is subscribed to, or None
        meaning "all" - also None (not an error) for a chat_id with no
        row at all, since the settings-menu renderer (dispatch/
        bot_commands.py) shows every box checked by default for a
        brand-new user who hasn't touched a toggle yet. Callers that need
        to know whether a row exists AT ALL (is this chat_id a DM
        recipient to begin with) use has_preferences/is_active instead -
        this method alone never answers that question."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT origins_csv FROM user_preferences WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return _parse_origins_csv(row[0]) if row else None

    def is_active(self, chat_id: int) -> bool:
        """False for a paused ("Alle abwählen"'d, or blocked-bot-
        deactivated) chat_id, or one with no row at all - true only for
        an existing, active row."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT is_active FROM user_preferences WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return bool(row and row[0])

    def get_users_for_origin(self, origin_iata: str) -> list[int]:
        """Every ACTIVE chat_id subscribed to `origin_iata` - "all"
        (origins_csv empty/"*") or an explicit list containing it. A
        chat_id with no row at all (never opted in) is never included -
        "all" is only ever a real preference once a user has actually
        started the bot, never assumed for a stranger (see module
        docstring)."""
        code = origin_iata.strip().upper()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT chat_id, origins_csv FROM user_preferences WHERE is_active = 1"
            ).fetchall()
        return [
            chat_id
            for chat_id, origins_csv in rows
            if (origins := _parse_origins_csv(origins_csv)) is None or code in origins
        ]

    # --- writes ----------------------------------------------------------------

    def set_user_origins(self, chat_id: int, origins: list[str] | None, *, now: datetime | None = None) -> None:
        """Upsert `chat_id`'s subscribed origins - None/[]/["*"] all mean
        "all" (see module docstring). Reactivates the row (is_active=1):
        a user explicitly setting a non-empty origin list is deliberately
        choosing to receive DMs again, even if they'd previously been
        paused."""
        self._upsert(chat_id, origins_csv=_render_origins_csv(origins), is_active=1, now=now)

    def set_active(self, chat_id: int, is_active: bool, *, now: datetime | None = None) -> None:
        """Pause (e.g. a blocked-bot 403 - dispatch/telegram.py's
        _dispatch_personal_dms) or reactivate `chat_id` WITHOUT touching
        its stored origins - creates a row (defaulting to "all") if none
        exists yet. Deliberately never used for "Alle abwählen" itself -
        see deselect_all, which also resets origins to the neutral "all"
        default; a system-detected block is a different kind of pause
        and must never silently discard a user's own explicit
        preference just because Telegram couldn't reach them once."""
        existing = self.get_user_origins(chat_id) if self.has_preferences(chat_id) else None
        self._upsert(chat_id, origins_csv=_render_origins_csv(existing), is_active=1 if is_active else 0, now=now)

    def select_all(self, chat_id: int, *, now: datetime | None = None) -> None:
        """"🌟 Alle auswählen" - every airport, active."""
        self.set_user_origins(chat_id, None, now=now)

    def deselect_all(self, chat_id: int, *, now: datetime | None = None) -> None:
        """"❌ Alle abwählen" - pauses the subscription (is_active=0) AND
        resets origins back to the neutral "all" default - never an
        empty csv (which this project's own rule reads as "all", the
        opposite of what this button means - see module docstring's
        "THE 'ALLE ABWÄHLEN' EDGE CASE"), and never leaves a stale
        explicit subset sitting inertly behind is_active=0 either, so a
        later reactivation (a fresh toggle, or an explicit select_all)
        starts clean rather than silently resurrecting whatever was
        selected right before the user paused."""
        self._upsert(chat_id, origins_csv=_render_origins_csv(None), is_active=0, now=now)

    def toggle_origin(
        self, chat_id: int, origin_iata: str, *, all_origins: tuple[str, ...] = SETTINGS_MENU_AIRPORTS,
        now: datetime | None = None,
    ) -> list[str]:
        """Toggle exactly one origin for `chat_id` (creating a row from
        the "all" default if none exists), returning the resulting
        resolved set. Toggling OFF while currently "all" expands to
        `all_origins` minus the toggled one first - a flat includes-list
        can't otherwise represent "everything except this one". Toggling
        the last excluded one back ON the same way collapses back to the
        canonical "all" state (select_all) rather than storing a literal
        csv of every code - two different paths to "all" must end up
        identically represented, never as two subtly different states
        that happen to resolve the same today. If the result would be
        empty (the user just toggled off their last remaining airport),
        that's treated as "Alle abwählen" (see deselect_all) rather than
        silently writing an empty csv back as "all" again - the opposite
        of what the user just did."""
        code = origin_iata.strip().upper()
        current = self.get_user_origins(chat_id)
        effective = set(all_origins) if current is None else set(current)
        if code in effective:
            effective.discard(code)
        else:
            effective.add(code)

        if not effective:
            self.deselect_all(chat_id, now=now)
            return []
        if effective == set(all_origins):
            self.select_all(chat_id, now=now)
            return list(all_origins)
        new_origins = sorted(effective)
        self.set_user_origins(chat_id, new_origins, now=now)
        return new_origins

    def _upsert(self, chat_id: int, *, origins_csv: str, is_active: int, now: datetime | None) -> None:
        moment = (now or datetime.now(timezone.utc)).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO user_preferences (chat_id, origins_csv, is_active, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET "
                "origins_csv = excluded.origins_csv, is_active = excluded.is_active, updated_at = excluded.updated_at",
                (chat_id, origins_csv, is_active, moment),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)
