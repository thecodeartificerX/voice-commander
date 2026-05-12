"""Parameter resolver — rapidfuzz-backed mapping from utterances to hwnds / launch tokens.

Two pure functions power the ``focus`` and ``open`` verbs:

* :func:`resolve_window` — enumerates visible windows, scores each candidate by
  ``max(WRatio(target, proc_name), WRatio(target, title))``, returns the
  top-scoring hwnd above the configured threshold.
* :func:`resolve_app` — resolves *target* to a launch token. URIs and existing
  paths pass through verbatim; fuzzy app names resolve against Start Menu
  ``.lnk`` files and ``shell:AppsFolder`` entries (cached for the daemon's
  lifetime on first call).

Both functions raise ``FocusWindowError`` / ``OpenResolveError`` carrying the
top-3 candidates when no match clears the configured threshold.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from rapidfuzz.fuzz import WRatio

from .tools._win32 import FocusWindowError

logger = logging.getLogger(__name__)


class OpenResolveError(Exception):
    """Raised when :func:`resolve_app` cannot fuzzy-match *target* to any known app."""


# ---------------------------------------------------------------------------
# Config threshold accessors
# ---------------------------------------------------------------------------


_DEFAULT_FOCUS_THRESHOLD = 70
_DEFAULT_OPEN_THRESHOLD = 70

# Cache of the active config (set by daemon.build_streaming_daemon at
# startup). Tests can override via :func:`_set_config_for_tests`.
_config_ref: Any = None


def _set_config(config: Any) -> None:
    """Daemon hook — wires the active config so fuzzy thresholds flow from TOML."""
    global _config_ref
    _config_ref = config


def _focus_threshold() -> int:
    cfg = _config_ref
    if cfg is not None and hasattr(cfg, "focus_fuzzy_threshold"):
        return int(cfg.focus_fuzzy_threshold)
    return _DEFAULT_FOCUS_THRESHOLD


def _open_threshold() -> int:
    cfg = _config_ref
    if cfg is not None and hasattr(cfg, "open_fuzzy_threshold"):
        return int(cfg.open_fuzzy_threshold)
    return _DEFAULT_OPEN_THRESHOLD


# ---------------------------------------------------------------------------
# resolve_window
# ---------------------------------------------------------------------------


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def resolve_window(target: str) -> int:
    """Fuzzy-match *target* to a visible window; return its hwnd.

    Scoring: ``max(WRatio(target, proc_name), WRatio(target, title))`` per
    candidate, take argmax above ``focus_fuzzy_threshold`` (default 70).

    Raises
    ------
    FocusWindowError
        If no candidate clears the threshold. The error message carries the
        top-3 candidates so the user can see why a call missed.
    """
    try:
        import win32api
        import win32con  # noqa: F401  (imported for side effects / parity)
        import win32gui
        import win32process
    except ImportError as exc:
        raise FocusWindowError("pywin32 not available; cannot enumerate windows") from exc

    candidates: list[tuple[int, str, str, int]] = []

    def _enum(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if not title:
            # Skip windows without a title — too noisy for fuzzy scoring and
            # they are usually tooltips / system surfaces the user can't name.
            return True

        try:
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0

        proc_name = _get_process_name(pid)

        proc_score = int(WRatio(target, proc_name)) if proc_name else 0
        title_score = int(WRatio(target, title)) if title else 0
        score = max(proc_score, title_score)

        candidates.append((hwnd, proc_name, title, score))
        return True

    win32gui.EnumWindows(_enum, None)

    if not candidates:
        raise FocusWindowError(
            f"no window matching {target!r}; no visible windows with titles enumerated"
        )

    # Sort descending by score for top-3 reporting.
    candidates.sort(key=lambda c: c[3], reverse=True)
    top3 = candidates[:3]
    best_hwnd, best_proc, best_title, best_score = top3[0]

    threshold = _focus_threshold()

    # Suppress unused-warning without affecting runtime.
    _ = win32api

    if best_score < threshold:
        top3_display = [(p, t, s) for _hwnd, p, t, s in top3]
        raise FocusWindowError(
            f"no window matching {target!r} (threshold={threshold}); top3={top3_display}"
        )

    logger.debug(
        "resolve_window target=%r picked=hwnd=%d proc=%r title=%r score=%d top3=%r",
        target,
        best_hwnd,
        best_proc,
        best_title,
        best_score,
        [(p, t, s) for _hwnd, p, t, s in top3],
    )
    return best_hwnd


def _get_process_name(pid: int) -> str:
    """Return the image-base-name for *pid*, or empty string on any failure.

    Prefers :mod:`psutil` (robust, cross-version) and falls back to the
    raw Win32 ``GetModuleBaseName`` path when psutil is unavailable.
    """
    if pid == 0:
        return ""
    # Primary path: psutil.
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            return str(psutil.Process(pid).name())
        except Exception:
            return ""
    # Fallback: Win32 GetModuleBaseName. Requires a non-zero hModule — pass
    # ``None`` for pywin32 to substitute the process's main module.
    try:
        import win32api
        import win32process
    except ImportError:
        return ""
    try:
        handle = win32api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except Exception:
        return ""
    try:
        name = win32process.GetModuleBaseName(handle, None)
    except Exception:
        return ""
    finally:
        with contextlib.suppress(Exception):
            handle.Close()
    return str(name) if name else ""


# ---------------------------------------------------------------------------
# resolve_app
# ---------------------------------------------------------------------------


_URI_RE = re.compile(r"^[a-z][a-z0-9+\-.]*://", re.IGNORECASE)

# Daemon-lifetime cache. Populated on first resolve_app() call that needs it.
_cache: dict[str, list[tuple[str, str]] | None] = {"apps": None}
_cache_lock = threading.Lock()


def resolve_app(target: str) -> str:
    """Resolve *target* to a launch token (URI / absolute path / shell:AppsFolder\\<AUMID>).

    Resolution order:

    1. URI (any ``scheme://…``) — returned verbatim.
    2. Existing filesystem path — returned as absolute ``str``.
    3. Fuzzy match against Start Menu ``.lnk`` stems + ``shell:AppsFolder``
       display names. Best match above ``open_fuzzy_threshold`` wins.

    Raises
    ------
    OpenResolveError
        If no candidate clears the threshold.
    """
    if _URI_RE.match(target):
        return target

    path_candidate: Path | None
    try:
        path_candidate = Path(target)
    except (OSError, ValueError):
        path_candidate = None
    if path_candidate is not None:
        try:
            if path_candidate.exists():
                return str(path_candidate.resolve())
        except OSError:
            pass

    apps = _get_app_cache()
    if not apps:
        raise OpenResolveError(
            f"no app matching {target!r}; app catalog empty "
            f"(Start Menu + AppsFolder enumeration found nothing)"
        )

    scored: list[tuple[str, str, int]] = [
        (display, token, int(WRatio(target, display))) for display, token in apps
    ]
    scored.sort(key=lambda c: c[2], reverse=True)
    top3 = scored[:3]
    best_display, best_token, best_score = top3[0]

    threshold = _open_threshold()
    if best_score < threshold:
        top3_display = [(d, s) for d, _t, s in top3]
        raise OpenResolveError(
            f"no app matching {target!r} (threshold={threshold}); top3={top3_display}"
        )

    logger.debug(
        "resolve_app target=%r picked=display=%r token=%r score=%d top3=%r",
        target,
        best_display,
        best_token,
        best_score,
        [(d, s) for d, _t, s in top3],
    )
    return best_token


_DANGEROUS_DISPLAY_PATTERNS = re.compile(
    r"\b("
    # Install / repair / uninstall chain
    r"uninstall|uninstaller|repair|reset|crash|setup|installer"
    # Admin / system configuration shortcuts
    r"|registry editor|regedit"
    r"|disk management|diskmgmt|diskpart"
    r"|format|cipher"
    r"|group policy|gpedit|secpol"
    r"|services manager"
    r"|local users and groups"
    r"|event viewer"
    r")\b",
    re.IGNORECASE,
)


def _is_dangerous_candidate(display: str) -> bool:
    """Filter out uninstallers / repairers / setup shortcuts from the app catalog.

    The resolver fuzzy-matches utterances to display names; without this
    filter, saying "facebook" (no Facebook app installed) could score an
    "Uninstall Zoom Workplace" entry highly enough to launch it. That is
    a destructive action the user did not intend.
    """
    return bool(_DANGEROUS_DISPLAY_PATTERNS.search(display))


def _get_app_cache() -> list[tuple[str, str]]:
    """Return the cached (display_name, launch_token) list, enumerating on first call."""
    cached = _cache["apps"]  # snapshot — single atomic read
    if cached is not None:
        return cached
    with _cache_lock:
        cached = _cache["apps"]  # re-check under lock — snapshot
        if cached is not None:
            return cached
        raw = _enumerate_start_menu() + _enumerate_apps_folder()
        apps: list[tuple[str, str]] = []
        dropped = 0
        for display, token in raw:
            if _is_dangerous_candidate(display):
                dropped += 1
                continue
            apps.append((display, token))
        _cache["apps"] = apps
        logger.info(
            "resolve_app cache populated: %d entries (dropped %d dangerous)",
            len(apps),
            dropped,
        )
        return apps


def _invalidate_app_cache() -> None:
    """Test hook — clear the app cache so subsequent calls re-enumerate."""
    with _cache_lock:
        _cache["apps"] = None


def _enumerate_start_menu() -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    roots: list[Path] = []

    program_data = os.environ.get("ProgramData")  # noqa: SIM112 - Windows env var casing
    if program_data:
        roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")

    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")

    for root in roots:
        if not root.exists():
            continue
        try:
            for lnk in root.rglob("*.lnk"):
                results.append((lnk.stem, str(lnk)))
        except OSError:
            logger.debug("Start Menu scan failed under %s", root, exc_info=True)
    return results


def _enumerate_apps_folder() -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        logger.debug("win32com not available, skipping AppsFolder enum")
        return results

    try:
        pythoncom.CoInitialize()
    except Exception:
        logger.debug("CoInitialize failed (already initialized?)", exc_info=True)

    try:
        shell = win32com.client.Dispatch("Shell.Application")
        ns = shell.NameSpace("shell:AppsFolder")
        if ns is None:
            logger.warning("shell:AppsFolder NameSpace returned None")
            return results
        items = ns.Items()
        count = items.Count
        for i in range(count):
            try:
                item = items.Item(i)
                display = str(item.Name)
                aumid = str(item.Path)
                token = f"shell:AppsFolder\\{aumid}"
                results.append((display, token))
            except Exception:
                continue
    except Exception:
        logger.warning("AppsFolder enumeration failed", exc_info=True)
    return results
