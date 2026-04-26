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
    close,
    close_window,
    last,
    mute,
    no_match,
    open_target,
    press,
    scroll,
    summon_commander,
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
# close / close_window
# ---------------------------------------------------------------------------


def test_close_sends_ctrl_w(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_win32gui = MagicMock()
    # GetForegroundWindow: first returns 11 (before), then 22 (after) so verify passes.
    mock_win32gui.GetForegroundWindow.side_effect = [11, 22]
    mock_win32gui.IsWindow.return_value = True

    with (
        patch.dict("sys.modules", {"win32gui": mock_win32gui}),
        patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey,
    ):
        close()
    mock_hotkey.assert_called_once_with("ctrl", "w")


def test_close_window_sends_alt_f4(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_win32gui = MagicMock()
    mock_win32gui.GetForegroundWindow.side_effect = [11, 22]
    mock_win32gui.IsWindow.return_value = True

    with (
        patch.dict("sys.modules", {"win32gui": mock_win32gui}),
        patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey,
    ):
        close_window()
    mock_hotkey.assert_called_once_with("alt", "f4")


def test_close_self_verify_logs_when_fg_unchanged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """GetForegroundWindow returns same hwnd before and after → WARNING, no raise."""
    mock_win32gui = MagicMock()
    mock_win32gui.GetForegroundWindow.return_value = 77  # unchanged
    mock_win32gui.IsWindow.return_value = True

    monkeypatch.setattr("voice_commander.tools.primitives._CLOSE_VERIFY_TIMEOUT_MS", 10)
    monkeypatch.setattr("voice_commander.tools.primitives._CLOSE_VERIFY_POLL_INTERVAL_MS", 5)

    with (
        patch.dict("sys.modules", {"win32gui": mock_win32gui}),
        patch("voice_commander.tools.primitives.pyautogui.hotkey"),
        caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"),
    ):
        close()
    assert "verify timeout" in caplog.text


# ---------------------------------------------------------------------------
# last
# ---------------------------------------------------------------------------


def test_last_default_sends_alt_tab() -> None:
    """Bare last() → Alt+Tab; _close_with_verify snapshots fg and polls for change."""
    mock_win32gui = MagicMock()
    # 3 calls: before_hwnd in _close_with_verify, current_hwnd poll, return value fetch
    mock_win32gui.GetForegroundWindow.side_effect = [11, 22, 22]
    mock_win32gui.IsWindow.return_value = True

    with (
        patch.dict("sys.modules", {"win32gui": mock_win32gui}),
        patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey,
    ):
        last()
    mock_hotkey.assert_called_once_with("alt", "tab")


def test_last_tab_true_sends_ctrl_tab() -> None:
    """last(tab=True) → Ctrl+Tab, no self-verify (no fg change expected)."""
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        last(tab=True)
    mock_hotkey.assert_called_once_with("ctrl", "tab")


def test_last_default_verify_logs_when_fg_unchanged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Alt+Tab that leaves fg hwnd unchanged → WARNING, no raise."""
    mock_win32gui = MagicMock()
    mock_win32gui.GetForegroundWindow.return_value = 77  # unchanged
    mock_win32gui.IsWindow.return_value = True

    monkeypatch.setattr("voice_commander.tools.primitives._CLOSE_VERIFY_TIMEOUT_MS", 10)
    monkeypatch.setattr("voice_commander.tools.primitives._CLOSE_VERIFY_POLL_INTERVAL_MS", 5)

    with (
        patch.dict("sys.modules", {"win32gui": mock_win32gui}),
        patch("voice_commander.tools.primitives.pyautogui.hotkey"),
        caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"),
    ):
        last()
    assert "verify timeout" in caplog.text
    assert "last" in caplog.text


def test_last_signature() -> None:
    import inspect

    from voice_commander.tools import primitives

    sig = inspect.signature(primitives.last)
    assert "tab" in sig.parameters
    assert sig.parameters["tab"].annotation == "bool"
    assert sig.parameters["tab"].default is False


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
# summon_commander
# ---------------------------------------------------------------------------


def test_summon_commander_spawns_powershell_in_repo() -> None:
    import subprocess as _subprocess

    with patch("subprocess.Popen") as mock_popen:
        summon_commander()

    mock_popen.assert_called_once_with(
        ["pwsh.exe", "-NoExit", "-Command", "ccd"],
        cwd=r"F:\Tools\Projects\voice-commander",
        creationflags=_subprocess.CREATE_NEW_CONSOLE,
    )


def test_summon_commander_takes_no_args() -> None:
    import inspect

    sig = inspect.signature(summon_commander)
    assert len(sig.parameters) == 0


# ---------------------------------------------------------------------------
# no_match
# ---------------------------------------------------------------------------


def test_no_match_is_noop() -> None:
    assert no_match("casual chit-chat") is None


# ---------------------------------------------------------------------------
# mute
# ---------------------------------------------------------------------------


def test_mute_invokes_injected_callback() -> None:
    from voice_commander.tools import primitives as _p

    fake = MagicMock()
    _p._set_mute_callback(fake)
    try:
        mute()
    finally:
        _p._set_mute_callback(None)
    fake.assert_called_once_with()


def test_mute_noop_when_callback_unset(caplog: pytest.LogCaptureFixture) -> None:
    from voice_commander.tools import primitives as _p

    _p._set_mute_callback(None)
    with caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"):
        mute()
    assert any("no daemon callback wired" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Sanity: OS-level fallback keeps the suite import-clean even on non-Windows
# ---------------------------------------------------------------------------


def test_imports_expose_expected_symbols() -> None:
    from voice_commander.tools import primitives as _p

    # Each verb present with the Python symbol declared in the module.
    for name in (
        "click",
        "close",
        "close_window",
        "last",
        "mute",
        "no_match",
        "open_target",
        "press",
        "scroll",
        "summon_commander",
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
