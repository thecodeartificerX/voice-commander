from __future__ import annotations

import pytest

from voice_commander.picker.mru import MruTracker, MruEntry


def _entry(hwnd: int, *, pid: int = 100, proc: str = "chrome.exe", title: str | None = None) -> MruEntry:
    return MruEntry(hwnd=hwnd, pid=pid, proc_name=proc, title=title or f"win{hwnd}")


def test_record_pushes_to_front_and_dedupes():
    tracker = MruTracker(capacity=8)
    tracker.record(_entry(1))
    tracker.record(_entry(2))
    tracker.record(_entry(3))
    tracker.record(_entry(1))  # re-focus moves to front, no duplicate
    hwnds = [e.hwnd for e in tracker.snapshot()]
    assert hwnds == [1, 3, 2]


def test_capacity_bounds_buffer():
    tracker = MruTracker(capacity=3)
    for i in range(1, 6):
        tracker.record(_entry(i))
    hwnds = [e.hwnd for e in tracker.snapshot()]
    assert hwnds == [5, 4, 3]


def test_top_filters_with_predicate():
    tracker = MruTracker(capacity=8)
    for i in range(1, 6):
        tracker.record(_entry(i))
    # Keep only even hwnds.
    top = tracker.top(2, predicate=lambda e: e.hwnd % 2 == 0)
    assert [e.hwnd for e in top] == [4, 2]


def test_top_respects_n_cap():
    tracker = MruTracker(capacity=8)
    for i in range(1, 6):
        tracker.record(_entry(i))
    top = tracker.top(2)
    assert [e.hwnd for e in top] == [5, 4]


def test_top_returns_empty_when_buffer_empty():
    tracker = MruTracker(capacity=8)
    assert tracker.top(5) == []


def test_register_self_hwnd_excludes_it_from_top():
    tracker = MruTracker(capacity=8)
    tracker.register_self_hwnd(42)
    tracker.record(_entry(1))
    tracker.record(_entry(42))
    tracker.record(_entry(2))
    top = tracker.top(5, predicate=lambda e: e.hwnd not in tracker.self_hwnds)
    assert [e.hwnd for e in top] == [2, 1]
