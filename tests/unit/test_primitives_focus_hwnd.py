from __future__ import annotations

import pytest

from voice_commander.tools import primitives


class _FakeWin32:
    """Stand-in for the win32gui / win32con / win32process module trio."""

    def __init__(self) -> None:
        self.focused: int | None = None

    # win32gui surface used by focus()
    def IsIconic(self, hwnd: int) -> bool:  # noqa: N802 — match win32 naming
        return False

    def ShowWindow(self, hwnd: int, _flag: int) -> None:  # noqa: N802
        pass

    def GetForegroundWindow(self) -> int:  # noqa: N802
        return 0


def test_focus_with_hwnd_skips_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``_hwnd`` is supplied, ``focus`` must NOT call ``resolve_window``."""
    called = {"resolve": 0, "do_focus": 0, "verify": 0}

    def _no_resolve(_target: str) -> int:
        called["resolve"] += 1
        raise AssertionError("resolve_window should not be called when _hwnd is set")

    def _do_focus(hwnd: int, _fg_tid: int, _t_tid: int) -> None:
        called["do_focus"] += 1
        assert hwnd == 99

    def _verify_foreground(hwnd: int) -> bool:
        called["verify"] += 1
        return hwnd == 99

    monkeypatch.setattr("voice_commander.resolver.resolve_window", _no_resolve)
    monkeypatch.setattr("voice_commander.tools.primitives._do_focus", _do_focus)
    monkeypatch.setattr("voice_commander.tools.primitives._verify_foreground", _verify_foreground)

    out = primitives.focus(target="", _hwnd=99)
    assert out == 99
    assert called == {"resolve": 0, "do_focus": 1, "verify": 1}


def test_focus_target_still_resolves_when_hwnd_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing behaviour preserved: no ``_hwnd`` → resolve_window is called."""

    def _fake_resolve(target: str) -> int:
        assert target == "chrome"
        return 77

    def _do_focus(hwnd: int, _fg: int, _t: int) -> None:
        assert hwnd == 77

    monkeypatch.setattr("voice_commander.resolver.resolve_window", _fake_resolve)
    monkeypatch.setattr("voice_commander.tools.primitives._do_focus", _do_focus)
    monkeypatch.setattr("voice_commander.tools.primitives._verify_foreground", lambda h: True)

    assert primitives.focus("chrome") == 77
