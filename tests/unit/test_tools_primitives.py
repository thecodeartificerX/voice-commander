from unittest.mock import MagicMock, patch

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


def test_focus_window_matching_window():
    """EnumWindows finds a visible matching window; SetForegroundWindow is called."""
    mock_win32gui = MagicMock()
    mock_win32con = MagicMock()
    mock_win32con.SW_RESTORE = 9

    # IsWindowVisible returns True, GetWindowText returns a matching title.
    mock_win32gui.IsWindowVisible.return_value = True
    mock_win32gui.GetWindowText.return_value = "My Notepad Window"

    # Simulate EnumWindows by calling the callback immediately with hwnd=42.
    def fake_enum_windows(callback, extra):
        callback(42, None)

    mock_win32gui.EnumWindows.side_effect = fake_enum_windows

    with patch.dict(
        "sys.modules",
        {"win32gui": mock_win32gui, "win32con": mock_win32con},
    ):
        focus_window("notepad")

    mock_win32gui.ShowWindow.assert_called_once_with(42, mock_win32con.SW_RESTORE)
    mock_win32gui.SetForegroundWindow.assert_called_once_with(42)


def test_focus_window_no_matching_window(caplog):
    """EnumWindows finds no match; SetForegroundWindow is never called."""
    import logging

    mock_win32gui = MagicMock()
    mock_win32con = MagicMock()

    mock_win32gui.IsWindowVisible.return_value = True
    mock_win32gui.GetWindowText.return_value = "Some Unrelated Window"

    def fake_enum_windows(callback, extra):
        callback(99, None)

    mock_win32gui.EnumWindows.side_effect = fake_enum_windows

    with patch.dict(
        "sys.modules",
        {"win32gui": mock_win32gui, "win32con": mock_win32con},
    ), caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"):
        focus_window("notepad")

    mock_win32gui.SetForegroundWindow.assert_not_called()
    assert "notepad" in caplog.text


def test_focus_window_import_error_fallback(caplog):
    """When pywin32 is missing, focus_window logs a warning and returns cleanly."""
    import logging

    with (
        patch.dict("sys.modules", {"win32gui": None, "win32con": None}),
        caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"),
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


def test_launch_oserror_handled(caplog):
    """OSError from startfile is caught and logged."""
    import logging

    with (
        patch("voice_commander.tools.primitives.os.startfile", side_effect=OSError("not found")),
        caplog.at_level(logging.ERROR, logger="voice_commander.tools.primitives"),
    ):
        launch("nonexistent_app")

    assert "launch() failed" in caplog.text


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
