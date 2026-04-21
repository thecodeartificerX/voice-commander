from unittest.mock import patch

from voice_commander.tools import mouse


def test_click_left_clicks():
    with patch("voice_commander.tools.mouse.pyautogui.click") as c:
        mouse.click()
    c.assert_called_once_with()


def test_right_click_calls_right_click():
    with patch("voice_commander.tools.mouse.pyautogui.rightClick") as rc:
        mouse.right_click()
    rc.assert_called_once_with()
