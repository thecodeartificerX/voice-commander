from __future__ import annotations

import pyautogui

from ..registry import tool


@tool(phrases=["new tab", "open new tab"])
def new_tab() -> None:
    pyautogui.hotkey("ctrl", "t")


@tool(phrases=["close tab", "close this tab"])
def close_tab() -> None:
    pyautogui.hotkey("ctrl", "w")


@tool(phrases=["reopen tab", "reopen last tab", "bring back tab"])
def reopen_tab() -> None:
    pyautogui.hotkey("ctrl", "shift", "t")


@tool(phrases=["reload", "refresh", "reload page"])
def reload() -> None:
    pyautogui.press("f5")
