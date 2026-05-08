"""Unit tests for the 9-verb LLM-only primitive catalog (ADR 0042).

Covers each verb with pyautogui + resolver + win32 helpers mocked. Verifies
self-verify behaviors where applicable (focus raises, close* warn on timeout).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from voice_commander.resolver import OpenResolveError
from voice_commander.tools._win32 import FocusWindowError
from voice_commander.tools.primitives import (
    click,
    no_match,
    open_target,
    press,
    scroll,
    type_text,
    wait,
)

# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------


def test_wait_sleeps() -> None:
    with patch("voice_commander.tools.primitives.time.sleep") as mock_sleep:
        wait(250)
    mock_sleep.assert_called_once_with(0.25)


# ---------------------------------------------------------------------------
# press
# ---------------------------------------------------------------------------


def test_press_splits_on_plus() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press("ctrl+shift+t")
    mock_hotkey.assert_called_once_with("ctrl", "shift", "t")


def test_press_strips_whitespace() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press("ctrl + c")
    mock_hotkey.assert_called_once_with("ctrl", "c")


def test_press_splits_on_whitespace_only() -> None:
    """LLM / VerbRouter often emits 'Ctrl V' (no plus). Must still fire."""
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press("Ctrl V")
    mock_hotkey.assert_called_once_with("ctrl", "v")


def test_press_aliases_control_to_ctrl() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press("control v")
    mock_hotkey.assert_called_once_with("ctrl", "v")


def test_press_aliases_full_words() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press("windows option escape")
    mock_hotkey.assert_called_once_with("win", "alt", "esc")


def test_press_splits_on_and() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press("ctrl and shift and t")
    mock_hotkey.assert_called_once_with("ctrl", "shift", "t")


def test_press_splits_on_comma() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press("ctrl, c")
    mock_hotkey.assert_called_once_with("ctrl", "c")


def test_press_splits_on_hyphen() -> None:
    """Whisper transcribes 'Ctrl C' as 'Ctrl-C' often. Must still fire."""
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press("Ctrl-C")
    mock_hotkey.assert_called_once_with("ctrl", "c")


def test_press_unknown_key_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    """Reject combos containing keys pyautogui won't recognize, so a typo
    surfaces as a WARNING rather than a silent no-op."""
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        with caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"):
            press("ctrl xyzzy")
    mock_hotkey.assert_not_called()
    assert any("unknown key" in r.message for r in caplog.records)


def test_press_empty_combo_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        with caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"):
            press("   ")
    mock_hotkey.assert_not_called()
    assert any("empty combo" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# type
# ---------------------------------------------------------------------------


def test_type_uses_interval_20ms() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.write") as mock_write:
        type_text("hello")
    mock_write.assert_called_once_with("hello", interval=0.02)


def test_type_truncates_long_text() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.write") as mock_write:
        type_text("a" * 600)
    args, _ = mock_write.call_args
    assert len(args[0]) == 500


# ---------------------------------------------------------------------------
# focus
# ---------------------------------------------------------------------------


def test_focus_calls_resolver_and_routes_hwnd(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolver returns hwnd=42, focus attaches thread input, verifies, no raise."""
    _TARGET_HWND = 42

    mock_win32gui = MagicMock()
    mock_win32con = MagicMock()
    mock_win32process = MagicMock()
    mock_win32con.SW_RESTORE = 9
    mock_win32gui.IsIconic.return_value = False
    mock_win32gui.GetForegroundWindow.return_value = 99
    mock_win32process.GetWindowThreadProcessId.side_effect = lambda hwnd, *_: (
        (10, 0) if hwnd == 99 else (20, 12345)
    )

    monkeypatch.setattr(
        "voice_commander.tools.primitives.resolver.resolve_window",
        lambda target: _TARGET_HWND,
    )

    # primitives.focus delegates foreground-claim mechanics to _do_focus
    # (in _win32.py). Stub it to invoke the mocked win32gui calls the
    # assertions below verify, without running the real Alt-tap /
    # AttachThreadInput sequence.
    def _fake_do_focus(hwnd: int, _fg_tid: int, _target_tid: int) -> None:
        import win32gui as _wg  # resolves to mock_win32gui under the sys.modules patch

        _wg.BringWindowToTop(hwnd)
        _wg.SetForegroundWindow(hwnd)

    monkeypatch.setattr(
        "voice_commander.tools.primitives._do_focus",
        _fake_do_focus,
    )
    monkeypatch.setattr(
        "voice_commander.tools.primitives._verify_foreground",
        lambda hwnd: True,
    )

    with patch.dict(
        "sys.modules",
        {"win32gui": mock_win32gui, "win32con": mock_win32con, "win32process": mock_win32process},
    ):
        # Import the focus function from the module (avoid shadowing Python's focus).
        from voice_commander.tools.primitives import focus as _focus

        _focus("notepad")

    mock_win32gui.BringWindowToTop.assert_called_once_with(_TARGET_HWND)
    mock_win32gui.SetForegroundWindow.assert_called_once_with(_TARGET_HWND)


def test_focus_raises_on_resolver_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_target: str) -> int:
        raise FocusWindowError("no match")

    monkeypatch.setattr("voice_commander.tools.primitives.resolver.resolve_window", _raise)
    mock_win32gui = MagicMock()
    mock_win32con = MagicMock()
    mock_win32process = MagicMock()

    with patch.dict(
        "sys.modules",
        {"win32gui": mock_win32gui, "win32con": mock_win32con, "win32process": mock_win32process},
    ):
        from voice_commander.tools.primitives import focus as _focus

        with pytest.raises(FocusWindowError):
            _focus("zzz")


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


def test_open_uri_shortcut(monkeypatch: pytest.MonkeyPatch) -> None:
    """open('https://x.com') → resolver passes URI through; os.startfile called."""
    monkeypatch.setattr(
        "voice_commander.tools.primitives.resolver.resolve_app",
        lambda t: t,
    )
    # Neutralise the best-effort _verify_open so it returns quickly.
    monkeypatch.setattr("voice_commander.tools.primitives._verify_open", lambda _t: None)
    with patch("voice_commander.tools.primitives.os.startfile") as mock_startfile:
        open_target("https://x.com")
    mock_startfile.assert_called_once_with("https://x.com")


def test_open_calls_resolve_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """open('spotify') → resolver returns launch token → os.startfile(token)."""
    monkeypatch.setattr(
        "voice_commander.tools.primitives.resolver.resolve_app",
        lambda t: r"C:\fake\Spotify.lnk",
    )
    monkeypatch.setattr("voice_commander.tools.primitives._verify_open", lambda _t: None)
    with patch("voice_commander.tools.primitives.os.startfile") as mock_startfile:
        open_target("spotify")
    mock_startfile.assert_called_once_with(r"C:\fake\Spotify.lnk")


def test_open_blocks_cmd_resolved(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If resolver hands back cmd.exe, the blocklist kicks in and startfile is NOT called."""
    monkeypatch.setattr(
        "voice_commander.tools.primitives.resolver.resolve_app",
        lambda t: r"C:\Windows\System32\cmd.exe",
    )
    with (
        patch("voice_commander.tools.primitives.os.startfile") as mock_startfile,
        caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"),
    ):
        open_target("command prompt")
    mock_startfile.assert_not_called()
    assert "blocked" in caplog.text


def test_open_resolver_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_t: str) -> str:
        raise OpenResolveError("no app")

    monkeypatch.setattr("voice_commander.tools.primitives.resolver.resolve_app", _raise)
    with pytest.raises(OpenResolveError):
        open_target("zzz")


def test_open_startfile_oserror_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError from os.startfile propagates so dispatcher chimes on failure."""
    monkeypatch.setattr(
        "voice_commander.tools.primitives.resolver.resolve_app",
        lambda t: r"C:\fake\MyApp.lnk",
    )
    monkeypatch.setattr(
        "voice_commander.tools.primitives.os.startfile",
        MagicMock(side_effect=OSError("not found")),
    )
    with pytest.raises(OSError, match="not found"):
        open_target("nonexistent_app")


def test_open_self_verify_best_effort_logs_on_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """_verify_open timeout → WARNING logged, but open_target does not raise."""
    monkeypatch.setattr(
        "voice_commander.tools.primitives.resolver.resolve_app",
        lambda t: r"C:\fake\MyApp.lnk",
    )
    # Force the verify to timeout by making EnumWindows find nothing matching.
    mock_win32gui = MagicMock()
    mock_win32gui.IsWindowVisible.return_value = True
    mock_win32gui.GetWindowText.return_value = "Unrelated Window"
    mock_win32gui.EnumWindows.side_effect = lambda cb, _: cb(1, None)

    # Short-circuit the verify loop to only run once, then log the warning.
    monkeypatch.setattr("voice_commander.tools.primitives._OPEN_VERIFY_TIMEOUT_MS", 10)
    monkeypatch.setattr("voice_commander.tools.primitives._OPEN_VERIFY_POLL_INTERVAL_MS", 5)

    with (
        patch.dict("sys.modules", {"win32gui": mock_win32gui}),
        patch("voice_commander.tools.primitives.os.startfile"),
        caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"),
    ):
        open_target("myapp")

    assert "verify timeout" in caplog.text or "timeout" in caplog.text


# ---------------------------------------------------------------------------
# click
# ---------------------------------------------------------------------------


def test_click_default_left() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.click") as mock_click:
        click()
    mock_click.assert_called_once_with(button="left")


@pytest.mark.parametrize("button", ["right", "middle"])
def test_click_accepts_right_and_middle(button: str) -> None:
    with patch("voice_commander.tools.primitives.pyautogui.click") as mock_click:
        click(button=button)
    mock_click.assert_called_once_with(button=button)


def test_click_rejects_unknown_button(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("voice_commander.tools.primitives.pyautogui.click") as mock_click,
        caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"),
    ):
        click(button="top")
    mock_click.assert_not_called()
    assert "unknown button" in caplog.text


# ---------------------------------------------------------------------------
# scroll
# ---------------------------------------------------------------------------


def test_scroll_up() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.scroll") as mock_scroll:
        scroll("up", amount=5)
    mock_scroll.assert_called_once_with(5)


def test_scroll_down() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.scroll") as mock_scroll:
        scroll("down", amount=3)
    mock_scroll.assert_called_once_with(-3)


def test_scroll_unknown_direction_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("voice_commander.tools.primitives.pyautogui.scroll") as mock_scroll,
        caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"),
    ):
        scroll("sideways")
    mock_scroll.assert_not_called()
    assert "unknown direction" in caplog.text


# ---------------------------------------------------------------------------
# no_match
# ---------------------------------------------------------------------------


def test_no_match_is_noop() -> None:
    assert no_match("casual chit-chat") is None


# ---------------------------------------------------------------------------
# Sanity: OS-level fallback keeps the suite import-clean even on non-Windows
# ---------------------------------------------------------------------------


def test_imports_expose_expected_symbols() -> None:
    from voice_commander.tools import primitives as _p

    for name in (
        "click",
        "focus",
        "no_match",
        "open_target",
        "press",
        "scroll",
        "type_text",
        "wait",
    ):
        assert hasattr(_p, name), f"primitives missing symbol {name!r}"


def test_focus_returns_hwnd(monkeypatch):
    """focus() should return the target hwnd as int."""
    import sys
    import types

    from voice_commander.tools import primitives as P

    fake_hwnd = 0x1234

    monkeypatch.setattr(P.resolver, "resolve_window", lambda target: fake_hwnd)

    fake_win32gui = types.SimpleNamespace(
        IsIconic=lambda h: False,
        ShowWindow=lambda h, c: None,
        GetForegroundWindow=lambda: fake_hwnd,
        SetForegroundWindow=lambda h: None,
        BringWindowToTop=lambda h: None,
    )
    fake_win32process = types.SimpleNamespace(GetWindowThreadProcessId=lambda h: (0, 0))
    fake_win32con = types.SimpleNamespace(SW_RESTORE=9)
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_win32process)
    monkeypatch.setitem(sys.modules, "win32con", fake_win32con)

    result = P.focus("comet")
    assert result == fake_hwnd
