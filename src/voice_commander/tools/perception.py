"""Perception tools — observation primitives for graph branch nodes.

Three side-effect-free verbs that let GraphRuntime branch nodes observe the
desktop without focusing windows or sending keystrokes. All three are
``llm_only = true`` with ``phrases = []``.
"""

from __future__ import annotations

import contextlib
import logging

from ..registry import tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# read_clipboard
# ---------------------------------------------------------------------------


@tool
def read_clipboard() -> str:
    """Return the current Windows clipboard text. Empty string if no text is available."""
    try:
        import win32clipboard
        import win32con
    except ImportError:
        logger.warning("read_clipboard: win32clipboard not available")
        return ""
    try:
        win32clipboard.OpenClipboard()
    except Exception:
        logger.debug("read_clipboard: OpenClipboard failed", exc_info=True)
        return ""
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return ""
        data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return str(data) if data else ""
    finally:
        with contextlib.suppress(Exception):
            win32clipboard.CloseClipboard()


# ---------------------------------------------------------------------------
# get_active_window_title
# ---------------------------------------------------------------------------


@tool
def get_active_window_title() -> str:
    """Return the title of the currently focused window, or empty string if none."""
    try:
        import win32gui
    except ImportError:
        logger.warning("get_active_window_title: pywin32 not available")
        return ""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return ""
    return str(win32gui.GetWindowText(hwnd) or "")


# ---------------------------------------------------------------------------
# get_cursor_pos
# ---------------------------------------------------------------------------


@tool
def get_cursor_pos() -> tuple[int, int]:
    """Return the current cursor position as (x, y) screen coordinates."""
    try:
        import win32api
    except ImportError:
        logger.warning("get_cursor_pos: pywin32 not available")
        return (0, 0)
    return tuple(win32api.GetCursorPos())
