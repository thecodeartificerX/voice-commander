from unittest.mock import patch

from voice_commander.tools import browser


def _test_hotkey(fn, keys):
    with patch("voice_commander.tools.browser.pyautogui.hotkey") as hk:
        fn()
    hk.assert_called_once_with(*keys)


def test_new_tab():
    _test_hotkey(browser.new_tab, ("ctrl", "t"))


def test_close_tab():
    _test_hotkey(browser.close_tab, ("ctrl", "w"))


def test_reopen_tab():
    _test_hotkey(browser.reopen_tab, ("ctrl", "shift", "t"))


def test_reload_sends_f5():
    with patch("voice_commander.tools.browser.pyautogui.press") as pr:
        browser.reload()
    pr.assert_called_once_with("f5")


def test_email_launches_comet_with_gmail():
    with patch("voice_commander.tools.browser.subprocess.Popen") as popen:
        browser.email()
    popen.assert_called_once_with([str(browser.COMET_LAUNCH_PATH), browser.GMAIL_URL])


def test_messenger_launches_comet_with_messages_url():
    with patch("voice_commander.tools.browser.subprocess.Popen") as popen:
        browser.messenger()
    popen.assert_called_once_with([str(browser.COMET_LAUNCH_PATH), browser.MESSENGER_URL])
