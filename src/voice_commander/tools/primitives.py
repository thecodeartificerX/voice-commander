"""Primitive verbs — the 7-action + 4-perception tool catalog.

This module is the sole source of action primitives. Every verb
here is ``llm_only = true`` with ``phrases = []``. Two verbs shadow Python
builtins — ``type`` and ``open``. Their Python symbols are ``type_text`` and
``open_target``; the registry exposes them under the short names via
``@tool(name=...)``.

Action verbs: ``focus``, ``type``, ``open``, ``press``, ``wait``, ``click``, ``scroll``.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, cast

import pyautogui

from .. import resolver
from ..registry import tool
from ._win32 import (
    FocusWindowError,
    _do_focus,
    _verify_foreground,
)
from .keyboard_combo import (
    PRESS_ALIASES as _PRESS_ALIASES,
)
from .keyboard_combo import (
    PRESS_SPLIT_RE as _PRESS_SPLIT_RE,
)
from .keyboard_combo import (
    parse_combo as _parse_combo,
)

logger = logging.getLogger(__name__)

_MAX_TYPE_TEXT_LEN = 500

# Applied to the RESOLVED launch token basename (post-resolver.resolve_app)
# AND to the raw user-provided target when it looks like a direct filename.
# Guards against interpreter invocation and system-utility launch.
_LAUNCH_BLOCKLIST = {
    # Shell interpreters / script hosts
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "wscript",
    "wscript.exe",
    "cscript",
    "cscript.exe",
    # Admin / destructive system utilities
    "regedit",
    "regedit.exe",
    "diskmgmt.msc",
    "diskpart",
    "diskpart.exe",
    "format",
    "format.com",
    "cipher",
    "cipher.exe",
    "gpedit.msc",
    "secpol.msc",
    "services.msc",
    "shutdown",
    "shutdown.exe",
    "taskkill",
    "taskkill.exe",
    "rundll32",
    "rundll32.exe",
    "msconfig",
    "msconfig.exe",
}

# Deny any launch whose resolved path lives under a Windows system directory.
_SYSTEM_PATH_RE = re.compile(r"[\\/](System32|SysWOW64|WinSxS)[\\/]", re.IGNORECASE)

# Applied to `press(combo=...)` BEFORE keystroke synthesis. Normalized to
# sorted lowercase token set, so "shift+delete", "Delete+Shift", "SHIFT+DEL"
# all match the same blocked chord.
_PRESS_BLOCKLIST: set[frozenset[str]] = {
    frozenset({"shift", "delete"}),  # permanent delete, bypasses Recycle Bin
    frozenset({"shift", "del"}),
    frozenset({"win", "r"}),  # Run dialog — script / command entry
}

# Aliases + split regex live in tools/keyboard_combo.py; re-exported above
# so existing imports continue to work.

# How long to poll EnumWindows after an open() before giving up.
_OPEN_VERIFY_TIMEOUT_MS = 500
_OPEN_VERIFY_POLL_INTERVAL_MS = 50
_OPEN_VERIFY_FUZZY_THRESHOLD = 60


# ---------------------------------------------------------------------------
# focus
# ---------------------------------------------------------------------------


@tool
def focus(target: str) -> int:
    """Focus a window matching *target* (process name or window title, fuzzy-matched).

    Delegates hwnd resolution to :func:`resolver.resolve_window`, then uses the
    Windows 11 AttachThreadInput workaround to claim foreground and verifies the
    focus change via ``_verify_foreground``.

    Returns
    -------
    int
        The Win32 window handle (hwnd) of the foregrounded window.

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

    try:
        _do_focus(target_hwnd, foreground_tid, target_tid)
    except Exception as exc:
        raise FocusWindowError(
            f"SetForegroundWindow failed for target={target!r} hwnd={target_hwnd}: {exc}"
        ) from exc

    if not _verify_foreground(target_hwnd):
        raise FocusWindowError(
            f"Focus verification failed for target={target!r} "
            f"hwnd={target_hwnd} (GetForegroundWindow did not match after 500 ms)"
        )

    return target_hwnd


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
            "type truncated: %d chars > %d max",
            len(text),
            _MAX_TYPE_TEXT_LEN,
        )
        text = text[:_MAX_TYPE_TEXT_LEN]
    pyautogui.write(text, interval=0.02)


# ---------------------------------------------------------------------------
# open  (Python symbol: open_target — avoid shadowing the builtin)
# ---------------------------------------------------------------------------


@tool(name="open")
def open_target(target: str) -> int:
    """Open *target* — a URI, file path, or fuzzy-matched app name.

    Delegates resolution to :func:`resolver.resolve_app`, which returns a
    launch token (URI verbatim, resolved file path, or
    ``shell:AppsFolder\\<AUMID>``). The resolved token is then checked against
    a small blocklist of raw interpreter invocations before being handed to
    ``os.startfile``.

    After launch, polls ``EnumWindows`` for up to 500 ms looking for a new
    window whose title fuzzy-matches *target*. On timeout: WARNING log, no
    raise (some apps take seconds to appear). On success: INFO log.

    Returns
    -------
    int
        The Win32 window handle of the verified-launched window, or 0 if
        verification timed out or the launch was blocked.
    """
    # Pre-resolution raw-input check: reject if user-provided target looks
    # like a direct filename match for a blocked interpreter / utility.
    raw_basename = os.path.basename(target).lower()
    if raw_basename in _LAUNCH_BLOCKLIST:
        logger.warning(
            "open blocked destructive raw target: target=%r",
            target,
        )
        return 0

    token = resolver.resolve_app(target)

    # Post-resolution: basename blocklist covers resolved .lnk pointing at
    # e.g. cmd.exe, or AppsFolder AUMIDs landing on blocked basenames.
    token_basename = os.path.basename(token).lower()
    if token_basename in _LAUNCH_BLOCKLIST:
        logger.warning(
            "open blocked destructive resolved token: target=%r token=%r",
            target,
            token,
        )
        return 0

    # Post-resolution: reject any launch whose path lives under a Windows
    # system directory. Catches System32 / SysWOW64 / WinSxS targets that
    # slip past the basename check (e.g. renamed copies of regedit.exe).
    if _SYSTEM_PATH_RE.search(token):
        logger.warning(
            "open blocked system-path target: target=%r token=%r",
            target,
            token,
        )
        return 0

    os.startfile(token)

    return _verify_open(target)


def _verify_open(target: str) -> int:
    """Best-effort post-launch verification: poll EnumWindows for a matching title.

    Returns the matched hwnd on success, or 0 on timeout / unavailable deps.
    """
    try:
        import win32gui
        from rapidfuzz.fuzz import WRatio
    except ImportError:
        logger.debug("open verify skipped: pywin32/rapidfuzz not available")
        return 0

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
                target,
                best_hwnd,
                best_title,
                best_score,
            )
            return best_hwnd
        time.sleep(poll_s)

    logger.warning(
        "open verify timeout: target=%r (no matching window within %d ms)",
        target,
        _OPEN_VERIFY_TIMEOUT_MS,
    )
    return 0


# ---------------------------------------------------------------------------
# press
# ---------------------------------------------------------------------------


@tool
def press(combo: str) -> None:
    """Press a key combination like 'ctrl+c', 'alt+tab', 'win+l'.

    Accepts ``+``, whitespace, commas, or ``and`` as separators so that
    transcripts like ``"Ctrl V"`` or ``"control and shift t"`` route
    correctly. Aliases ``control``/``command``/``windows``/``option``/
    ``return``/``escape``/``spacebar``/``pgup``/``pgdn`` to pyautogui's
    canonical keynames.

    Blocks known-destructive chords (Shift+Delete, Win+R, …). Logs
    WARNING and no-ops instead of raising — the dispatcher continues
    with the rest of the plan if any.
    """
    keys = _parse_combo(combo)
    if not keys:
        logger.warning("press: empty combo %r; no-op", combo)
        return
    valid_keys = getattr(pyautogui, "KEYBOARD_KEYS", None)
    if valid_keys is not None:
        unknown = [k for k in keys if k not in valid_keys]
        if unknown:
            logger.warning(
                "press: unknown key(s) %s in combo=%r (normalized=%s); no-op",
                unknown,
                combo,
                "+".join(keys),
            )
            return
    key_set = frozenset(keys)
    if key_set in _PRESS_BLOCKLIST:
        logger.warning(
            "press blocked destructive chord: combo=%r (normalized=%s)",
            combo,
            "+".join(sorted(key_set)),
        )
        return
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
# scroll
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


