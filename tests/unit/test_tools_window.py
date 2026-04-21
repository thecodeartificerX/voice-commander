from unittest.mock import patch
from voice_commander.tools import window


def test_minimize():
    with patch("voice_commander.tools.window.pyautogui.hotkey") as hk:
        window.minimize()
    hk.assert_called_once_with("win", "down")


def test_maximize():
    with patch("voice_commander.tools.window.pyautogui.hotkey") as hk:
        window.maximize()
    hk.assert_called_once_with("win", "up")


def test_focus_browser_calls_helper():
    with patch("voice_commander.tools.window._focus_comet") as f:
        window.focus_browser()
    f.assert_called_once()


def test_focus_terminal_calls_helper():
    with patch("voice_commander.tools.window._focus_terminal") as f:
        window.focus_terminal()
    f.assert_called_once()
