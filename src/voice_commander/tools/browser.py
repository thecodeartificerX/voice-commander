from __future__ import annotations

import subprocess

import pyautogui

from ..registry import tool
from ._win32 import COMET_LAUNCH_PATH

GMAIL_URL = "https://mail.google.com"
MESSENGER_URL = "https://fb.com/messages"


@tool
def new_tab() -> None:
    pyautogui.hotkey("ctrl", "t")


@tool
def close_tab() -> None:
    pyautogui.hotkey("ctrl", "w")


@tool
def reopen_tab() -> None:
    pyautogui.hotkey("ctrl", "shift", "t")


@tool
def reload() -> None:
    pyautogui.press("f5")


@tool
def email() -> None:
    """Open Gmail in Comet."""
    subprocess.Popen([str(COMET_LAUNCH_PATH), GMAIL_URL])


@tool
def messenger() -> None:
    """Open Facebook Messenger in Comet."""
    subprocess.Popen([str(COMET_LAUNCH_PATH), MESSENGER_URL])
