from unittest.mock import patch
from voice_commander.tools import clipboard


def test_copy_sends_ctrl_c():
    with patch("voice_commander.tools.clipboard.pyautogui.hotkey") as hk:
        clipboard.copy()
    hk.assert_called_once_with("ctrl", "c")
