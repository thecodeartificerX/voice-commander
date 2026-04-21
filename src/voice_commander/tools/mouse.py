from __future__ import annotations

import pyautogui

from ..registry import tool


@tool
def click() -> None:
    """Left-click at the current cursor position."""
    pyautogui.click()


@tool
def right_click() -> None:
    """Right-click at the current cursor position."""
    pyautogui.rightClick()
