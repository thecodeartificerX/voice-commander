from __future__ import annotations

import pyautogui

from ..registry import tool


@tool(phrases=["lock screen", "lock the screen", "lock computer"])
def lock_screen() -> None:
    pyautogui.hotkey("win", "l")


@tool(phrases=["take screenshot", "screenshot", "capture screen", "snip"])
def take_screenshot() -> None:
    pyautogui.hotkey("win", "shift", "s")
