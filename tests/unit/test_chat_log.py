"""Tests for ChatLog ring buffer + fade curve."""

from __future__ import annotations

import pytest

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


# ---------------------------------------------------------------------------
# Edge cases (issue #28)
# ---------------------------------------------------------------------------


def test_fade_ms_zero_drops_to_zero_at_hold_boundary():
    """fade_ms=0: opacity is 1.0 inside hold, then 0.0 at the hold boundary."""
    log = ChatLog(max_lines=3, hold_ms=1000, fade_ms=0)
    e = _entry("x", 0.0)
    log.append(e)
    assert log.opacity_of(e, 0.5) == 1.0   # inside hold window
    assert log.opacity_of(e, 1.0) == 0.0   # exactly at hold boundary → instant drop


def test_hold_ms_zero_fades_immediately():
    """hold_ms=0, fade_ms>0: entry starts fading from the moment it is born."""
    log = ChatLog(max_lines=3, hold_ms=0, fade_ms=1000)
    e = _entry("x", 0.0)
    log.append(e)
    assert log.opacity_of(e, 0.0) == pytest.approx(1.0)               # age=0 → t=0 → full
    assert log.opacity_of(e, 0.5) == pytest.approx(0.5)               # halfway through fade
    assert log.opacity_of(e, 1.0) == pytest.approx(0.0, abs=1e-9)    # fully faded
    assert log.opacity_of(e, 2.0) == pytest.approx(0.0, abs=1e-9)    # clamped


def test_max_lines_one_always_keeps_only_latest():
    """max_lines=1: each append evicts the previous; entries() length is always ≤ 1."""
    log = ChatLog(max_lines=1, hold_ms=1000, fade_ms=1000)
    log.append(_entry("a", 0.0))
    log.append(_entry("b", 1.0))
    log.append(_entry("c", 2.0))
    result = log.entries()
    assert len(result) == 1
    assert result[0].text == "c"


@pytest.mark.parametrize("bad_max_lines", [0, -1, -100])
def test_invalid_max_lines_raises(bad_max_lines: int):
    """max_lines <= 0 must raise ValueError."""
    with pytest.raises(ValueError):
        ChatLog(max_lines=bad_max_lines, hold_ms=1000, fade_ms=1000)


def test_reverse_time_order_entries_newest_appended_first():
    """Appending entries with decreasing born_at_s: entries() is newest-appended-first,
    and opacity is computed from each entry's own born_at_s regardless of insert order."""
    log = ChatLog(max_lines=3, hold_ms=1000, fade_ms=1000)
    first_inserted = _entry("first_inserted", born_at_s=5.0)   # older born_at, appended first
    second_inserted = _entry("second_inserted", born_at_s=1.0) # newer born_at, appended second
    log.append(first_inserted)
    log.append(second_inserted)
    # second_inserted is newest-appended → appears first
    texts = [e.text for e in log.entries()]
    assert texts == ["second_inserted", "first_inserted"]
    # opacity is from born_at_s, not insertion position
    now = 5.5
    # first_inserted: born=5.0, now=5.5, age=0.5 < hold_s=1.0 → still in hold → 1.0
    assert log.opacity_of(first_inserted, now) == pytest.approx(1.0)
    # second_inserted: born=1.0, now=5.5, age=4.5, hold_s=1.0, fade_s=1.0 → t=3.5 → clamped 0.0
    assert log.opacity_of(second_inserted, now) == pytest.approx(0.0, abs=1e-9)


def test_tick_clock_skew_opacity_never_negative():
    """now_s < born_at_s (clock skew): negative age → opacity is 1.0, entry is not evicted."""
    log = ChatLog(max_lines=3, hold_ms=1000, fade_ms=1000)
    e = _entry("future", born_at_s=10.0)
    log.append(e)
    # now is before born_at — opacity must be 1.0, never negative
    assert log.opacity_of(e, 5.0) == pytest.approx(1.0)
    assert log.opacity_of(e, 0.0) == pytest.approx(1.0)
    # tick with skewed clock must not evict the entry
    log.tick(5.0)
    assert len(log.entries()) == 1


@pytest.mark.parametrize("bad_hold_ms", [-1, -1000])
def test_invalid_hold_ms_raises(bad_hold_ms: int):
    """hold_ms < 0 must raise ValueError."""
    with pytest.raises(ValueError):
        ChatLog(max_lines=3, hold_ms=bad_hold_ms, fade_ms=1000)


@pytest.mark.parametrize("bad_fade_ms", [-1, -1000])
def test_invalid_fade_ms_raises(bad_fade_ms: int):
    """fade_ms < 0 must raise ValueError."""
    with pytest.raises(ValueError):
        ChatLog(max_lines=3, hold_ms=1000, fade_ms=bad_fade_ms)


def test_both_zero_ms_entry_instantly_invisible():
    """hold_ms=0, fade_ms=0: opacity is 0.0 at birth and entry evicted on first tick."""
    log = ChatLog(max_lines=3, hold_ms=0, fade_ms=0)
    e = _entry("x", 0.0)
    log.append(e)
    assert log.opacity_of(e, 0.0) == 0.0   # instantly invisible at birth
    assert log.opacity_of(e, 1.0) == 0.0
    log.tick(0.0)
    assert log.entries() == []              # immediately evicted
