"""LLM-only primitive tools for the hybrid router."""

from __future__ import annotations

import logging
import os
import re
import time

import pyautogui

from ..registry import tool
from ._win32 import (
    FocusWindowError,
    _allow_set_foreground,
    _attach_thread_input,
    _verify_foreground,
)

logger = logging.getLogger(__name__)

_LAUNCH_BLOCKLIST = re.compile(
    r"(\\\\|[A-Za-z]:\\|/|cmd|powershell|wscript|cscript)", re.IGNORECASE
)
_MAX_TYPE_TEXT_LEN = 500


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
    if len(text) > _MAX_TYPE_TEXT_LEN:
        logger.warning("type_text truncated: %d chars > %d max", len(text), _MAX_TYPE_TEXT_LEN)
        text = text[:_MAX_TYPE_TEXT_LEN]
    pyautogui.write(text, interval=0.02)


@tool
def focus_window(title_substring: str) -> None:
    """Focus a window whose title contains the given substring (case-insensitive).

    Uses the AttachThreadInput workaround for Windows 11 foreground-lockout
    protection, then verifies the focus change via polling GetForegroundWindow.

    Raises
    ------
    FocusWindowError
        If no matching window is found, or if the focus attempt fails
        verification. Propagated to Dispatcher so the plan chain is halted
        instead of sending keystrokes to the wrong window.
    """
    try:
        import win32con
        import win32gui
        import win32process
    except ImportError as exc:
        logger.warning("pywin32 not available, cannot focus window")
        raise FocusWindowError("pywin32 not available; cannot focus window by title") from exc

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
        logger.error("focus_window: no visible window matching title_substring=%r", title_substring)
        raise FocusWindowError(f"No window matching '{title_substring}'")

    # Restore if minimized.
    if win32gui.IsIconic(target_hwnd):
        win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)

    # Obtain thread IDs for AttachThreadInput.
    fg_hwnd = win32gui.GetForegroundWindow()
    if fg_hwnd:
        foreground_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
    else:
        foreground_tid = 0
    target_tid, _ = win32process.GetWindowThreadProcessId(target_hwnd)

    _allow_set_foreground()

    same_thread = foreground_tid == target_tid or foreground_tid == 0
    if not same_thread:
        _attach_thread_input(foreground_tid, target_tid, True)
    try:
        win32gui.BringWindowToTop(target_hwnd)
        win32gui.SetForegroundWindow(target_hwnd)
    except Exception as exc:
        raise FocusWindowError(
            f"SetForegroundWindow failed for title_substring={title_substring!r} "
            f"hwnd={target_hwnd}: {exc}"
        ) from exc
    finally:
        if not same_thread:
            _attach_thread_input(foreground_tid, target_tid, False)

    if not _verify_foreground(target_hwnd):
        raise FocusWindowError(
            f"Focus verification failed for title_substring={title_substring!r} "
            f"hwnd={target_hwnd} (GetForegroundWindow did not match after 60 ms)"
        )


@tool
def launch(app: str) -> None:
    """Launch an application, file, or URI via the system handler."""
    if _LAUNCH_BLOCKLIST.search(app):
        logger.warning("launch blocked suspicious input: %r", app)
        return
    try:
        os.startfile(app)
    except OSError:
        logger.exception("launch() failed for app=%r", app)


@tool
def no_match(reason: str) -> None:
    """Escape hatch: LLM signals no tool fits the utterance."""
    # Body is a no-op; the router intercepts no_match before dispatch.
    pass
