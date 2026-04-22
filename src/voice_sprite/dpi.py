from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes
import logging

logger = logging.getLogger(__name__)


def get_primary_dpi() -> int:
    """Return the DPI of the primary monitor. Falls back to 96 on failure."""
    with contextlib.suppress(AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)

    try:
        hmon = ctypes.windll.user32.MonitorFromPoint(
            ctypes.wintypes.POINT(0, 0),
            1,  # MONITOR_DEFAULTTOPRIMARY
        )
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        return dpi_x.value
    except (AttributeError, OSError):
        logger.warning("Could not read DPI — falling back to 96")
        return 96
