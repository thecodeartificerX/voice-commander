"""Windows clipboard round-trip for dictation paste.

The dictation result is pasted by: snapshot the current clipboard, set it to
the transcription, send Ctrl+V, then restore the snapshot. With Windows
clipboard history (Win+V) enabled this leaves the user's original entry on top
and the transcription as the second history entry.
"""

from __future__ import annotations

import logging
import time

import pyautogui
import win32clipboard
import win32con

logger = logging.getLogger(__name__)

# OpenClipboard fails if another process holds the clipboard; retry briefly.
_OPEN_RETRIES = 6
_OPEN_RETRY_DELAY_S = 0.05


def _open_clipboard() -> None:
    last_err: Exception | None = None
    for _ in range(_OPEN_RETRIES):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception as e:  # pywintypes.error
            last_err = e
            time.sleep(_OPEN_RETRY_DELAY_S)
    raise RuntimeError(f"could not open clipboard: {last_err}")


def read_clipboard_text() -> str | None:
    """Return the clipboard's text contents, or None if it holds no text."""
    _open_clipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT))
        return None
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    """Replace the clipboard contents with *text*."""
    _open_clipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def send_paste() -> None:
    """Send Ctrl+V to the foreground window."""
    pyautogui.hotkey("ctrl", "v")


def paste_via_clipboard(text: str, settle_ms: int = 200) -> None:
    """Set clipboard to *text*, paste into the foreground window, restore original.

    *settle_ms* is the pause after the clipboard set (before paste) and after
    the paste (before restore) — Windows clipboard ops are not instant.
    """
    original = read_clipboard_text()
    set_clipboard_text(text)
    try:
        time.sleep(settle_ms / 1000.0)
        send_paste()
        time.sleep(settle_ms / 1000.0)
    finally:
        if original is not None:
            set_clipboard_text(original)
        else:
            logger.warning(
                "dictation: original clipboard held no text and was not restored"
            )
