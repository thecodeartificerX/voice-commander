from __future__ import annotations

import pyautogui

from ..registry import tool


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
