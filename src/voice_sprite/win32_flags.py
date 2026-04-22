"""Apply Win32 extended window styles for click-through, topmost, no-taskbar sprite."""

from __future__ import annotations

import ctypes
import logging

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000  # Enables per-pixel alpha / transparency
WS_EX_TRANSPARENT = 0x00000020  # Click-through (mouse events pass to window below)
WS_EX_TOOLWINDOW = 0x00000080  # Excluded from taskbar and Alt+Tab
WS_EX_NOACTIVATE = 0x08000000  # Never receives input focus
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
LWA_ALPHA = 0x00000002


def apply_click_through(hwnd: int) -> None:
    """Make a window click-through, always-on-top, no taskbar, no focus steal."""
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
        logger.info("Applied click-through flags to hwnd=%d", hwnd)
    except OSError:
        logger.exception("Failed to apply Win32 flags to hwnd=%d", hwnd)
