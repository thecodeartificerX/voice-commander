from __future__ import annotations

import pytest

from voice_commander.picker.registry import (
    get_global_picker_registry,
    reset_global_picker_registry,
)
from voice_commander.tools.tabs_picker import (
    TabsPickerSettings,
    _format_title,
    build_tabs_picker,
    register_tabs_picker,
)
from voice_commander.tools.tabs_uia import TabInfo


@pytest.fixture(autouse=True)
def _reset_pickers():
    reset_global_picker_registry()
    yield
    reset_global_picker_registry()


def _tabs(*titles: str) -> list[TabInfo]:
    return [TabInfo(index=i, title=t) for i, t in enumerate(titles)]


def test_format_title_truncates_long_strings():
    out = _format_title("a" * 100, max_len=40)
    assert len(out) == 40
    assert out.endswith("…")


def test_format_title_passes_short_strings_through():
    assert _format_title("hello") == "hello"


def test_provider_returns_empty_when_no_foreground():
    provider = build_tabs_picker(
        foreground_hwnd=lambda: 0,
        settings=TabsPickerSettings(),
        enumerate_tabs=lambda hwnd: _tabs("t1", "t2"),
    )
    assert provider() == []


def test_provider_returns_empty_when_enumerator_returns_empty():
    """The active window has no tabs — picker framework will miss-chime,
    matching the spec: 'brings up nothing if the active window doesn't
    have tabs.'"""
    provider = build_tabs_picker(
        foreground_hwnd=lambda: 1234,
        settings=TabsPickerSettings(),
        enumerate_tabs=lambda hwnd: [],
    )
    assert provider() == []


def test_provider_builds_pickeritems_with_correct_plan():
    provider = build_tabs_picker(
        foreground_hwnd=lambda: 9999,
        settings=TabsPickerSettings(),
        enumerate_tabs=lambda hwnd: _tabs("Gmail", "GitHub", "YouTube"),
    )
    items = provider()
    # Page titles go in the ``app`` slot (single-line card layout).
    assert [it.app for it in items] == ["Gmail", "GitHub", "YouTube"]

    plan0 = items[0].action
    assert plan0.steps[0].name == "tabs"
    assert plan0.steps[0].kwargs == {
        "_browser_hwnd": 9999,
        "_tab_index": 0,
        "_tab_title": "Gmail",
    }
    assert plan0.raw_response["router"] == "picker"
    assert plan0.raw_response["verb"] == "tabs"
    assert plan0.raw_response["selection"] == "Gmail"
    assert plan0.raw_response["browser_hwnd"] == 9999
    assert plan0.raw_response["tab_index"] == 0


def test_provider_truncates_to_cap():
    provider = build_tabs_picker(
        foreground_hwnd=lambda: 7,
        settings=TabsPickerSettings(cap=2),
        enumerate_tabs=lambda hwnd: _tabs("a", "b", "c", "d"),
    )
    items = provider()
    assert [it.app for it in items] == ["a", "b"]


def test_provider_passes_through_foreground_hwnd_to_enumerator():
    captured = {}

    def _enum(hwnd: int) -> list[TabInfo]:
        captured["hwnd"] = hwnd
        return _tabs("t1")

    provider = build_tabs_picker(
        foreground_hwnd=lambda: 12345,
        settings=TabsPickerSettings(),
        enumerate_tabs=_enum,
    )
    provider()
    assert captured["hwnd"] == 12345


def test_provider_card_layout_single_line_title_in_app_slot():
    """All cards come from the same browser. To avoid repeating the
    browser name on every row, the provider puts the page title in the
    ``app`` slot (bold primary line) and leaves ``title`` empty so the
    modal draws a clean single-line card."""
    provider = build_tabs_picker(
        foreground_hwnd=lambda: 1,
        settings=TabsPickerSettings(),
        enumerate_tabs=lambda hwnd: _tabs("Inbox"),
    )
    item = provider()[0]
    assert item.app == "Inbox"
    assert item.title == ""
    assert item.label == "Inbox"


def test_register_tabs_picker_lands_on_global_registry():
    register_tabs_picker(
        foreground_hwnd=lambda: 0,
        settings=TabsPickerSettings(),
    )
    assert get_global_picker_registry().has("tabs")
