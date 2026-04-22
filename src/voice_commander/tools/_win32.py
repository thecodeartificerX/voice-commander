from __future__ import annotations

import ctypes
import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path
from subprocess import Popen

logger = logging.getLogger(__name__)

COMET_EXE = "comet.exe"
COMET_LAUNCH_PATH = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Perplexity" / "Comet" / "Application" / "comet.exe"
)

# SW_RESTORE value — same constant used by win32con; duplicated here so it's
# available even before pywin32 is imported, and for ctypes-only fallback paths.
_SW_RESTORE = 9

# AllowSetForegroundWindow(ASFW_ANY) — allows any process to set foreground.
_ASFW_ANY = 0xFFFFFFFF


class FocusWindowError(RuntimeError):
    """Raised when focus_window_by_exe cannot reliably place a window in the foreground."""


def _allow_set_foreground() -> None:
    """Belt-and-suspenders: grant ASFW_ANY so our process can claim foreground."""
    try:
        user32 = ctypes.windll.user32
        # argtypes/restype for AllowSetForegroundWindow
        allow_fn = user32.AllowSetForegroundWindow
        allow_fn.argtypes = [ctypes.c_uint]
        allow_fn.restype = ctypes.c_bool
        allow_fn(_ASFW_ANY)
    except Exception:
        logger.debug("AllowSetForegroundWindow unavailable", exc_info=True)


# Alt-tap trick: briefly synthesize Alt key down+up. Windows treats the
# calling thread as having just received input from the user, which
# lifts the foreground-lockout restriction for the immediately following
# SetForegroundWindow call. The Alt key is chosen because a bare tap
# does nothing visible on Windows (it only activates the menu bar if
# held, and even that requires a focused window that has a menu).
_VK_MENU = 0x12  # Alt
_KEYEVENTF_KEYUP = 0x0002


def _grant_foreground_privilege() -> None:
    """Synthesize a brief Alt keypress to satisfy Windows' foreground lockout.

    On Windows 10/11, ``SetForegroundWindow`` silently fails unless the
    calling thread has received input from the user in the last few
    hundred milliseconds (or other conditions from
    `SetForegroundWindow`_ MSDN page). For a background daemon driven by
    VAD-triggered speech, no such input exists. Faking an Alt tap via
    ``keybd_event`` fools the foreground-lockout check without any
    user-visible side effect on Windows.

    .. _SetForegroundWindow: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow
    """
    try:
        user32 = ctypes.windll.user32
        keybd = user32.keybd_event
        keybd.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_void_p]
        keybd.restype = None
        keybd(_VK_MENU, 0, 0, None)  # Alt down
        keybd(_VK_MENU, 0, _KEYEVENTF_KEYUP, None)  # Alt up
    except Exception:
        logger.debug("keybd_event(Alt tap) failed", exc_info=True)


def _get_current_thread_id() -> int:
    """Return the calling thread's OS thread ID via kernel32."""
    try:
        kernel32 = ctypes.windll.kernel32
        return int(kernel32.GetCurrentThreadId())
    except Exception:
        return 0


def _attach_thread_input(attach_from: int, attach_to: int, attach: bool) -> None:
    """Wrapper around user32.AttachThreadInput with explicit argtypes."""
    try:
        user32 = ctypes.windll.user32
        fn = user32.AttachThreadInput
        fn.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_bool]
        fn.restype = ctypes.c_bool
        fn(attach_from, attach_to, attach)
    except Exception:
        logger.debug("AttachThreadInput failed attach=%s", attach, exc_info=True)


def _verify_foreground(target_hwnd: int, polls: int = 10, interval_ms: int = 20) -> bool:
    """Poll GetForegroundWindow up to *polls* times; return True if it equals target_hwnd.

    Defaults to ~200 ms total (10 × 20 ms). Windows' foreground state can
    lag behind the SetForegroundWindow call, especially for Chromium-based
    apps (Comet, Chrome, Edge, Slack, VS Code) which do their own window
    management between the kernel notification and the foreground change
    becoming observable.
    """
    try:
        import win32gui
    except ImportError:
        return False
    for _ in range(polls):
        if win32gui.GetForegroundWindow() == target_hwnd:
            return True
        time.sleep(interval_ms / 1000.0)
    return False


def _do_focus(hwnd: int, foreground_tid: int, target_tid: int) -> None:
    """Core focus sequence: optionally attach threads, BringWindowToTop, SetForegroundWindow."""
    import win32gui

    _allow_set_foreground()

    # Grant ourselves foreground-change privilege. Without this, Windows
    # 10/11 silently refuses SetForegroundWindow calls from a background
    # daemon that has not received recent user input.
    _grant_foreground_privilege()

    same_thread = foreground_tid == target_tid or foreground_tid == 0

    if not same_thread:
        _attach_thread_input(foreground_tid, target_tid, True)
    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    finally:
        if not same_thread:
            _attach_thread_input(foreground_tid, target_tid, False)


def focus_window_by_exe(exe_name: str, launch_path: str | Sequence[str] | None = None) -> bool:
    """Focus the foreground window to a process whose exe name matches *exe_name*.

    Algorithm
    ---------
    1. Enumerate top-level visible windows; pick the first whose owning process
       has ``exe_name`` as its image name.
    2. If the window is minimized (``IsIconic``), restore it via ``ShowWindow``.
    3. Call ``AllowSetForegroundWindow(ASFW_ANY)`` as belt-and-suspenders.
    4. Obtain the current foreground window's thread ID.
    5. If the foreground thread differs from the target window's thread, use
       ``AttachThreadInput(fg_tid, target_tid, TRUE)`` around the focus call,
       with a guaranteed ``AttachThreadInput(..., FALSE)`` detach in a finally block.
    6. Call ``BringWindowToTop(hwnd)`` then ``SetForegroundWindow(hwnd)``.
    7. Poll ``GetForegroundWindow()`` up to ~60 ms (3 × 20 ms) to verify success.

    Returns
    -------
    bool
        ``True`` on verified success.

    Raises
    ------
    FocusWindowError
        On any failure — window not found, SetForegroundWindow denied, or
        verification timeout. Callers should let this propagate so ``Dispatcher``
        halts the plan chain instead of sending keystrokes to the wrong window.
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
    except ImportError as exc:
        Popen(spawn_argv)
        raise FocusWindowError(
            f"pywin32/psutil not available; launched '{exe_name}' instead"
        ) from exc

    target_pids = {p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == exe_name}
    if not target_pids:
        Popen(spawn_argv)
        raise FocusWindowError(f"No running process named '{exe_name}'; launched it instead")

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
        raise FocusWindowError(
            f"Process '{exe_name}' is running but has no visible top-level window"
        )

    hwnd = found[0]

    # Restore if minimized.
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    # Get thread IDs for AttachThreadInput.
    fg_hwnd = win32gui.GetForegroundWindow()
    foreground_tid: int
    if fg_hwnd:
        foreground_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
    else:
        foreground_tid = 0
    target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)

    try:
        _do_focus(hwnd, foreground_tid, target_tid)
    except Exception as exc:
        raise FocusWindowError(
            f"SetForegroundWindow failed for '{exe_name}' hwnd={hwnd}: {exc}"
        ) from exc

    if not _verify_foreground(hwnd):
        raise FocusWindowError(
            f"Focus verification failed for '{exe_name}' hwnd={hwnd} "
            "(GetForegroundWindow did not match after 200 ms)"
        )

    return True
