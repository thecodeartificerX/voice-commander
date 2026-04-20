from __future__ import annotations

import logging
import winreg
from subprocess import Popen

import pyautogui

logger = logging.getLogger(__name__)


def default_browser_progid() -> str | None:
    """Returns the ProgID of the default browser from the Windows registry, or None."""
    path = r"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            progid, _ = winreg.QueryValueEx(key, "ProgId")
            return progid
    except OSError:
        return None


def progid_to_exe(progid: str) -> str | None:
    mapping = {
        "ChromeHTML": "chrome.exe",
        "MSEdgeHTM": "msedge.exe",
        "FirefoxURL": "firefox.exe",
        "BraveHTML": "brave.exe",
    }
    for prefix, exe in mapping.items():
        if progid.startswith(prefix):
            return exe
    return None


def focus_window_by_exe(exe_name: str) -> bool:
    """Cycles Alt+Tab-less focus to a window whose process exe matches. Returns True on success."""
    try:
        import psutil
        import win32con
        import win32gui
        import win32process
    except ImportError:
        Popen([exe_name])
        return False

    target_pids = {p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == exe_name}
    if not target_pids:
        Popen([exe_name])
        return False

    found: list[int] = []
    def _enum(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in target_pids:
            found.append(hwnd)
        return True

    win32gui.EnumWindows(_enum, None)
    if not found:
        return False
    hwnd = found[0]
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        logger.exception("SetForegroundWindow failed")
        return False
    return True
