"""LLM-only primitive tools for the hybrid router."""
from __future__ import annotations

import logging
import os
import time

import pyautogui

from ..registry import tool

logger = logging.getLogger(__name__)


@tool
def wait(ms: int) -> None:
    """Pause execution for the specified milliseconds."""
    time.sleep(ms / 1000.0)


@tool
def press_keys(combo: str) -> None:
    """Press a key combination like 'ctrl+c', 'alt+tab', 'win+l'."""
    keys = [k.strip() for k in combo.split("+")]
    pyautogui.hotkey(*keys)


@tool
def type_text(text: str) -> None:
    """Type arbitrary text via keystroke synthesis."""
    pyautogui.write(text, interval=0.02)


@tool
def focus_window(title_substring: str) -> None:
    """Focus a window whose title contains the given substring (case-insensitive)."""
    try:
        import win32con
        import win32gui
    except ImportError:
        logger.warning("pywin32 not available, cannot focus window")
        return

    target_hwnd: int | None = None

    def _enum(hwnd: int, _: object) -> bool:
        nonlocal target_hwnd
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if title_substring.lower() in title.lower():
            target_hwnd = hwnd
            return False  # stop enumeration
        return True

    win32gui.EnumWindows(_enum, None)
    if target_hwnd is None:
        logger.warning("No window matching '%s'", title_substring)
        return
    try:
        win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(target_hwnd)
    except Exception:
        logger.exception("SetForegroundWindow failed for '%s'", title_substring)


@tool
def launch(app: str) -> None:
    """Launch an application, file, or URI via the system handler."""
    os.startfile(app)


@tool
def no_match(reason: str) -> None:
    """Escape hatch: LLM signals no tool fits the utterance."""
    # Body is a no-op; the router intercepts no_match before dispatch.
    pass
