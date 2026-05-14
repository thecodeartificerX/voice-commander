"""Unit tests for tabs_uia helpers that don't require real UIA / Win32.

The two functions that DO require UIA (list_chromium_tabs, invoke_chromium_tab)
are exercised by the visual E2E harness against a real Comet browser — they
can't be faithfully unit-tested without recreating the COM tree.
"""

from __future__ import annotations

from voice_commander.tools.tabs_uia import (
    CHROMIUM_BROWSER_PROCESSES,
    _find_tab_strip,
)


class _FakeCtrl:
    """Minimal UIA-like control for testing _find_tab_strip without COM."""

    def __init__(
        self,
        type_name: str,
        children: list["_FakeCtrl"] | None = None,
    ) -> None:
        self.ControlTypeName = type_name
        self._children = children or []

    def GetChildren(self) -> list["_FakeCtrl"]:
        return self._children


def test_chromium_allowlist_includes_target_browsers():
    """Required: Comet (the user's daily-driver Perplexity browser)."""
    assert "comet.exe" in CHROMIUM_BROWSER_PROCESSES
    assert "chrome.exe" in CHROMIUM_BROWSER_PROCESSES
    assert "msedge.exe" in CHROMIUM_BROWSER_PROCESSES
    assert "brave.exe" in CHROMIUM_BROWSER_PROCESSES


def test_chromium_allowlist_excludes_electron_apps():
    """VS Code, Slack, Discord all share ClassName="Chrome_WidgetWin_1"
    but are NOT Chromium browsers and should not match."""
    assert "code.exe" not in CHROMIUM_BROWSER_PROCESSES
    assert "code - insiders.exe" not in CHROMIUM_BROWSER_PROCESSES
    assert "slack.exe" not in CHROMIUM_BROWSER_PROCESSES
    assert "discord.exe" not in CHROMIUM_BROWSER_PROCESSES


def test_find_tab_strip_returns_none_when_no_tabcontrol():
    root = _FakeCtrl(
        "WindowControl",
        children=[
            _FakeCtrl("PaneControl", children=[_FakeCtrl("ButtonControl")]),
        ],
    )
    assert _find_tab_strip(root) is None


def test_find_tab_strip_returns_tab_with_tabitem_children():
    tab_items = [
        _FakeCtrl("TabItemControl"),
        _FakeCtrl("TabItemControl"),
        _FakeCtrl("TabItemControl"),
    ]
    strip = _FakeCtrl("TabControl", children=tab_items)
    root = _FakeCtrl(
        "WindowControl",
        children=[
            _FakeCtrl("PaneControl", children=[strip]),
        ],
    )
    assert _find_tab_strip(root) is strip


def test_find_tab_strip_skips_empty_tabcontrols():
    """A TabControl with no TabItem children is decorative — keep searching."""
    empty = _FakeCtrl("TabControl", children=[_FakeCtrl("ButtonControl")])
    real_items = [_FakeCtrl("TabItemControl"), _FakeCtrl("TabItemControl")]
    real = _FakeCtrl("TabControl", children=real_items)
    root = _FakeCtrl("WindowControl", children=[empty, real])
    assert _find_tab_strip(root) is real


def test_find_tab_strip_handles_stale_node_exceptions():
    """A stale UIA element raises on attribute access; we keep walking."""

    class _Stale:
        @property
        def ControlTypeName(self) -> str:
            raise RuntimeError("stale element")

        def GetChildren(self) -> list:
            return []

    good = _FakeCtrl(
        "TabControl", children=[_FakeCtrl("TabItemControl")]
    )
    root = _FakeCtrl("WindowControl", children=[_Stale(), good])
    assert _find_tab_strip(root) is good
