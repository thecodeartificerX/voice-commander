from __future__ import annotations

import pyautogui

from ..registry import tool


@tool
def copy() -> None:
    """Sends Ctrl+C to copy the current selection."""
    pyautogui.hotkey("ctrl", "c")


@tool
def paste() -> None:
    """Ctrl+V."""
    pyautogui.hotkey("ctrl", "v")


@tool
def cut() -> None:
    """Ctrl+X."""
    pyautogui.hotkey("ctrl", "x")


@tool
def select_all() -> None:
    """Ctrl+A."""
    pyautogui.hotkey("ctrl", "a")
