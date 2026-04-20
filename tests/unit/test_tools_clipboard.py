from unittest.mock import patch
from voice_commander.tools import clipboard


def test_copy_sends_ctrl_c():
    with patch("voice_commander.tools.clipboard.pyautogui.hotkey") as hk:
        clipboard.copy()
    hk.assert_called_once_with("ctrl", "c")


def test_paste_sends_ctrl_v():
    with patch("voice_commander.tools.clipboard.pyautogui.hotkey") as hk:
        clipboard.paste()
    hk.assert_called_once_with("ctrl", "v")


def test_cut_sends_ctrl_x():
    with patch("voice_commander.tools.clipboard.pyautogui.hotkey") as hk:
        clipboard.cut()
    hk.assert_called_once_with("ctrl", "x")


def test_select_all_sends_ctrl_a():
    with patch("voice_commander.tools.clipboard.pyautogui.hotkey") as hk:
        clipboard.select_all()
    hk.assert_called_once_with("ctrl", "a")
