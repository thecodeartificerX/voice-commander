from unittest.mock import patch

from voice_commander.tools import system


def test_lock_screen_sends_win_l():
    with patch("voice_commander.tools.system.pyautogui.hotkey") as hk:
        system.lock_screen()
    hk.assert_called_once_with("win", "l")


def test_take_screenshot_sends_win_shift_s():
    with patch("voice_commander.tools.system.pyautogui.hotkey") as hk:
        system.take_screenshot()
    hk.assert_called_once_with("win", "shift", "s")


def test_cancel_presses_escape():
    with patch("voice_commander.tools.system.pyautogui.press") as pr:
        system.cancel()
    pr.assert_called_once_with("esc")
