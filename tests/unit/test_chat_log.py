"""Tests for ChatLog ring buffer + fade curve."""

from __future__ import annotations

from voice_sprite.chat_log import ChatLog, ChatLogEntry


def _entry(text: str, born_at_s: float, status: str = "ok") -> ChatLogEntry:
    return ChatLogEntry(text=text, status=status, born_at_s=born_at_s)  # type: ignore[arg-type]


def test_append_below_cap_retains_all():
    log = ChatLog(max_lines=3, hold_ms=1000, fade_ms=1000)
    log.append(_entry("a", 0.0))
    log.append(_entry("b", 1.0))
    assert [e.text for e in log.entries()] == ["b", "a"]  # newest-first


def test_append_over_cap_evicts_oldest():
    log = ChatLog(max_lines=2, hold_ms=1000, fade_ms=1000)
    log.append(_entry("a", 0.0))
    log.append(_entry("b", 1.0))
    log.append(_entry("c", 2.0))
    assert [e.text for e in log.entries()] == ["c", "b"]


def test_opacity_full_during_hold():
    log = ChatLog(max_lines=3, hold_ms=1000, fade_ms=1000)
    e = _entry("x", 10.0)
    log.append(e)
    assert log.opacity_of(e, 10.0) == 1.0
    assert log.opacity_of(e, 10.999) == 1.0  # still in hold window


def test_opacity_linear_after_hold():
    log = ChatLog(max_lines=3, hold_ms=1000, fade_ms=1000)
    e = _entry("x", 0.0)
    log.append(e)
    assert log.opacity_of(e, 1.0) == 1.0  # right at hold boundary
    assert abs(log.opacity_of(e, 1.5) - 0.5) < 1e-6
    assert log.opacity_of(e, 2.0) == 0.0
    assert log.opacity_of(e, 3.0) == 0.0  # clamped


def test_tick_evicts_fully_faded():
    log = ChatLog(max_lines=5, hold_ms=100, fade_ms=100)
    log.append(_entry("a", 0.0))
    log.append(_entry("b", 0.5))
    log.tick(0.1)
    assert [e.text for e in log.entries()] == ["b", "a"]  # still in hold/fade
    log.tick(1.0)  # now well past both fade windows
    assert log.entries() == []
