from __future__ import annotations

import pyautogui

from ..registry import tool


@tool
def lock_screen() -> None:
    pyautogui.hotkey("win", "l")


@tool
def take_screenshot() -> None:
    pyautogui.hotkey("win", "shift", "s")
