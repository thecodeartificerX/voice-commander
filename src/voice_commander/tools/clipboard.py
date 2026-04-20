from __future__ import annotations

import pyautogui

from ..registry import tool


@tool(phrases=["copy", "copy that", "copy selection"])
def copy() -> None:
    """Sends Ctrl+C to copy the current selection."""
    pyautogui.hotkey("ctrl", "c")


@tool(phrases=["paste", "paste it", "paste here"])
def paste() -> None:
    """Ctrl+V."""
    pyautogui.hotkey("ctrl", "v")


@tool(phrases=["cut", "cut selection", "cut that"])
def cut() -> None:
    """Ctrl+X."""
    pyautogui.hotkey("ctrl", "x")


@tool(phrases=["select all", "select everything"])
def select_all() -> None:
    """Ctrl+A."""
    pyautogui.hotkey("ctrl", "a")
