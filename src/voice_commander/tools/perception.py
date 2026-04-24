"""Perception tools — observation-only verbs for the agentic fallback loop.

These tools let the LLM observe the desktop environment without causing any
side effects. No windows are focused, no keystrokes are sent, no state is
mutated. All four functions are ``llm_only = true`` with ``phrases = []``.
"""

from __future__ import annotations

import contextlib
import logging

from ..registry import tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_process_name(pid: int) -> str:
    """Return the image-base-name for *pid*, or empty string on any failure.

    Prefers :mod:`psutil` (robust, cross-version). Returns ``""`` on any
    exception or if *pid* is zero.
    """
    if pid == 0:
        return ""
    try:
        import psutil
    except ImportError:
        return ""
    try:
        return str(psutil.Process(pid).name())
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# get_focused_window
# ---------------------------------------------------------------------------


@tool  # type: ignore[type-var]  # perception tools return data, not None
def get_focused_window() -> dict[str, str | int]:
    """Return the currently focused window title and process name.

    Uses ``win32gui.GetForegroundWindow()`` to obtain the active hwnd, then
    reads its title and resolves the owning process name via psutil.

    Returns ``{"hwnd": int, "title": str, "process": str}``.
    On import error (no pywin32), returns ``{"hwnd": 0, "title": "", "process": ""}``.
    """
    try:
        import win32gui
        import win32process
    except ImportError:
        logger.warning("pywin32 not available; get_focused_window returns empty result")
        return {"hwnd": 0, "title": "", "process": ""}

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": 0, "title": "", "process": ""}

    title = win32gui.GetWindowText(hwnd) or ""
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        pid = 0

    process = _get_process_name(pid)
    logger.debug("get_focused_window: hwnd=%d title=%r process=%r", hwnd, title, process)
    return {"hwnd": hwnd, "title": title, "process": process}


# ---------------------------------------------------------------------------
# list_windows
# ---------------------------------------------------------------------------


@tool  # type: ignore[type-var]  # perception tools return data, not None
def list_windows() -> list[dict[str, str | int]]:
    """List all visible, non-minimized windows (title + process, max 20).

    Uses ``win32gui.EnumWindows()`` with filters: ``IsWindowVisible()``,
    non-empty title, and not minimized (``not IsIconic()``). Results are
    sorted by title and capped at 20 entries.

    Returns ``[{"hwnd": int, "title": str, "process": str}]``.
    Returns an empty list if pywin32 is unavailable.
    """
    try:
        import win32gui
        import win32process
    except ImportError:
        logger.warning("pywin32 not available; list_windows returns empty list")
        return []

    results: list[dict[str, str | int]] = []

    def _enum(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.IsIconic(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if not title:
            return True
        try:
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        process = _get_process_name(pid)
        results.append({"hwnd": hwnd, "title": title, "process": process})
        return True

    win32gui.EnumWindows(_enum, None)

    results.sort(key=lambda w: str(w["title"]).lower())
    capped = results[:20]
    logger.debug("list_windows: found %d visible windows (returned %d)", len(results), len(capped))
    return capped


# ---------------------------------------------------------------------------
# get_clipboard
# ---------------------------------------------------------------------------


@tool  # type: ignore[type-var]  # perception tools return data, not None
def get_clipboard() -> dict[str, str | None]:
    """Return current clipboard text content (max 500 chars).

    Uses ``win32clipboard.OpenClipboard()`` / ``GetClipboardData(CF_UNICODETEXT)``
    / ``CloseClipboard()``. Text is capped at 500 characters.

    Returns ``{"text": str}`` on success, ``{"text": None}`` if the clipboard
    is empty, not text, or any error occurs.
    """
    try:
        import win32clipboard  # type: ignore[import-untyped]
        import win32con
    except ImportError:
        logger.warning("pywin32 not available; get_clipboard returns None")
        return {"text": None}

    try:
        win32clipboard.OpenClipboard()
    except Exception:
        logger.debug("get_clipboard: OpenClipboard failed", exc_info=True)
        return {"text": None}

    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return {"text": None}
        try:
            raw = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except Exception:
            logger.debug("get_clipboard: GetClipboardData failed", exc_info=True)
            return {"text": None}
    finally:
        with contextlib.suppress(Exception):
            win32clipboard.CloseClipboard()

    if not isinstance(raw, str):
        return {"text": None}

    text = raw[:500]
    logger.debug("get_clipboard: returned %d chars", len(text))
    return {"text": text}


# ---------------------------------------------------------------------------
# list_processes
# ---------------------------------------------------------------------------


@tool  # type: ignore[type-var]  # perception tools return data, not None
def list_processes() -> list[dict[str, str | int]]:
    """List running processes that have visible windows (max 20).

    Cross-references ``psutil.process_iter()`` with the PIDs reported by
    ``list_windows()`` so that only processes owning at least one visible,
    non-minimized window are returned. Results are sorted by process name
    and capped at 20 entries.

    Returns ``[{"pid": int, "name": str}]``.
    Returns an empty list if psutil is unavailable.
    """
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not available; list_processes returns empty list")
        return []

    # Collect the set of PIDs that own visible windows via list_windows().
    visible_windows = list_windows()
    visible_pids: set[int] = set()
    try:
        import win32process

        for w in visible_windows:
            hwnd = w.get("hwnd")
            if hwnd:
                try:
                    _tid, pid = win32process.GetWindowThreadProcessId(int(hwnd))
                    if pid:
                        visible_pids.add(pid)
                except Exception:
                    pass
    except ImportError:
        # pywin32 unavailable — fall back to the process field on the window dict.
        pass

    # If we could not resolve PIDs from hwnds, collect from psutil name match.
    # As a fallback, iterate psutil and check if name appears in visible window processes.
    if not visible_pids:
        visible_proc_names = {str(w.get("process", "")).lower() for w in visible_windows}
        results: list[dict[str, str | int]] = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                info = proc.info
                name = str(info.get("name") or "")
                if name.lower() in visible_proc_names and name:
                    results.append({"pid": int(info["pid"]), "name": name})
            except Exception:
                continue
        results.sort(key=lambda p: str(p["name"]).lower())
        capped = results[:20]
        logger.debug("list_processes (name-fallback): returning %d entries", len(capped))
        return capped

    results = []
    seen_pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            info = proc.info
            pid = int(info.get("pid") or 0)
            name = str(info.get("name") or "")
            if pid in visible_pids and pid not in seen_pids and name:
                results.append({"pid": pid, "name": name})
                seen_pids.add(pid)
        except Exception:
            continue

    results.sort(key=lambda p: str(p["name"]).lower())
    capped = results[:20]
    logger.debug(
        "list_processes: returning %d entries (visible_pids=%d)",
        len(capped),
        len(visible_pids),
    )
    return capped
