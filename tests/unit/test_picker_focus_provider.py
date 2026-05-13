from __future__ import annotations

import pytest

from voice_commander.picker.mru import MruEntry, MruTracker
from voice_commander.picker.registry import reset_global_picker_registry
from voice_commander.tools.focus_picker import (
    FocusPickerSettings,
    build_focus_picker,
    format_label,
)


@pytest.fixture(autouse=True)
def _reset_pickers():
    reset_global_picker_registry()
    yield
    reset_global_picker_registry()


def _entry(hwnd: int, *, proc: str = "chrome.exe", title: str = "Untitled") -> MruEntry:
    return MruEntry(hwnd=hwnd, pid=hwnd * 10, proc_name=proc, title=title)


def test_format_label_combines_proc_and_title():
    e = _entry(1, proc="chrome.exe", title="voice-commander - Google Chrome")
    assert format_label(e) == "Chrome — voice-commander - Google Chrome"


def test_format_label_strips_exe_suffix():
    e = _entry(1, proc="Code.exe", title="daemon.py - voice-commander")
    assert format_label(e) == "Code — daemon.py - voice-commander"


def test_format_label_falls_back_to_title_only_when_proc_empty():
    e = _entry(1, proc="", title="My Window")
    assert format_label(e) == "My Window"


def test_format_label_truncates_long_titles():
    e = _entry(
        1,
        proc="chrome.exe",
        title="a really long window title that exceeds the cap to keep modal width sane",
    )
    label = format_label(e, max_len=40)
    assert len(label) <= 40
    assert label.endswith("…")


def test_provider_returns_top_cap_entries():
    tracker = MruTracker(capacity=16)
    for h in range(1, 8):
        tracker.record(_entry(h, proc=f"app{h}.exe", title=f"win{h}"))

    settings = FocusPickerSettings(cap=5, exclude_foreground=False, exclude_self=False)
    provider = build_focus_picker(
        tracker=tracker,
        settings=settings,
        foreground_hwnd=lambda: 0,
    )
    items = provider()
    assert len(items) == 5
    # Newest first.
    assert [it.action.steps[0].kwargs["_hwnd"] for it in items] == [7, 6, 5, 4, 3]
    assert items[0].action.steps[0].name == "focus"
    assert items[0].action.raw_response["router"] == "picker"


def test_provider_excludes_current_foreground():
    tracker = MruTracker(capacity=16)
    for h in range(1, 6):
        tracker.record(_entry(h, proc=f"app{h}.exe"))
    settings = FocusPickerSettings(cap=5, exclude_foreground=True, exclude_self=False)
    provider = build_focus_picker(
        tracker=tracker, settings=settings, foreground_hwnd=lambda: 4
    )
    hwnds = [it.action.steps[0].kwargs["_hwnd"] for it in provider()]
    assert 4 not in hwnds


def test_provider_excludes_self_hwnds_when_enabled():
    tracker = MruTracker(capacity=16)
    tracker.register_self_hwnd(2)
    for h in range(1, 6):
        tracker.record(_entry(h, proc=f"app{h}.exe"))
    settings = FocusPickerSettings(cap=5, exclude_foreground=False, exclude_self=True)
    provider = build_focus_picker(
        tracker=tracker, settings=settings, foreground_hwnd=lambda: 0
    )
    hwnds = [it.action.steps[0].kwargs["_hwnd"] for it in provider()]
    assert 2 not in hwnds


def test_provider_returns_empty_when_no_candidates():
    tracker = MruTracker(capacity=8)
    settings = FocusPickerSettings(cap=5, exclude_foreground=False, exclude_self=False)
    provider = build_focus_picker(
        tracker=tracker, settings=settings, foreground_hwnd=lambda: 0
    )
    assert provider() == []
