from unittest.mock import MagicMock, patch

import pytest

from voice_commander.tools.primitives import (
    focus_window,
    launch,
    no_match,
    press_keys,
    type_text,
    wait,
)


def test_wait():
    with patch("voice_commander.tools.primitives.time.sleep") as mock_sleep:
        wait(500)
    mock_sleep.assert_called_once_with(0.5)


def test_press_keys_ctrl_c():
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press_keys("ctrl+c")
    mock_hotkey.assert_called_once_with("ctrl", "c")


def test_press_keys_alt_tab():
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press_keys("alt+tab")
    mock_hotkey.assert_called_once_with("alt", "tab")


def test_press_keys_strips_whitespace():
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press_keys("ctrl + shift + n")
    mock_hotkey.assert_called_once_with("ctrl", "shift", "n")


def test_type_text():
    with patch("voice_commander.tools.primitives.pyautogui.write") as mock_write:
        type_text("hello")
    mock_write.assert_called_once_with("hello", interval=0.02)


def test_focus_window_matching_window(monkeypatch):
    """EnumWindows finds a visible matching window; AttachThreadInput path runs; no raise."""
    mock_win32gui = MagicMock()
    mock_win32con = MagicMock()
    mock_win32process = MagicMock()
    mock_win32con.SW_RESTORE = 9

    _TARGET_HWND = 42
    _FG_HWND = 99
    _FG_TID = 10
    _TARGET_TID = 20

    # IsWindowVisible returns True; GetWindowText returns a matching title.
    mock_win32gui.IsWindowVisible.return_value = True
    mock_win32gui.GetWindowText.return_value = "My Notepad Window"
    mock_win32gui.IsIconic.return_value = False

    # First call to GetForegroundWindow → fg_hwnd; subsequent calls (verification) → target hwnd.
    _gfw_calls: list[int] = []

    def _gfw():
        if not _gfw_calls:
            _gfw_calls.append(1)
            return _FG_HWND
        _gfw_calls.append(1)
        return _TARGET_HWND  # verification succeeds immediately

    mock_win32gui.GetForegroundWindow.side_effect = _gfw

    # GetWindowThreadProcessId: (tid, pid)
    mock_win32process.GetWindowThreadProcessId.side_effect = lambda hwnd, *_: (
        (_FG_TID, 0) if hwnd == _FG_HWND else (_TARGET_TID, 12345)
    )

    # Simulate EnumWindows by calling the callback immediately with hwnd=42.
    def fake_enum_windows(callback, extra):
        callback(_TARGET_HWND, None)

    mock_win32gui.EnumWindows.side_effect = fake_enum_windows

    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", lambda *a: None)
    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", lambda: None)

    with patch.dict(
        "sys.modules",
        {"win32gui": mock_win32gui, "win32con": mock_win32con, "win32process": mock_win32process},
    ):
        focus_window("notepad")

    mock_win32gui.BringWindowToTop.assert_called_once_with(_TARGET_HWND)
    mock_win32gui.SetForegroundWindow.assert_called_once_with(_TARGET_HWND)


def test_focus_window_no_matching_window(caplog):
    """EnumWindows finds no match; FocusWindowError is raised and logged."""
    import logging

    from voice_commander.tools._win32 import FocusWindowError

    mock_win32gui = MagicMock()
    mock_win32con = MagicMock()
    mock_win32process = MagicMock()

    mock_win32gui.IsWindowVisible.return_value = True
    mock_win32gui.GetWindowText.return_value = "Some Unrelated Window"
    mock_win32gui.GetForegroundWindow.return_value = 99

    def fake_enum_windows(callback, extra):
        callback(99, None)

    mock_win32gui.EnumWindows.side_effect = fake_enum_windows

    with (
        patch.dict(
            "sys.modules",
            {
                "win32gui": mock_win32gui,
                "win32con": mock_win32con,
                "win32process": mock_win32process,
            },
        ),
        caplog.at_level(logging.ERROR, logger="voice_commander.tools.primitives"),
        pytest.raises(FocusWindowError),
    ):
        focus_window("notepad")

    mock_win32gui.SetForegroundWindow.assert_not_called()
    assert "notepad" in caplog.text


def test_focus_window_import_error_fallback(caplog):
    """When pywin32 is missing, focus_window logs a warning and raises FocusWindowError."""
    import logging

    from voice_commander.tools._win32 import FocusWindowError

    with (
        patch.dict("sys.modules", {"win32gui": None, "win32con": None, "win32process": None}),
        caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"),
        pytest.raises(FocusWindowError),
    ):
        focus_window("anything")

    assert "pywin32" in caplog.text


def test_launch():
    with patch("voice_commander.tools.primitives.os.startfile") as mock_startfile:
        launch("notepad.exe")
    mock_startfile.assert_called_once_with("notepad.exe")


def test_no_match_noop():
    result = no_match("nothing matched the utterance")
    assert result is None


def test_launch_blocks_unc_path(caplog):
    """UNC paths are blocked by the launch blocklist."""
    import logging

    with caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"):
        launch("\\\\evil-server\\share\\payload.exe")

    assert "blocked" in caplog.text


def test_launch_blocks_cmd(caplog):
    """cmd.exe invocations are blocked."""
    import logging

    with caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"):
        launch("cmd.exe /c del *")

    assert "blocked" in caplog.text


def test_launch_blocks_absolute_path(caplog):
    """Absolute Windows paths are blocked."""
    import logging

    with caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"):
        launch("C:\\Windows\\System32\\cmd.exe")

    assert "blocked" in caplog.text


def test_launch_allows_app_name():
    """Simple app names like 'notepad.exe' are allowed."""
    with patch("voice_commander.tools.primitives.os.startfile") as mock_startfile:
        launch("notepad.exe")
    mock_startfile.assert_called_once_with("notepad.exe")


def test_launch_oserror_propagates():
    """OSError from startfile propagates to caller (dispatcher handles it)."""
    with (
        patch("voice_commander.tools.primitives.os.startfile", side_effect=OSError("not found")),
        pytest.raises(OSError, match="not found"),
    ):
        launch("nonexistent_app")


def test_type_text_truncates_long_input():
    """Text longer than 500 chars is truncated."""
    with patch("voice_commander.tools.primitives.pyautogui.write") as mock_write:
        type_text("a" * 600)
    # Should be called with truncated text
    args = mock_write.call_args[0]
    assert len(args[0]) == 500


def test_type_text_short_input_passes_through():
    """Text shorter than limit passes through unchanged."""
    with patch("voice_commander.tools.primitives.pyautogui.write") as mock_write:
        type_text("hello")
    mock_write.assert_called_once_with("hello", interval=0.02)
