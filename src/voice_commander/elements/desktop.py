"""Foreground-window and monitor geometry helpers.

Isolates the ``pywin32`` calls so the scanner/session modules stay free of
Win32 detail. The overlay only ever covers the monitor that hosts the
foreground window, so ``monitor_rect`` keeps a single, uniform DPI.
"""

from __future__ import annotations


def foreground_window() -> int:
    """Return the HWND of the current foreground window (0 if none)."""
    import win32gui

    return int(win32gui.GetForegroundWindow())


def monitor_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return the full bounds (left, top, right, bottom) of the monitor
    that hosts ``hwnd``."""
    import win32api
    import win32con

    hmon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    info = win32api.GetMonitorInfo(hmon)
    left, top, right, bottom = info["Monitor"]
    return (int(left), int(top), int(right), int(bottom))
