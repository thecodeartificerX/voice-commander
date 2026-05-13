"""Polls the OS cursor and docks a pyglet window to the current monitor's
work area (taskbar-excluded). Ctypes-only — no pywin32 dependency."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
from typing import Any

from .dpi import get_dpi_for_monitor

logger = logging.getLogger(__name__)

_MONITOR_DEFAULTTONEAREST = 0x00000002


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wt.LONG),
        ("top", wt.LONG),
        ("right", wt.LONG),
        ("bottom", wt.LONG),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wt.DWORD),
    ]


class CursorDock:
    """Snaps the given pyglet window to the bottom-right of the
    monitor currently under the cursor, with per-monitor DPI rescale."""

    def __init__(
        self,
        window: Any,
        sprite_base_size_px: int,
        window_extra_w_px: int,
        window_extra_h_px: int,
        margin_x: int,
        margin_y: int,
    ) -> None:
        self._window = window
        self._sprite_base = sprite_base_size_px
        self._extra_w_base = window_extra_w_px
        self._extra_h_base = window_extra_h_px
        self._margin_x = margin_x
        self._margin_y = margin_y
        self._last_hmon: int | None = None

    def tick(self, _dt: float) -> None:
        pt = wt.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return

        hmon = ctypes.windll.user32.MonitorFromPoint(pt, _MONITOR_DEFAULTTONEAREST)
        if hmon == self._last_hmon:
            return

        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            logger.debug("GetMonitorInfoW failed for hmon=%s", hmon)
            return

        dpi = get_dpi_for_monitor(hmon)
        scale = dpi / 96.0
        sprite_size = int(self._sprite_base * scale)
        extra_w = int(self._extra_w_base * scale)
        extra_h = int(self._extra_h_base * scale)
        window_w = sprite_size + extra_w
        window_h = sprite_size + extra_h

        self._window.set_size(window_w, window_h)

        x = mi.rcWork.right - window_w - self._margin_x
        y = mi.rcWork.bottom - window_h - self._margin_y
        self._window.set_location(x, y)
        self._last_hmon = hmon
        logger.debug(
            "CursorDock → hmon=%s rcWork=(%d,%d,%d,%d) dpi=%d → window=(%d,%d) @ (%d,%d)",
            hmon,
            mi.rcWork.left,
            mi.rcWork.top,
            mi.rcWork.right,
            mi.rcWork.bottom,
            dpi,
            window_w,
            window_h,
            x,
            y,
        )
