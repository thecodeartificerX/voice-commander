from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path


class AlreadyRunning(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* is a running process, False if it is dead/invalid."""
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_INFORMATION = 0x0400
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        # handle == 0: process does not exist (or invalid PID)
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # process exists but we lack permission to signal it
            return True
        except OSError:
            return False


class SingleInstanceLock:
    """Cross-platform-ish lock file using exclusive-create + PID."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fh, str(os.getpid()).encode())
            self._fh = fh
        except FileExistsError:
            if self._stale():
                self._path.unlink(missing_ok=True)
                return self.acquire()
            raise AlreadyRunning(f"Another instance is running (lock: {self._path})") from None

    def _stale(self) -> bool:
        try:
            pid = int(self._path.read_text().strip() or "-1")
        except (OSError, ValueError):
            return True
        if pid <= 0:
            return True
        return not _pid_alive(pid)

    def release(self) -> None:
        if self._fh is not None:
            with contextlib.suppress(OSError):
                os.close(self._fh)
            self._fh = None
        self._path.unlink(missing_ok=True)
