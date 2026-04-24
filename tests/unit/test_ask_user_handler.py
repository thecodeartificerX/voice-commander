"""Unit tests for handle_ask_user (plan_outcome_handler.py)."""

from __future__ import annotations

from voice_sprite.chat_log import ChatLog
from voice_sprite.plan_outcome_handler import handle_ask_user


def _chat_log() -> ChatLog:
    return ChatLog(max_lines=10, hold_ms=3000, fade_ms=1000)


# --- question appended to chat log ---


def test_handle_ask_user_appends_question_to_chat_log():
    """Question text appears as a single 'ok' entry in the chat log."""
    log = _chat_log()

    handle_ask_user({"question": "Which terminal?", "options": ""}, log)

    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].text == "Which terminal?"
    assert entries[0].status == "ok"


# --- options included in text ---


def test_handle_ask_user_includes_options_in_text():
    """When options are present, text is 'question\\noptions'."""
    log = _chat_log()

    handle_ask_user(
        {"question": "Which terminal?", "options": "1. VS Code, 2. Terminal"},
        log,
    )

    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].text == "Which terminal?\n1. VS Code, 2. Terminal"


# --- empty question ignored ---


def test_handle_ask_user_ignores_empty_question():
    """An event with an empty question string must not append any entry."""
    log = _chat_log()

    handle_ask_user({"question": "", "options": ""}, log)

    assert log.entries() == []


# --- now_provider ---


def test_handle_ask_user_uses_now_provider():
    """born_at_s of the appended entry equals the now_provider() result."""
    log = _chat_log()
    sentinel = 77.5

    handle_ask_user(
        {"question": "Which terminal?", "options": ""},
        log,
        now_provider=lambda: sentinel,
    )

    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].born_at_s == sentinel
