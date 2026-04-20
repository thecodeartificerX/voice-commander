from __future__ import annotations

import pyautogui

from ..registry import tool


@tool(phrases=["copy", "copy that", "copy selection"])
def copy() -> None:
    """Sends Ctrl+C to copy the current selection."""
    pyautogui.hotkey("ctrl", "c")
