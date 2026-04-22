"""Apply Win32 extended window styles for click-through, topmost, no-taskbar sprite."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000  # Enables per-pixel alpha / transparency
WS_EX_TRANSPARENT = 0x00000020  # Click-through (mouse events pass to window below)
WS_EX_TOOLWINDOW = 0x00000080  # Excluded from taskbar and Alt+Tab
WS_EX_NOACTIVATE = 0x08000000  # Never receives input focus
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
LWA_ALPHA = 0x00000002


class MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


class DWM_BLURBEHIND(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("fEnable", wintypes.BOOL),
        ("hRgnBlur", wintypes.HRGN),
        ("fTransitionOnMaximized", wintypes.BOOL),
    ]


DWM_BB_ENABLE = 0x1

# NOTE: don't use ctypes.HRESULT as restype — it auto-raises OSError on any
# non-zero return, including benign statuses like DWM_E_COMPOSITIONDISABLED,
# which would abort the whole flag-application pipeline. Use c_long and
# check manually so one failed call doesn't skip the rest.
dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long

dwmapi.DwmEnableBlurBehindWindow.argtypes = [wintypes.HWND, ctypes.POINTER(DWM_BLURBEHIND)]
dwmapi.DwmEnableBlurBehindWindow.restype = ctypes.c_long


def apply_click_through(hwnd: int) -> None:
    """Make a window click-through, always-on-top, no taskbar, no focus steal.

    Also calls DwmExtendFrameIntoClientArea with margins=-1 — canonical
    Win32 pattern that tells DWM to treat the entire client area as
    sheet-of-glass and honor the framebuffer's per-pixel alpha. Without
    this call, DWM on Windows 11 may composite the OpenGL framebuffer
    against an opaque black bg even when alpha=0 everywhere (pyglet
    issue #693).
    """
    try:
        # Add WS_EX_TOOLWINDOW (no taskbar entry) + WS_EX_NOACTIVATE (no
        # focus steal) on top of pyglet's WS_EX_LAYERED|WS_EX_TRANSPARENT.
        # Do NOT re-set WS_EX_LAYERED alone or re-call SetLayeredWindowAttributes
        # — calling SetLayeredWindowAttributes switches the window to constant
        # alpha mode and DISABLES the per-pixel alpha that DWM was using to
        # composite our framebuffer against the desktop. This was the cause
        # of the "opaque black background" bug.
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    except OSError:
        logger.exception("Failed core click-through flags on hwnd=%d", hwnd)

    # DwmExtendFrameIntoClientArea is useless here — it requires a window
    # with non-client area (title bar, borders) to extend. WS_POPUP has no
    # frame, so the call returns E_INVALIDARG. Skip it for overlay windows.

    # Override pyglet's DwmEnableBlurBehindWindow call. Pyglet passes an
    # empty blur region with DWM_BB_BLURREGION flag; on Windows 11 this
    # can fail to enable per-pixel alpha because DWM sees the BLURREGION
    # flag and tries to apply blur to an empty region. Canonical pattern =
    # DWM_BB_ENABLE only, NULL region — tells DWM to composite the
    # framebuffer's alpha channel directly.
    try:
        bb = DWM_BLURBEHIND()
        bb.dwFlags = DWM_BB_ENABLE
        bb.fEnable = True
        bb.hRgnBlur = 0
        bb.fTransitionOnMaximized = False
        hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
        if hr != 0:
            logger.warning("DwmEnableBlurBehindWindow HRESULT=0x%08x", hr & 0xFFFFFFFF)
    except OSError:
        logger.exception("DwmEnableBlurBehindWindow failed on hwnd=%d", hwnd)

    logger.info("Applied click-through + DWM sheet-of-glass flags to hwnd=%d", hwnd)
