"""Unit tests for the trimmed sprite plan_outcome / tool_fired handlers."""

from __future__ import annotations

import pytest

from voice_sprite.chat_log import ChatLog
from voice_sprite.plan_outcome_handler import handle_plan_outcome, handle_tool_fired


@pytest.fixture()
def chat_log() -> ChatLog:
    return ChatLog(max_lines=5, hold_ms=4000, fade_ms=3000)


def _make_outcome(status: str, *, steps=(), failed_step_index=None) -> dict:
    return {
        "transcript": "anything",
        "steps": list(steps),
        "status": status,
        "failed_step_index": failed_step_index,
        "error_msg": None,
        "duration_ms": 12,
    }


def _entries(chat_log: ChatLog):
    """Return current chat-log entries via whatever accessor the class exposes."""
    # ChatLog.entries() is a method (returns newest-first list)
    if callable(getattr(chat_log, "entries", None)):
        return chat_log.entries()
    if hasattr(chat_log, "snapshot"):
        return chat_log.snapshot()
    return list(chat_log)


def test_plan_outcome_miss_appends_no_match(chat_log: ChatLog) -> None:
    handle_plan_outcome(_make_outcome("miss"), chat_log, now_provider=lambda: 1.0)
    entries = _entries(chat_log)
    assert len(entries) == 1
    assert entries[0].text == "no match"
    assert entries[0].status == "miss"


def test_plan_outcome_ok_appends_nothing(chat_log: ChatLog) -> None:
    handle_plan_outcome(_make_outcome("ok"), chat_log, now_provider=lambda: 1.0)
    assert len(_entries(chat_log)) == 0


def test_plan_outcome_error_with_index(chat_log: ChatLog) -> None:
    handle_plan_outcome(
        _make_outcome("error", steps=[{"name": "press", "kwargs": {"combo": "ctrl+c"}}], failed_step_index=0),
        chat_log,
        now_provider=lambda: 1.0,
    )
    entries = _entries(chat_log)
    assert entries[0].text == "press failed"


def test_plan_outcome_error_no_index(chat_log: ChatLog) -> None:
    handle_plan_outcome(_make_outcome("error"), chat_log, now_provider=lambda: 1.0)
    entries = _entries(chat_log)
    assert entries[0].text == "command failed"


def test_tool_fired_appends_raw_name(chat_log: ChatLog) -> None:
    handle_tool_fired({"name": "click"}, chat_log, now_provider=lambda: 1.0)
    entries = _entries(chat_log)
    assert entries[0].text == "click"
    assert entries[0].status == "ok"


def test_tool_fired_no_name_is_noop(chat_log: ChatLog) -> None:
    handle_tool_fired({}, chat_log, now_provider=lambda: 1.0)
    assert len(_entries(chat_log)) == 0
