"""LLM-only primitive verbs — the 9-tool catalog the LLM chains (spec Section 2).

This module is the sole source of LLM-visible tools. Every verb here is
``llm_only = true`` with ``phrases = []``. Names are chosen to be short for
minimal prefill: ``focus``, ``type``, ``open``, ``close``, ``close_window``,
``press``, ``wait``, ``click``, ``no_match`` (plus ``scroll`` as a bonus).

Two verbs shadow Python builtins — ``type`` and ``open``. Their Python symbols
are ``type_text`` and ``open_target``; the registry exposes them under the
short LLM-visible names via ``@tool(name=...)``.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, cast

import pyautogui

from .. import resolver
from ..registry import tool
from ._win32 import (
    FocusWindowError,
    _allow_set_foreground,
    _attach_thread_input,
    _verify_foreground,
)

logger = logging.getLogger(__name__)

_MAX_TYPE_TEXT_LEN = 500

# Applied to the RESOLVED launch token (post-resolver.resolve_app), not the raw
# utterance. Guards against the LLM emitting a raw interpreter invocation.
_LAUNCH_BLOCKLIST = {"cmd", "cmd.exe", "powershell", "powershell.exe",
                     "pwsh", "pwsh.exe", "wscript", "wscript.exe",
                     "cscript", "cscript.exe"}

# How long to poll EnumWindows after an open() before giving up.
_OPEN_VERIFY_TIMEOUT_MS = 500
_OPEN_VERIFY_POLL_INTERVAL_MS = 50
_OPEN_VERIFY_FUZZY_THRESHOLD = 60

# How long to poll GetForegroundWindow after close()/close_window() before giving up.
_CLOSE_VERIFY_TIMEOUT_MS = 100
_CLOSE_VERIFY_POLL_INTERVAL_MS = 20


# ---------------------------------------------------------------------------
# focus
# ---------------------------------------------------------------------------


@tool
def focus(target: str) -> None:
    """Focus a window matching *target* (process name or window title, fuzzy-matched).

    Delegates hwnd resolution to :func:`resolver.resolve_window`, then uses the
    Windows 11 AttachThreadInput workaround to claim foreground and verifies the
    focus change via ``_verify_foreground``.

    Raises
    ------
    FocusWindowError
        If no window matches above the configured fuzzy threshold, or if the
        focus attempt fails verification. Propagated so Dispatcher halts the
        plan chain rather than sending keystrokes to the wrong window.
    """
    try:
        import win32con
        import win32gui
        import win32process
    except ImportError as exc:
        logger.warning("pywin32 not available, cannot focus window")
        raise FocusWindowError("pywin32 not available; cannot focus window") from exc

    target_hwnd = resolver.resolve_window(target)

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
            f"SetForegroundWindow failed for target={target!r} "
            f"hwnd={target_hwnd}: {exc}"
        ) from exc
    finally:
        if not same_thread:
            _attach_thread_input(foreground_tid, target_tid, False)

    if not _verify_foreground(target_hwnd):
        raise FocusWindowError(
            f"Focus verification failed for target={target!r} "
            f"hwnd={target_hwnd} (GetForegroundWindow did not match after 60 ms)"
        )


# ---------------------------------------------------------------------------
# type  (Python symbol: type_text — avoid shadowing the builtin)
# ---------------------------------------------------------------------------


@tool(name="type")
def type_text(text: str) -> None:
    """Type *text* into the currently focused window via ``pyautogui.write``.

    Truncates to ``_MAX_TYPE_TEXT_LEN`` (500) characters with a WARNING log
    when exceeded. Uses a 0.02 s inter-keystroke interval, which is fast but
    still reliable against common input lag.
    """
    if len(text) > _MAX_TYPE_TEXT_LEN:
        logger.warning(
            "type truncated: %d chars > %d max", len(text), _MAX_TYPE_TEXT_LEN,
        )
        text = text[:_MAX_TYPE_TEXT_LEN]
    pyautogui.write(text, interval=0.02)


# ---------------------------------------------------------------------------
# open  (Python symbol: open_target — avoid shadowing the builtin)
# ---------------------------------------------------------------------------


@tool(name="open")
def open_target(target: str) -> None:
    """Open *target* — a URI, file path, or fuzzy-matched app name.

    Delegates resolution to :func:`resolver.resolve_app`, which returns a
    launch token (URI verbatim, resolved file path, or
    ``shell:AppsFolder\\<AUMID>``). The resolved token is then checked against
    a small blocklist of raw interpreter invocations before being handed to
    ``os.startfile``.

    After launch, polls ``EnumWindows`` for up to 500 ms looking for a new
    window whose title fuzzy-matches *target*. On timeout: WARNING log, no
    raise (some apps take seconds to appear). On success: INFO log.
    """
    token = resolver.resolve_app(target)

    # Blocklist check on the RESOLVED token, not the raw input.
    token_basename = os.path.basename(token).lower()
    if token_basename in _LAUNCH_BLOCKLIST:
        logger.warning(
            "open blocked suspicious resolved token: target=%r token=%r",
            target, token,
        )
        return

    try:
        os.startfile(token)
    except OSError:
        logger.exception("open() failed for target=%r token=%r", target, token)
        return

    _verify_open(target)


def _verify_open(target: str) -> None:
    """Best-effort post-launch verification: poll EnumWindows for a matching title."""
    try:
        import win32gui
        from rapidfuzz.fuzz import WRatio
    except ImportError:
        logger.debug("open verify skipped: pywin32/rapidfuzz not available")
        return

    deadline = time.monotonic() + _OPEN_VERIFY_TIMEOUT_MS / 1000.0
    poll_s = _OPEN_VERIFY_POLL_INTERVAL_MS / 1000.0

    while time.monotonic() < deadline:
        best_score = 0
        best_title = ""
        best_hwnd = 0

        def _enum(hwnd: int, _: object) -> bool:
            nonlocal best_score, best_title, best_hwnd
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return True
            score = int(WRatio(target, title))
            if score > best_score:
                best_score = score
                best_title = title
                best_hwnd = hwnd
            return True

        win32gui.EnumWindows(_enum, None)
        if best_score >= _OPEN_VERIFY_FUZZY_THRESHOLD:
            logger.info(
                "open verified: target=%r hwnd=%d title=%r score=%d",
                target, best_hwnd, best_title, best_score,
            )
            return
        time.sleep(poll_s)

    logger.warning(
        "open verify timeout: target=%r (no matching window within %d ms)",
        target, _OPEN_VERIFY_TIMEOUT_MS,
    )


# ---------------------------------------------------------------------------
# close / close_window
# ---------------------------------------------------------------------------


@tool
def close() -> None:
    """Close the current tab/document in the focused window via Ctrl+W."""
    _close_with_verify(("ctrl", "w"), verb="close")


@tool
def close_window() -> None:
    """Close the currently focused window via Alt+F4."""
    _close_with_verify(("alt", "f4"), verb="close_window")


def _close_with_verify(combo: tuple[str, ...], *, verb: str) -> None:
    """Issue *combo*, then poll ``GetForegroundWindow`` for a change."""
    try:
        import win32gui
    except ImportError:
        pyautogui.hotkey(*combo)
        return

    before_hwnd = win32gui.GetForegroundWindow()
    pyautogui.hotkey(*combo)

    deadline = time.monotonic() + _CLOSE_VERIFY_TIMEOUT_MS / 1000.0
    poll_s = _CLOSE_VERIFY_POLL_INTERVAL_MS / 1000.0
    while time.monotonic() < deadline:
        current_hwnd = win32gui.GetForegroundWindow()
        if current_hwnd != before_hwnd or not win32gui.IsWindow(before_hwnd):
            return
        time.sleep(poll_s)

    logger.warning(
        "%s verify timeout: foreground hwnd unchanged (hwnd=%d) after %d ms",
        verb, before_hwnd, _CLOSE_VERIFY_TIMEOUT_MS,
    )


# ---------------------------------------------------------------------------
# press
# ---------------------------------------------------------------------------


@tool
def press(combo: str) -> None:
    """Press a key combination like 'ctrl+c', 'alt+tab', 'win+l'."""
    keys = [k.strip() for k in combo.split("+")]
    pyautogui.hotkey(*keys)


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------


@tool
def wait(ms: int) -> None:
    """Pause execution for the specified milliseconds."""
    time.sleep(ms / 1000.0)


# ---------------------------------------------------------------------------
# click
# ---------------------------------------------------------------------------


_ALLOWED_CLICK_BUTTONS = frozenset({"left", "right", "middle"})


@tool
def click(button: str = "left") -> None:
    """Click the mouse at the current cursor position.

    *button* must be one of ``"left"``, ``"right"``, or ``"middle"``. Any
    other value logs a WARNING and is a no-op.
    """
    if button not in _ALLOWED_CLICK_BUTTONS:
        logger.warning("click: unknown button %r; no-op", button)
        return
    cast(Any, pyautogui).click(button=button)


# ---------------------------------------------------------------------------
# scroll  (bonus verb — not in the spec's 9, but harmless and already here)
# ---------------------------------------------------------------------------


@tool
def scroll(direction: str, amount: int = 3) -> None:
    """Scroll the active window up or down."""
    d = direction.lower()
    if d == "up":
        clicks = amount
    elif d == "down":
        clicks = -amount
    else:
        logger.warning("scroll: unknown direction %r; no-op", direction)
        return
    pyautogui.scroll(clicks)


# ---------------------------------------------------------------------------
# no_match
# ---------------------------------------------------------------------------


@tool
def no_match(reason: str) -> None:
    """Escape hatch: LLM signals no tool fits the utterance.

    The router intercepts ``no_match`` before dispatch — the body is a no-op.
    """
    # Body intentionally empty — router treats no_match as the "None plan" signal.
    return
