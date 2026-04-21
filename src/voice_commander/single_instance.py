"""Single-instance lock for the voice-commander daemon.

Uses OS-level file locking (``msvcrt.locking`` on Windows, ``fcntl.flock`` on
POSIX) as the **primary** exclusion mechanism.  The OS automatically releases
the lock when the owning process exits — even on crash or SIGKILL — so
stale-lock recovery is automatic; no PID-alive polling required for the happy
path.

A process GUID is written alongside the PID for diagnostics and as
defence-in-depth against PID reuse in the ``_stale()`` fallback path.

Bug fixed in this rewrite (2026-04-21):
    ``ctypes.windll.kernel32.OpenProcess`` default ``restype`` is ``c_int``
    (32-bit).  On 64-bit Windows, ``HANDLE`` is a 64-bit pointer — the
    truncation produced bogus truthy values for dead PIDs, so ``_stale()``
    never reclaimed the lock file after a crash.  All ctypes bindings now
    declare explicit ``restype`` and ``argtypes``.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import signal
import sys
import uuid
from pathlib import Path
from typing import Any


class AlreadyRunning(Exception):
    """Raised when another daemon instance holds the lock."""


# ---------------------------------------------------------------------------
# PID-alive check with correct ctypes bindings
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    """Return *True* if *pid* is a running process, *False* otherwise.

    On Windows every ctypes call declares explicit ``restype`` / ``argtypes``
    so that 64-bit ``HANDLE`` values are not truncated to 32-bit ``c_int``.
    After ``OpenProcess`` succeeds we also call ``GetExitCodeProcess`` —
    a handle to a zombie process is truthy but exit-code != ``STILL_ACTIVE``.
    """
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]

        _OpenProcess = kernel32.OpenProcess
        _OpenProcess.restype = ctypes.c_void_p
        _OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]

        _GetExitCodeProcess = kernel32.GetExitCodeProcess
        _GetExitCodeProcess.restype = ctypes.c_int  # BOOL
        _GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]

        _CloseHandle = kernel32.CloseHandle
        _CloseHandle.restype = ctypes.c_int
        _CloseHandle.argtypes = [ctypes.c_void_p]

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        handle = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False

        try:
            exit_code = ctypes.c_uint32()
            if _GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            # GetExitCodeProcess failed — assume dead
            return False
        finally:
            _CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we lack permission to signal it.
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# OS-level file locking helpers
# ---------------------------------------------------------------------------

def _os_lock(fh: int) -> None:
    """Acquire a non-blocking exclusive lock on file descriptor *fh*.

    Raises ``OSError`` if the lock is held by another process.
    """
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fh, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(fh: int) -> None:
    """Release the lock acquired by :func:`_os_lock`."""
    if sys.platform == "win32":
        import msvcrt

        try:
            os.lseek(fh, 0, os.SEEK_SET)
            msvcrt.locking(fh, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # already unlocked or fd invalid
    else:
        import contextlib
        import fcntl

        with contextlib.suppress(OSError):
            fcntl.flock(fh, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# SingleInstanceLock
# ---------------------------------------------------------------------------

class SingleInstanceLock:
    """Cross-platform single-instance lock using OS-level file locking + PID.

    The OS automatically releases the advisory lock when the process exits
    (even on crash), so stale-lock recovery is automatic.  A PID and GUID
    are written to the lock file for diagnostics and defence-in-depth.

    Cleanup handlers (``atexit`` + ``SIGINT``/``SIGTERM``/``SIGBREAK``) are
    registered on :meth:`acquire` so the lock file is removed on clean exit.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: int | None = None
        self._guid: str = ""
        self._atexit_registered: bool = False
        self._prev_handlers: dict[int, Any] = {}

    # -- public API --------------------------------------------------------

    def acquire(self) -> None:
        """Acquire the lock.  Raises :class:`AlreadyRunning` on conflict."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        fh = os.open(self._path, os.O_CREAT | os.O_RDWR)

        try:
            _os_lock(fh)
        except OSError:
            # Lock held by another process.
            pid = self._read_pid_from_fd(fh)
            os.close(fh)
            raise AlreadyRunning(
                f"Another instance is running (pid={pid}, lock={self._path})"
            ) from None

        # Got lock — write PID:GUID.
        self._guid = uuid.uuid4().hex
        os.ftruncate(fh, 0)
        os.lseek(fh, 0, os.SEEK_SET)
        os.write(fh, f"{os.getpid()}:{self._guid}".encode())
        os.fsync(fh)
        self._fh = fh

        self._register_cleanup()

    def release(self) -> None:
        """Release the lock and remove the lock file."""
        if self._fh is not None:
            with contextlib.suppress(OSError):
                _os_unlock(self._fh)
            with contextlib.suppress(OSError):
                os.close(self._fh)
            self._fh = None
        self._path.unlink(missing_ok=True)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _read_pid_from_fd(fh: int) -> int:
        """Read the PID from a lock-file fd.  Returns ``-1`` on failure."""
        try:
            os.lseek(fh, 0, os.SEEK_SET)
            data = os.read(fh, 256).decode().strip()
            return int(data.split(":")[0])
        except (OSError, ValueError, IndexError):
            return -1

    def _stale(self) -> bool:
        """Fallback stale-PID check (defence-in-depth).

        Under normal operation OS-level locking makes this unnecessary —
        this path is only hit if the OS lock could not be acquired AND
        the caller wants to decide based on PID liveness.
        """
        try:
            data = self._path.read_text().strip()
            parts = data.split(":")
            pid = int(parts[0])
        except (OSError, ValueError, IndexError):
            return True
        if pid <= 0:
            return True
        return not _pid_alive(pid)

    # -- cleanup handlers --------------------------------------------------

    def _register_cleanup(self) -> None:
        if not self._atexit_registered:
            atexit.register(self.release)
            self._atexit_registered = True

        for sig in self._cleanup_signals():
            self._prev_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, self._signal_handler)

    @staticmethod
    def _cleanup_signals() -> list[int]:
        sigs: list[int] = [signal.SIGINT, signal.SIGTERM]
        if sys.platform == "win32":
            sigs.append(signal.SIGBREAK)  # type: ignore[attr-defined,unused-ignore]
        return sigs

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Release lock, then chain to the previous handler."""
        self.release()

        prev = self._prev_handlers.get(signum)
        if callable(prev):
            prev(signum, frame)
        elif prev == signal.SIG_DFL:
            # Re-raise with default disposition.
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
