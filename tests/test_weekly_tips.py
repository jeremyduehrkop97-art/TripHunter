"""dispatch/weekly_tips.py: content, VIP/Free framing, rotation and dispatch."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trip_hunter.dispatch.weekly_tips import (
    FREE_CHANNEL_UPSELL_LINE,
    TIPS,
    dispatch_weekly_tip,
    format_free_tip,
    format_vip_tip,
    free_tip_keyboard,
    next_tip_index,
    select_weekly_tip,
    tip_keyboard,
)
from trip_hunter.weekly_tip_repository import WeeklyTipRepository

_NOW = datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc)  # a Sunday

_AFFILIATE_VARS = ("ESIM_AFFILIATE_URL", "FLIGHT_COMPENSATION_AFFILIATE_URL", "SKIP_LINE_TICKETS_AFFILIATE_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (*_AFFILIATE_VARS, "VIP_SUBSCRIPTION_URL", "TELEGRAM_BOT_USERNAME"):
        monkeypatch.delenv(name, raising=False)


# --- content -----------------------------------------------------------------------


def test_there_are_exactly_three_tips_with_unique_keys():
    assert len(TIPS) == 3
    assert len({tip.key for tip in TIPS}) == 3


def test_the_three_named_topics_are_all_covered():
    """The task's exact three tips: eSIM roaming, flight-delay
    compensation, skip-the-line tickets."""
    bodies = " ".join(tip.header + tip.body for tip in TIPS).lower()
    assert "esim" in bodies and "airalo" in bodies and "yesim" in bodies
    assert "600" in bodies and "compensair" in bodies and "airhelp" in bodies
    assert "tiqets" in bodies and "klook" in bodies


def test_every_tip_has_a_header_a_body_a_cta_and_a_real_affiliate_url():
    for tip in TIPS:
        assert tip.header.startswith("<b>") and tip.header.endswith("</b>")
        assert tip.body and tip.cta_text
        assert tip.affiliate_url().startswith("https://")


# --- VIP vs. Free framing ------------------------------------------------------


def test_vip_framing_has_no_upsell_line():
    for tip in TIPS:
        text = format_vip_tip(tip)
        assert tip.header in text and tip.body in text
        assert FREE_CHANNEL_UPSELL_LINE not in text
        assert "Insider-Tipp für Member" in text


def test_free_framing_has_the_exact_same_content_plus_the_upsell_line():
    for tip in TIPS:
        vip_text = format_vip_tip(tip)
        free_text = format_free_tip(tip)
        assert free_text.startswith(vip_text)
        assert free_text == vip_text + "\n\n" + FREE_CHANNEL_UPSELL_LINE
        assert "VIP-Kanal" in FREE_CHANNEL_UPSELL_LINE


def test_vip_keyboard_has_only_the_affiliate_button():
    for tip in TIPS:
        (row,) = tip_keyboard(tip)["inline_keyboard"]
        (button,) = row
        assert button["text"] == tip.cta_text and button["url"] == tip.affiliate_url()


def test_free_keyboard_adds_the_vip_upsell_button_underneath():
    for tip in TIPS:
        rows = free_tip_keyboard(tip)["inline_keyboard"]
        assert len(rows) == 2
        assert rows[0][0]["url"] == tip.affiliate_url()
        assert "VIP" in rows[1][0]["text"]
        assert rows[1][0]["url"].startswith("https://")


def test_free_keyboard_never_carries_a_second_booking_style_link():
    """Matches every other Free-channel funnel element in this project:
    one upsell button, never a second real booking/affiliate link beyond
    the tip's own."""
    for tip in TIPS:
        rows = free_tip_keyboard(tip)["inline_keyboard"]
        urls = [button["url"] for row in rows for button in row]
        assert urls.count(tip.affiliate_url()) == 1


# --- rotation (pure function) ---------------------------------------------------


def test_next_index_starts_at_zero_when_never_sent_before():
    assert next_tip_index(None, 3) == 0


@pytest.mark.parametrize("last_index, tip_count, expected", [(0, 3, 1), (1, 3, 2), (2, 3, 0), (0, 4, 1), (3, 4, 0)])
def test_next_index_advances_and_wraps(last_index, tip_count, expected):
    assert next_tip_index(last_index, tip_count) == expected


def test_select_weekly_tip_does_not_itself_record_anything(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    index, tip = select_weekly_tip(repo)

    assert (index, tip) == (0, TIPS[0])
    assert repo.last_sent() is None  # unchanged - selecting is not recording


def test_rotation_cycles_through_all_tips_in_order_then_repeats(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    seen_keys = []
    for week in range(len(TIPS) * 2):
        index, tip = select_weekly_tip(repo)
        repo.record_sent(index, now=_NOW)
        seen_keys.append(tip.key)

    assert seen_keys == [tip.key for tip in TIPS] * 2


# --- dispatch --------------------------------------------------------------------


class _Sender:
    def __init__(self):
        self.calls: list[tuple[str, str, list[dict]]] = []

    def __call__(self, text, chat_id, keyboards) -> bool:
        self.calls.append((text, chat_id, keyboards))
        return True


def test_dispatch_sends_to_both_channels_and_advances_rotation(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    sender = _Sender()

    tip = dispatch_weekly_tip(repo, send_fn=sender, free_chat_id="free", vip_chat_id="vip", now=_NOW)

    assert tip is TIPS[0]
    assert len(sender.calls) == 2
    chat_ids = {call[1] for call in sender.calls}
    assert chat_ids == {"free", "vip"}
    vip_call = next(c for c in sender.calls if c[1] == "vip")
    free_call = next(c for c in sender.calls if c[1] == "free")
    assert vip_call[0] == format_vip_tip(tip)
    assert free_call[0] == format_free_tip(tip)
    state = repo.last_sent()
    assert state.last_index == 0 and state.last_sent_at == _NOW


def test_dispatch_rotates_to_the_next_tip_on_each_subsequent_call(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    sender = _Sender()

    first = dispatch_weekly_tip(repo, send_fn=sender, free_chat_id="free", vip_chat_id="vip", now=_NOW)
    second = dispatch_weekly_tip(repo, send_fn=sender, free_chat_id="free", vip_chat_id="vip", now=_NOW)

    assert (first.key, second.key) == (TIPS[0].key, TIPS[1].key)


def test_dispatch_with_only_one_channel_configured_sends_only_there(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    sender = _Sender()

    dispatch_weekly_tip(repo, send_fn=sender, free_chat_id=None, vip_chat_id="vip", now=_NOW)

    assert len(sender.calls) == 1 and sender.calls[0][1] == "vip"


def test_dispatch_with_neither_channel_configured_sends_nothing_and_does_not_advance(tmp_path):
    repo = WeeklyTipRepository(tmp_path / "t.db")
    sender = _Sender()

    result = dispatch_weekly_tip(repo, send_fn=sender, free_chat_id=None, vip_chat_id=None, now=_NOW)

    assert result is None
    assert sender.calls == []
    assert repo.last_sent() is None  # rotation never advanced - nothing was actually sent


def test_dispatch_advances_rotation_even_if_the_send_itself_fails(tmp_path):
    """Deliberate: a low-stakes weekly content post, not a perishable
    deal - re-sending the same tip next week over one transient failure
    would cost the rotation more than it gains (see the function's own
    docstring)."""
    repo = WeeklyTipRepository(tmp_path / "t.db")

    def failing_sender(text, chat_id, keyboards) -> bool:
        return False

    dispatch_weekly_tip(repo, send_fn=failing_sender, free_chat_id="free", vip_chat_id="vip", now=_NOW)

    assert repo.last_sent().last_index == 0
