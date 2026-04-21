from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path
from subprocess import Popen

logger = logging.getLogger(__name__)

COMET_EXE = "comet.exe"
COMET_LAUNCH_PATH = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Perplexity"
    / "Comet"
    / "Application"
    / "comet.exe"
)


def focus_window_by_exe(
    exe_name: str, launch_path: str | Sequence[str] | None = None
) -> bool:
    """Cycles Alt+Tab-less focus to a window whose process exe matches. Returns True on success.

    If the pywin32/psutil imports fail, or if no running process matches
    ``exe_name``, the function falls back to launching the application via
    ``subprocess.Popen``. ``launch_path`` overrides the spawn argv (useful when
    ``exe_name`` is not on PATH); defaults to ``[exe_name]``.
    """
    spawn_argv: Sequence[str]
    if launch_path is None:
        spawn_argv = [exe_name]
    elif isinstance(launch_path, str):
        spawn_argv = [launch_path]
    else:
        spawn_argv = launch_path

    try:
        import psutil
        import win32con
        import win32gui
        import win32process
    except ImportError:
        Popen(spawn_argv)
        return False

    target_pids = {p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == exe_name}
    if not target_pids:
        Popen(spawn_argv)
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
