"""MRU tracker for focused windows.

Two layers:

* :class:`MruTracker` — a pure ring buffer + filter helper. No Win32
  dependency, fully unit-testable.
* :class:`Win32MruPump` — installs a :func:`SetWinEventHook` on a dedicated
  message-pump thread and pushes :class:`MruEntry` records into the tracker.

The pump lives at the bottom of this module so unit tests can import
:class:`MruTracker` without triggering the pywin32 import.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MruEntry:
    hwnd: int
    pid: int
    proc_name: str
    title: str


class MruTracker:
    """Ring buffer of recently foregrounded windows, newest first.

    Re-recording an hwnd already in the buffer moves it to the front; this
    matches the "switching back and forth" intuition. Thread-safe: ``record``
    and ``snapshot`` / ``top`` may be called from different threads.
    """

    def __init__(self, capacity: int = 32) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._capacity = capacity
        self._buf: deque[MruEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._self_hwnds: set[int] = set()

    def register_self_hwnd(self, hwnd: int) -> None:
        """Mark *hwnd* as belonging to this daemon / sprite / modal.

        The tracker stores the set but does not filter automatically — callers
        pass ``predicate=lambda e: e.hwnd not in tracker.self_hwnds`` to
        :meth:`top` when self-exclusion is desired.
        """
        with self._lock:
            self._self_hwnds.add(hwnd)

    def unregister_self_hwnd(self, hwnd: int) -> None:
        with self._lock:
            self._self_hwnds.discard(hwnd)

    @property
    def self_hwnds(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._self_hwnds)

    def record(self, entry: MruEntry) -> None:
        """Push *entry* to the front, removing any prior occurrence of the same hwnd."""
        with self._lock:
            for i, existing in enumerate(self._buf):
                if existing.hwnd == entry.hwnd:
                    del self._buf[i]
                    break
            self._buf.appendleft(entry)

    def snapshot(self) -> list[MruEntry]:
        with self._lock:
            return list(self._buf)

    def top(
        self,
        n: int,
        predicate: Callable[[MruEntry], bool] | None = None,
    ) -> list[MruEntry]:
        if n <= 0:
            return []
        with self._lock:
            entries: Iterable[MruEntry] = list(self._buf)
        out: list[MruEntry] = []
        for entry in entries:
            if predicate is not None and not predicate(entry):
                continue
            out.append(entry)
            if len(out) >= n:
                break
        return out


# ---------------------------------------------------------------------------
# Win32 message-pump pump (Windows-only)
# ---------------------------------------------------------------------------


_HookInstaller = Callable[[Callable[[int], None]], Callable[[], None]]
_HwndResolver = Callable[[int], "MruEntry | None"]


class Win32MruPump:
    """Push foreground-change hwnds into an :class:`MruTracker`.

    On start, the pump spins up a dedicated daemon thread that installs a
    ``WinEventHook(EVENT_SYSTEM_FOREGROUND)`` and runs a Win32 message loop.
    Every foreground change resolves to an :class:`MruEntry` via *resolve_hwnd*
    and is recorded on the injected :class:`MruTracker`.

    The *install_hook* / *resolve_hwnd* dependencies are injected so unit
    tests can drive the pump without touching Win32 at all.
    """

    def __init__(
        self,
        tracker: MruTracker,
        install_hook: _HookInstaller | None = None,
        resolve_hwnd: _HwndResolver | None = None,
    ) -> None:
        self._tracker = tracker
        self._install_hook = install_hook or _default_install_hook
        self._resolve_hwnd = resolve_hwnd or _default_resolve_hwnd
        self._thread: threading.Thread | None = None
        self._uninstall: Callable[[], None] | None = None
        self._stopped = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopped.clear()
        # Drive installation on the caller thread when we have a fake;
        # the real Win32 path installs from inside the worker thread.
        if self._install_hook is _default_install_hook:
            self._thread = threading.Thread(
                target=self._run_win32, name="vc-mru-pump", daemon=True
            )
            self._thread.start()
        else:

            def _push(hwnd: int) -> None:
                entry = self._resolve_hwnd(hwnd)
                if entry is not None:
                    self._tracker.record(entry)

            self._uninstall = self._install_hook(_push)

    def stop(self) -> None:
        self._stopped.set()
        if self._uninstall is not None:
            try:
                self._uninstall()
            except Exception:
                logger.exception("Win32MruPump uninstall failed")
            self._uninstall = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run_win32(self) -> None:
        def _push(hwnd: int) -> None:
            entry = self._resolve_hwnd(hwnd)
            if entry is not None:
                self._tracker.record(entry)

        try:
            self._uninstall = self._install_hook(_push)
        except Exception:
            logger.exception("Win32MruPump: install_hook failed; pump inactive")
            return

        # Pump messages until stop is requested. The hook callback runs on this
        # thread, so the deque writes happen here.
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            msg = wintypes.MSG()
            while not self._stopped.is_set():
                # PeekMessage with PM_REMOVE so we can poll the stop event.
                if user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 0x0001):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    self._stopped.wait(0.05)
        except Exception:
            logger.exception("Win32MruPump message loop crashed")


# Default Win32 install_hook + resolve_hwnd ------------------------------------


def _default_install_hook(callback: Callable[[int], None]) -> Callable[[], None]:
    """Install ``EVENT_SYSTEM_FOREGROUND`` hook and return an uninstaller.

    Imported lazily so unit tests that inject fakes don't drag pywin32 in.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EVENT_SYSTEM_FOREGROUND = 0x0003
    WINEVENT_OUTOFCONTEXT = 0x0000
    WINEVENT_SKIPOWNPROCESS = 0x0002

    WinEventProcType = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        wintypes.LONG,
        wintypes.LONG,
        wintypes.DWORD,
        wintypes.DWORD,
    )

    def _proc(_hook, _event, hwnd, _id_obj, _id_child, _tid, _time) -> None:
        try:
            callback(int(hwnd))
        except Exception:
            logger.exception("MRU hook callback failed for hwnd=%s", hwnd)

    proc_ref = WinEventProcType(_proc)
    handle = user32.SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND,
        EVENT_SYSTEM_FOREGROUND,
        0,
        proc_ref,
        0,
        0,
        WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
    )
    if not handle:
        raise OSError("SetWinEventHook returned NULL")

    def _uninstall() -> None:
        user32.UnhookWinEvent(handle)

    # Keep a reference to proc_ref so Python doesn't GC the trampoline.
    _uninstall._proc_ref = proc_ref  # type: ignore[attr-defined]
    return _uninstall


def _default_resolve_hwnd(hwnd: int) -> MruEntry | None:
    """Resolve *hwnd* to an :class:`MruEntry`. Returns ``None`` on failure."""
    try:
        import win32gui
        import win32process
    except ImportError:
        return None

    try:
        title = win32gui.GetWindowText(hwnd) or ""
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return None

    proc_name = ""
    try:
        import psutil

        proc_name = psutil.Process(pid).name() if pid else ""
    except Exception:
        proc_name = ""

    if not title and not proc_name:
        return None
    return MruEntry(hwnd=int(hwnd), pid=int(pid or 0), proc_name=proc_name, title=title)
