"""Tests for dispatch/bot_commands.py. handle_update/build_airport_settings_
keyboard are pure logic (injected send/edit/answer callables) - no real
HTTP call anywhere in this file. run_once's own Telegram I/O is exercised
separately in test_bot_commands_live_io.py-style tests below via a fake
requests.Session, the same pattern dispatch/test_telegram_dispatch.py uses."""

from __future__ import annotations

from pathlib import Path

import pytest

from trip_hunter.dispatch.bot_commands import (
    _DESELECT_ALL_CALLBACK,
    _DONE_CALLBACK,
    _SELECT_ALL_CALLBACK,
    build_airport_settings_keyboard,
    handle_update,
    run_once,
)
from trip_hunter.user_preference_repository import SETTINGS_MENU_AIRPORTS, UserPreferenceRepository


class _Recorder:
    def __init__(self):
        self.sent: list[tuple] = []
        self.edited: list[tuple] = []
        self.answered: list[tuple] = []

    def send_fn(self, chat_id, text, keyboard) -> bool:
        self.sent.append((chat_id, text, keyboard))
        return True

    def edit_keyboard_fn(self, chat_id, message_id, keyboard) -> bool:
        self.edited.append((chat_id, message_id, keyboard))
        return True

    def answer_callback_fn(self, callback_query_id, text) -> bool:
        self.answered.append((callback_query_id, text))
        return True


def _message_update(text: str, chat_id: int = 555) -> dict:
    return {"message": {"text": text, "chat": {"id": chat_id}}}


def _callback_update(data: str, *, chat_id: int = 555, message_id: int = 10, callback_id: str = "cb1") -> dict:
    return {"callback_query": {"id": callback_id, "data": data, "message": {"message_id": message_id, "chat": {"id": chat_id}}}}


# --- keyboard structure ------------------------------------------------------------


def test_keyboard_has_five_toggle_rows_of_two_plus_two_control_rows(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    kb = build_airport_settings_keyboard(555, repo)
    rows = kb["inline_keyboard"]

    assert len(rows) == 5 + 2
    for row in rows[:5]:
        assert len(row) == 2
    assert [b["text"] for b in rows[5]] == ["🌟 Alle auswählen", "❌ Alle abwählen"]
    assert [b["text"] for b in rows[6]] == ["💾 Fertig & Speichern"]


def test_keyboard_pairs_match_the_tasks_own_requested_layout(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rows = build_airport_settings_keyboard(555, repo)["inline_keyboard"]
    codes_per_row = [[b["callback_data"].split(":")[1] for b in row] for row in rows[:5]]

    assert codes_per_row == [["FRA", "MUC"], ["BER", "HAM"], ["DUS", "CGN"], ["STR", "VIE"], ["ZRH", "BSL"]]


def test_a_brand_new_chat_id_sees_every_airport_checked_by_default(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rows = build_airport_settings_keyboard(555, repo)["inline_keyboard"]
    for row in rows[:5]:
        for button in row:
            assert button["text"].startswith("✅")


def test_a_toggled_off_airport_shows_the_unchecked_glyph(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.set_user_origins(555, [c for c in SETTINGS_MENU_AIRPORTS if c != "FRA"])

    rows = build_airport_settings_keyboard(555, repo)["inline_keyboard"]
    fra_button = next(b for row in rows[:5] for b in row if b["callback_data"] == "airport:FRA")
    other_button = next(b for row in rows[:5] for b in row if b["callback_data"] == "airport:MUC")

    assert fra_button["text"].startswith("⬜")
    assert other_button["text"].startswith("✅")


def test_every_button_fits_telegrams_64_byte_callback_data_limit(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rows = build_airport_settings_keyboard(555, repo)["inline_keyboard"]
    for row in rows:
        for button in row:
            assert len(button["callback_data"].encode("utf-8")) <= 64


# --- /start, /airports, /settings --------------------------------------------------


@pytest.mark.parametrize("command", ["/start", "/airports", "/settings"])
def test_every_start_command_sends_the_welcome_text_and_keyboard(tmp_path, command):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rec = _Recorder()

    handle_update(_message_update(command), repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)

    assert len(rec.sent) == 1
    chat_id, text, keyboard = rec.sent[0]
    assert chat_id == 555
    assert "persönlichen Deal-Alarme" in text
    assert len(keyboard["inline_keyboard"]) == 7


def test_an_unrelated_plain_message_is_silently_ignored(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rec = _Recorder()

    handle_update(_message_update("hallo bot wie geht's?"), repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)

    assert rec.sent == [] and rec.edited == [] and rec.answered == []


# --- callback queries ---------------------------------------------------------------


def test_toggling_an_airport_updates_the_repository_and_edits_the_keyboard_in_place(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rec = _Recorder()

    handle_update(_callback_update("airport:FRA"), repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)

    assert "FRA" not in repo.get_user_origins(555)
    assert len(rec.edited) == 1
    chat_id, message_id, keyboard = rec.edited[0]
    assert (chat_id, message_id) == (555, 10)
    fra_button = next(b for row in keyboard["inline_keyboard"][:5] for b in row if b["callback_data"] == "airport:FRA")
    assert fra_button["text"].startswith("⬜")
    assert len(rec.answered) == 1 and rec.answered[0][0] == "cb1"


def test_toggling_the_same_airport_twice_toggles_it_back_on(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rec = _Recorder()

    for _ in range(2):
        handle_update(_callback_update("airport:FRA"), repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)

    assert repo.get_user_origins(555) is None  # back to "all"


def test_select_all_callback(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.set_user_origins(555, ["FRA"])
    rec = _Recorder()

    handle_update(_callback_update(_SELECT_ALL_CALLBACK), repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)

    assert repo.get_user_origins(555) is None
    assert repo.is_active(555) is True
    assert "Alle DACH-Flughäfen aktiv" in rec.answered[0][1]


def test_deselect_all_callback(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.select_all(555)
    rec = _Recorder()

    handle_update(_callback_update(_DESELECT_ALL_CALLBACK), repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)

    assert repo.is_active(555) is False
    assert "pausiert" in rec.answered[0][1]


def test_done_callback_answers_but_never_edits_the_keyboard(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rec = _Recorder()

    handle_update(_callback_update(_DONE_CALLBACK), repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)

    assert rec.edited == []
    assert len(rec.answered) == 1 and "Gespeichert" in rec.answered[0][1]


def test_an_unknown_callback_data_is_answered_empty_and_changes_nothing(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rec = _Recorder()

    handle_update(_callback_update("something:unexpected"), repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)

    assert repo.has_preferences(555) is False
    assert rec.edited == []
    assert len(rec.answered) == 1 and rec.answered[0][1] == ""


def test_a_callback_with_no_resolvable_chat_or_message_is_answered_and_ignored(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rec = _Recorder()

    handle_update({"callback_query": {"id": "cb9", "data": "airport:FRA", "message": {}}}, repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)

    assert rec.edited == [] and len(rec.answered) == 1


def test_an_update_with_neither_message_nor_callback_is_ignored(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    rec = _Recorder()
    handle_update({"some_other_update_type": {}}, repo, send_fn=rec.send_fn, edit_keyboard_fn=rec.edit_keyboard_fn, answer_callback_fn=rec.answer_callback_fn)
    assert rec.sent == [] and rec.edited == [] and rec.answered == []


# --- run_once: real Telegram I/O (fake session, no network) ---------------------


class _Resp:
    def __init__(self, ok=True, result=None, status_code=200):
        self.status_code = status_code
        self._ok = ok
        self._result = result

    def json(self):
        return {"ok": self._ok, "result": self._result}


class _Session:
    def __init__(self, updates):
        self.calls = []
        self._updates = updates

    def post(self, url, data=None, timeout=None):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, data))
        if method == "getUpdates":
            return _Resp(result=self._updates)
        return _Resp(result={})


def test_run_once_processes_updates_and_persists_the_offset(tmp_path):
    db_path = tmp_path / "t.db"
    repo = UserPreferenceRepository(db_path)
    updates = [
        {"update_id": 100, "message": {"text": "/start", "chat": {"id": 555}}},
        {"update_id": 101, "callback_query": {"id": "cb1", "data": "airport:FRA", "message": {"message_id": 1, "chat": {"id": 555}}}},
    ]
    session = _Session(updates)

    processed = run_once("123:ABC", repo=repo, db_path=db_path, session=session)

    assert processed == 2
    assert "FRA" not in repo.get_user_origins(555)
    methods = [c[0] for c in session.calls]
    assert "sendMessage" in methods and "editMessageReplyMarkup" in methods and "answerCallbackQuery" in methods

    # Second run with no new updates from Telegram's side: offset was sent as 101+1.
    session2 = _Session([])
    run_once("123:ABC", repo=repo, db_path=db_path, session=session2)
    get_updates_call = next(c for c in session2.calls if c[0] == "getUpdates")
    assert get_updates_call[1]["offset"] == 101 + 1


def test_run_once_with_no_updates_processes_nothing(tmp_path):
    db_path = tmp_path / "t.db"
    repo = UserPreferenceRepository(db_path)
    processed = run_once("123:ABC", repo=repo, db_path=db_path, session=_Session([]))
    assert processed == 0


def test_run_once_skips_one_bad_update_without_losing_the_rest(tmp_path):
    db_path = tmp_path / "t.db"
    repo = UserPreferenceRepository(db_path)
    updates = [
        {"update_id": 1, "message": {"text": "/start", "chat": {}}},  # chat has no "id" -> handled gracefully, not a crash
        {"update_id": 2, "message": {"text": "/start", "chat": {"id": 777}}},
    ]
    session = _Session(updates)

    processed = run_once("123:ABC", repo=repo, db_path=db_path, session=session)

    assert processed == 2  # neither update raises - the first is just a no-op (chat_id is None)
    methods = [c[0] for c in session.calls if c[0] == "sendMessage"]
    assert len(methods) == 1  # only the second update actually sent anything


# --- GitHub Actions workflow -------------------------------------------------------


def test_workflow_polls_every_five_minutes_with_a_manual_dispatch():
    text = (Path(__file__).parent.parent / ".github" / "workflows" / "bot_commands.yml").read_text(encoding="utf-8")
    assert '- cron: "*/5 * * * *"' in text
    assert "workflow_dispatch" in text
    assert "python -m trip_hunter.dispatch.bot_commands" in text
    assert "group: trip-hunter-state" in text and "cancel-in-progress: false" in text
