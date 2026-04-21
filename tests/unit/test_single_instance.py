"""Comprehensive unit tests for voice_commander.single_instance.

Covers:
- _pid_alive() on Windows (ctypes mocking) and POSIX (os.kill path)
- SingleInstanceLock: acquire, release, stale reclaim, corrupt file reclaim
- Lock file format: PID:GUID
- Parent directory creation
- AlreadyRunning raised with PID info in message
- atexit and signal handler registration
- Signal handler chains to previous handler
- _stale() defence-in-depth method
- _read_pid_from_fd static method
"""

from __future__ import annotations

import atexit
import os
import re
import signal
import sys
from unittest.mock import MagicMock, patch

import pytest

from voice_commander.single_instance import (
    AlreadyRunning,
    SingleInstanceLock,
    _pid_alive,
)

# ===========================================================================
# Helpers
# ===========================================================================

_LOCK_RE = re.compile(r"^\d+:[0-9a-f]{32}$")


def _read_via_fd(lock: SingleInstanceLock) -> str:
    """Read lock file content through the already-open fd (Windows-safe)."""
    fh = lock._fh
    assert fh is not None, "lock must be acquired before reading via fd"
    os.lseek(fh, 0, os.SEEK_SET)
    data = os.read(fh, 512).decode().strip()
    return data


# ===========================================================================
# _pid_alive — platform-neutral
# ===========================================================================


class TestPidAliveCurrentProcess:
    def test_current_process_is_alive(self):
        """os.getpid() must always return True — we are obviously running."""
        assert _pid_alive(os.getpid()) is True


# ===========================================================================
# _pid_alive — POSIX path (os.kill)
# ===========================================================================


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only path")
class TestPidAlivePosix:
    def test_dead_pid_returns_false(self):
        """Spawn a subprocess, wait for it, then confirm its PID reads False."""
        import subprocess

        proc = subprocess.Popen(["true"])
        proc.wait()
        assert _pid_alive(proc.pid) is False

    def test_permission_error_treated_as_alive(self, monkeypatch):
        """PermissionError from os.kill means process exists but we can't signal."""
        import voice_commander.single_instance as si

        monkeypatch.setattr(
            si.os,
            "kill",
            lambda pid, sig: (_ for _ in ()).throw(PermissionError()),
        )
        assert _pid_alive(9999) is True

    def test_process_lookup_error_returns_false(self, monkeypatch):
        """ProcessLookupError from os.kill means no such process."""
        import voice_commander.single_instance as si

        monkeypatch.setattr(
            si.os,
            "kill",
            lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()),
        )
        assert _pid_alive(9999) is False

    def test_generic_oserror_returns_false(self, monkeypatch):
        """Generic OSError from os.kill → False."""
        import voice_commander.single_instance as si

        monkeypatch.setattr(
            si.os,
            "kill",
            lambda pid, sig: (_ for _ in ()).throw(OSError("other")),
        )
        assert _pid_alive(9999) is False


# ===========================================================================
# _pid_alive — Windows path (ctypes mocking)
# ===========================================================================


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path")
class TestPidAliveWindows:
    """Test the ctypes-based Windows implementation by mocking kernel32.

    Notes
    -----
    The module calls ``ctypes.windll.kernel32`` at function *call time*, not at
    import time.  We patch ``ctypes.windll`` for the duration of each test.
    The mock must honour the ``_GetExitCodeProcess.side_effect`` contract: the
    function receives a ``ctypes.byref`` object so we write through its
    ``._obj`` attribute.
    """

    @staticmethod
    def _make_kernel32(open_return: int, exit_code: int, get_exit_ok: bool = True):
        kernel32 = MagicMock()
        kernel32.OpenProcess.return_value = open_return

        def _get_exit_code(handle, lp_exit_code):
            if get_exit_ok:
                lp_exit_code._obj.value = exit_code
            return int(get_exit_ok)

        kernel32.GetExitCodeProcess.side_effect = _get_exit_code
        kernel32.CloseHandle.return_value = 1
        return kernel32

    def test_open_process_returns_null_means_dead(self):
        """OpenProcess returning 0/NULL → _pid_alive returns False."""
        kernel32 = self._make_kernel32(open_return=0, exit_code=0)
        with patch("ctypes.windll") as mock_windll:
            mock_windll.kernel32 = kernel32
            result = _pid_alive(1234)
        assert result is False

    def test_open_process_success_still_active_means_alive(self):
        """Valid handle + exit_code 259 (STILL_ACTIVE) → True."""
        kernel32 = self._make_kernel32(open_return=0xDEADBEEF, exit_code=259)
        with patch("ctypes.windll") as mock_windll:
            mock_windll.kernel32 = kernel32
            result = _pid_alive(1234)
        assert result is True

    def test_open_process_success_exit_code_zero_means_dead(self):
        """Valid handle + exit_code 0 (process exited) → False."""
        kernel32 = self._make_kernel32(open_return=0xDEADBEEF, exit_code=0)
        with patch("ctypes.windll") as mock_windll:
            mock_windll.kernel32 = kernel32
            result = _pid_alive(1234)
        assert result is False

    def test_get_exit_code_failure_means_dead(self):
        """GetExitCodeProcess returning BOOL 0 (failure) → assume dead → False."""
        kernel32 = self._make_kernel32(open_return=0xDEADBEEF, exit_code=0, get_exit_ok=False)
        with patch("ctypes.windll") as mock_windll:
            mock_windll.kernel32 = kernel32
            result = _pid_alive(1234)
        assert result is False


# ===========================================================================
# SingleInstanceLock — core acquire / release
# ===========================================================================


class TestSingleInstanceLockAcquire:
    def test_first_acquire_succeeds(self, tmp_path):
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()
        lock.release()

    def test_lock_file_exists_after_acquire(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        try:
            assert lock_path.exists(), "lock file must exist after acquire()"
        finally:
            lock.release()

    def test_lock_file_format_is_pid_colon_guid(self, tmp_path):
        """Lock file content must match '<pid>:<32-hex-char-guid>'.

        Read through the already-open fd to avoid Windows sharing violations.
        """
        lock_path = tmp_path / "daemon.lock"
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        try:
            content = _read_via_fd(lock)
            assert _LOCK_RE.match(content), (
                f"Lock file content {content!r} does not match PID:GUID format"
            )
            pid_in_file = int(content.split(":")[0])
            assert pid_in_file == os.getpid()
        finally:
            lock.release()

    def test_guid_portion_is_32_hex_chars(self, tmp_path):
        """GUID part must be exactly 32 lowercase hex characters."""
        lock_path = tmp_path / "daemon.lock"
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        try:
            content = _read_via_fd(lock)
            guid = content.split(":")[1]
            assert len(guid) == 32
            assert all(c in "0123456789abcdef" for c in guid)
        finally:
            lock.release()

    def test_acquire_creates_parent_directories(self, tmp_path):
        lock_path = tmp_path / "a" / "b" / "c" / "daemon.lock"
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        try:
            assert lock_path.exists()
        finally:
            lock.release()


class TestSingleInstanceLockRelease:
    def test_release_removes_lock_file(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        lock.release()
        assert not lock_path.exists()

    def test_release_sets_fh_to_none(self, tmp_path):
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()
        assert lock._fh is not None
        lock.release()
        assert lock._fh is None

    def test_release_before_acquire_is_noop(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        lock = SingleInstanceLock(lock_path)
        lock.release()  # must not raise
        assert not lock_path.exists()

    def test_release_swallows_os_close_error(self, tmp_path, monkeypatch):
        """release() must not propagate OSError from os.close()."""
        lock_path = tmp_path / "daemon.lock"
        lock = SingleInstanceLock(lock_path)
        lock.acquire()

        import voice_commander.single_instance as si_mod

        original_close = si_mod.os.close

        def _bad_close(fd):
            original_close(fd)  # actually release the fd so unlink can proceed
            raise OSError("simulated bad fd")

        monkeypatch.setattr(si_mod.os, "close", _bad_close)
        lock.release()  # must not raise

    def test_double_release_is_safe(self, tmp_path):
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()
        lock.release()
        lock.release()  # second call must not raise


# ===========================================================================
# SingleInstanceLock — AlreadyRunning conflict
# ===========================================================================


class TestSingleInstanceLockConflict:
    def test_second_acquire_raises_already_running(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        a = SingleInstanceLock(lock_path)
        a.acquire()
        b = SingleInstanceLock(lock_path)
        try:
            with pytest.raises(AlreadyRunning):
                b.acquire()
        finally:
            a.release()

    def test_already_running_message_format(self, tmp_path):
        """AlreadyRunning message must contain 'pid=' and the lock path."""
        lock_path = tmp_path / "daemon.lock"
        a = SingleInstanceLock(lock_path)
        a.acquire()
        b = SingleInstanceLock(lock_path)
        try:
            with pytest.raises(AlreadyRunning, match=r"pid=") as exc_info:
                b.acquire()
            assert "lock=" in str(exc_info.value)
        finally:
            a.release()

    def test_already_running_message_contains_lock_path(self, tmp_path):
        """AlreadyRunning message must mention the lock file path."""
        lock_path = tmp_path / "daemon.lock"
        a = SingleInstanceLock(lock_path)
        a.acquire()
        b = SingleInstanceLock(lock_path)
        try:
            with pytest.raises(AlreadyRunning, match="lock="):
                b.acquire()
        finally:
            a.release()

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=("Windows msvcrt.locking prevents cross-fd reads; PID is -1 on conflict"),
    )
    def test_already_running_message_contains_pid_posix(self, tmp_path):
        """On POSIX, AlreadyRunning message includes the actual running PID."""
        lock_path = tmp_path / "daemon.lock"
        a = SingleInstanceLock(lock_path)
        a.acquire()
        b = SingleInstanceLock(lock_path)
        try:
            with pytest.raises(AlreadyRunning, match=rf"pid={os.getpid()}"):
                b.acquire()
        finally:
            a.release()

    def test_acquire_succeeds_after_previous_holder_releases(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        a = SingleInstanceLock(lock_path)
        a.acquire()
        b = SingleInstanceLock(lock_path)
        with pytest.raises(AlreadyRunning):
            b.acquire()
        a.release()
        b.acquire()  # now must succeed
        b.release()


# ===========================================================================
# SingleInstanceLock — stale lock reclaim
# ===========================================================================


class TestSingleInstanceLockStale:
    def test_stale_lock_file_without_os_lock_is_reclaimed(self, tmp_path):
        """A lock file with a dead PID (no OS lock held) is overwritten."""
        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text("999999999:deadbeefdeadbeefdeadbeefdeadbeef")
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        try:
            # Read via the open fd — Windows-safe
            content = _read_via_fd(lock)
            assert _LOCK_RE.match(content), (
                f"Reclaimed lock content {content!r} doesn't match PID:GUID format"
            )
            assert content.split(":")[0] == str(os.getpid())
        finally:
            lock.release()

    def test_stale_bare_pid_format_reclaimed(self, tmp_path):
        """Old-format lock file (bare PID, no GUID) is treated as stale."""
        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text("999999999")
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        try:
            content = _read_via_fd(lock)
            assert str(os.getpid()) in content
        finally:
            lock.release()

    def test_corrupt_lock_file_is_reclaimed(self, tmp_path):
        """Lock file with non-parseable content is treated as stale."""
        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text("not-a-pid-at-all!!")
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        try:
            assert lock_path.exists()
        finally:
            lock.release()

    def test_empty_lock_file_is_reclaimed(self, tmp_path):
        """Empty lock file (e.g. truncated on crash) is treated as stale."""
        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text("")
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        try:
            assert lock_path.exists()
        finally:
            lock.release()


# ===========================================================================
# SingleInstanceLock._stale() — defence-in-depth method
# ===========================================================================


class TestSingleInstanceLockStaleMethod:
    def test_stale_returns_true_for_dead_pid(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text("999999999:fakeguidfakeguidfakeguidfakeguid")
        lock = SingleInstanceLock(lock_path)
        assert lock._stale() is True

    def test_stale_returns_false_for_current_pid(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text(f"{os.getpid()}:fakeguidfakeguidfakeguidfakeg")
        lock = SingleInstanceLock(lock_path)
        assert lock._stale() is False

    def test_stale_returns_true_for_missing_file(self, tmp_path):
        lock_path = tmp_path / "nonexistent.lock"
        lock = SingleInstanceLock(lock_path)
        assert lock._stale() is True

    def test_stale_returns_true_for_corrupt_content(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text("garbage")
        lock = SingleInstanceLock(lock_path)
        assert lock._stale() is True

    def test_stale_returns_true_for_zero_pid(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text("0:someguid")
        lock = SingleInstanceLock(lock_path)
        assert lock._stale() is True

    def test_stale_returns_true_for_negative_pid(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text("-1:someguid")
        lock = SingleInstanceLock(lock_path)
        assert lock._stale() is True


# ===========================================================================
# SingleInstanceLock._read_pid_from_fd — static method
# ===========================================================================


class TestReadPidFromFd:
    def test_reads_pid_from_pid_colon_guid(self, tmp_path):
        """Standard PID:GUID format parses to correct PID."""
        lock_path = tmp_path / "test.lock"
        lock_path.write_bytes(b"1234:abc123def456abc123def456abc123de")
        fh = os.open(str(lock_path), os.O_RDWR)
        try:
            pid = SingleInstanceLock._read_pid_from_fd(fh)
        finally:
            os.close(fh)
        assert pid == 1234

    def test_reads_pid_from_bare_pid(self, tmp_path):
        """Bare PID (old format without GUID) still parses correctly."""
        lock_path = tmp_path / "test.lock"
        lock_path.write_bytes(b"5678")
        fh = os.open(str(lock_path), os.O_RDWR)
        try:
            pid = SingleInstanceLock._read_pid_from_fd(fh)
        finally:
            os.close(fh)
        assert pid == 5678

    def test_returns_minus_one_for_corrupt_content(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        lock_path.write_bytes(b"not-a-number:abc")
        fh = os.open(str(lock_path), os.O_RDWR)
        try:
            pid = SingleInstanceLock._read_pid_from_fd(fh)
        finally:
            os.close(fh)
        assert pid == -1

    def test_returns_minus_one_for_empty_file(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        lock_path.write_bytes(b"")
        fh = os.open(str(lock_path), os.O_RDWR)
        try:
            pid = SingleInstanceLock._read_pid_from_fd(fh)
        finally:
            os.close(fh)
        assert pid == -1


# ===========================================================================
# atexit and signal handler registration
# ===========================================================================


class TestCleanupRegistration:
    def test_atexit_registered_after_acquire(self, tmp_path, monkeypatch):
        """atexit.register must be called with self.release after acquire."""
        registered = []
        monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))

        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()
        try:
            assert any(fn == lock.release for fn in registered), (
                "lock.release was not registered with atexit"
            )
        finally:
            lock.release()

    def test_atexit_not_double_registered(self, tmp_path, monkeypatch):
        """atexit.register is called at most once even if acquire is called once."""
        release_reg_count = [0]
        original_register = atexit.register

        def counting_register(fn):
            # Count only calls registering a release-like callable
            if hasattr(fn, "__func__") or "release" in getattr(fn, "__name__", ""):
                release_reg_count[0] += 1
            original_register(fn)

        monkeypatch.setattr(atexit, "register", counting_register)

        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()
        try:
            assert release_reg_count[0] == 1
        finally:
            lock.release()

    def test_sigint_handler_set_after_acquire(self, tmp_path):
        """After acquire(), SIGINT handler must be replaced with _signal_handler."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        prev_handler = signal.getsignal(signal.SIGINT)
        lock.acquire()
        try:
            current = signal.getsignal(signal.SIGINT)
            assert current == lock._signal_handler
        finally:
            signal.signal(signal.SIGINT, prev_handler)
            lock.release()

    def test_sigterm_handler_set_after_acquire(self, tmp_path):
        """After acquire(), SIGTERM handler must be replaced with _signal_handler."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        prev_handler = signal.getsignal(signal.SIGTERM)
        lock.acquire()
        try:
            current = signal.getsignal(signal.SIGTERM)
            assert current == lock._signal_handler
        finally:
            signal.signal(signal.SIGTERM, prev_handler)
            lock.release()

    @pytest.mark.skipif(sys.platform != "win32", reason="SIGBREAK is Windows-only")
    def test_sigbreak_handler_set_after_acquire_on_windows(self, tmp_path):
        """After acquire() on Windows, SIGBREAK handler is also set."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        prev_handler = signal.getsignal(signal.SIGBREAK)  # type: ignore[attr-defined]
        lock.acquire()
        try:
            current = signal.getsignal(signal.SIGBREAK)  # type: ignore[attr-defined]
            assert current == lock._signal_handler
        finally:
            signal.signal(signal.SIGBREAK, prev_handler)  # type: ignore[attr-defined]
            lock.release()

    @pytest.mark.skipif(sys.platform == "win32", reason="SIGBREAK not on POSIX")
    def test_sigbreak_not_in_cleanup_signals_on_posix(self):
        """On POSIX, _cleanup_signals() must contain exactly SIGINT + SIGTERM."""
        sigs = SingleInstanceLock._cleanup_signals()
        assert signal.SIGINT in sigs
        assert signal.SIGTERM in sigs
        assert len(sigs) == 2

    def test_previous_sigint_handler_stored(self, tmp_path):
        """The handler that was current at acquire time is stored for chaining."""
        # Record whatever SIGINT is pointing to right now, then set our sentinel.
        # The lock must capture exactly the sentinel (what was set immediately before acquire).
        sentinel = MagicMock()
        signal.signal(signal.SIGINT, sentinel)
        prev_handler = signal.getsignal(signal.SIGINT)  # should be sentinel
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()
        try:
            stored = lock._prev_handlers.get(signal.SIGINT)
            assert stored is prev_handler, f"Expected prev handler {prev_handler!r}, got {stored!r}"
        finally:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            lock.release()


# ===========================================================================
# Signal handler behaviour
# ===========================================================================


class TestSignalHandler:
    def test_signal_handler_calls_release(self, tmp_path):
        """Invoking _signal_handler must call release()."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()

        released = []
        original_release = lock.release.__func__  # unbound

        def tracking_release():
            released.append(True)
            original_release(lock)

        lock.release = tracking_release  # type: ignore[method-assign]

        # Provide a no-op prev handler so signal handler doesn't re-raise.
        lock._prev_handlers[signal.SIGINT] = lambda signum, frame: None

        lock._signal_handler(signal.SIGINT, None)
        assert released, "release() was not called by _signal_handler"

    def test_signal_handler_chains_to_callable_prev_handler(self, tmp_path):
        """_signal_handler must invoke a callable previous handler after release."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()

        chained_calls: list[tuple[int, object]] = []

        def prev(signum: int, frame: object) -> None:
            chained_calls.append((signum, frame))

        # Fully release the lock first so signal handler's release() is a no-op.
        lock.release()

        # Re-plant handlers dict with our chain target.
        lock._prev_handlers[signal.SIGTERM] = prev

        lock._signal_handler(signal.SIGTERM, None)
        assert chained_calls == [(signal.SIGTERM, None)]

    def test_signal_handler_restores_sig_dfl_and_reraised(self, tmp_path, monkeypatch):
        """When prev handler is SIG_DFL, signal is reset to SIG_DFL then re-sent."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()
        lock.release()  # avoid Windows unlink issues inside the handler

        lock._prev_handlers[signal.SIGTERM] = signal.SIG_DFL

        killed_with: list[tuple[int, int]] = []
        import voice_commander.single_instance as si_mod

        monkeypatch.setattr(signal, "signal", lambda sig, handler: None)
        monkeypatch.setattr(si_mod.os, "kill", lambda pid, sig: killed_with.append((pid, sig)))

        lock._signal_handler(signal.SIGTERM, None)
        assert (os.getpid(), signal.SIGTERM) in killed_with

    def test_signal_handler_ignores_none_prev_handler(self, tmp_path):
        """When prev handler is None (not set), _signal_handler must not raise."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()
        lock.release()  # fully release so handler body is clean

        lock._prev_handlers[signal.SIGINT] = None
        lock._signal_handler(signal.SIGINT, None)  # must not raise

    def test_signal_handler_survives_release_exception(self, tmp_path):
        """If release() raises, handler must still chain to prev handler."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()

        chained_calls: list[tuple[int, object]] = []

        def prev(signum: int, frame: object) -> None:
            chained_calls.append((signum, frame))

        def bad_release() -> None:
            raise TypeError("internal state corrupted")

        lock.release()  # clean up real lock first
        lock.release = bad_release  # type: ignore[method-assign]
        lock._prev_handlers[signal.SIGINT] = prev

        lock._signal_handler(signal.SIGINT, None)
        assert chained_calls == [(signal.SIGINT, None)]

    def test_signal_handler_survives_prev_handler_exception(self, tmp_path, monkeypatch):
        """If prev handler raises, handler must fall through to SIG_DFL re-raise."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        lock.acquire()
        lock.release()

        def bad_prev(signum: int, frame: object) -> None:
            raise RuntimeError("prev handler exploded")

        lock._prev_handlers[signal.SIGTERM] = bad_prev

        killed_with: list[tuple[int, int]] = []
        import voice_commander.single_instance as si_mod

        monkeypatch.setattr(signal, "signal", lambda sig, handler: None)
        monkeypatch.setattr(si_mod.os, "kill", lambda pid, sig: killed_with.append((pid, sig)))

        lock._signal_handler(signal.SIGTERM, None)
        assert (os.getpid(), signal.SIGTERM) in killed_with


# ===========================================================================
# _cleanup_signals() — static method
# ===========================================================================


class TestCleanupSignals:
    def test_always_includes_sigint_and_sigterm(self):
        sigs = SingleInstanceLock._cleanup_signals()
        assert signal.SIGINT in sigs
        assert signal.SIGTERM in sigs

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_includes_sigbreak_on_windows(self):
        sigs = SingleInstanceLock._cleanup_signals()
        assert signal.SIGBREAK in sigs  # type: ignore[attr-defined]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_does_not_include_sigbreak_on_posix(self):
        sigs = SingleInstanceLock._cleanup_signals()
        assert len(sigs) == 2, f"Expected exactly SIGINT+SIGTERM on POSIX, got {sigs}"


# ===========================================================================
# Context-manager / edge cases
# ===========================================================================


class TestEdgeCases:
    def test_lock_instance_path_attribute(self, tmp_path):
        lock_path = tmp_path / "daemon.lock"
        lock = SingleInstanceLock(lock_path)
        assert lock._path == lock_path

    def test_guid_is_set_after_acquire(self, tmp_path):
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        assert lock._guid == ""
        lock.acquire()
        try:
            assert len(lock._guid) == 32
        finally:
            lock.release()

    def test_guid_differs_across_separate_acquisitions(self, tmp_path):
        """Two distinct lock acquisitions produce two different GUIDs."""
        lock_path = tmp_path / "daemon.lock"
        lock1 = SingleInstanceLock(lock_path)
        lock1.acquire()
        guid1 = lock1._guid
        lock1.release()

        lock2 = SingleInstanceLock(lock_path)
        lock2.acquire()
        guid2 = lock2._guid
        lock2.release()

        assert guid1 != guid2

    def test_lock_in_deeply_nested_path(self, tmp_path):
        deep = tmp_path / "x" / "y" / "z" / "w" / "v" / "daemon.lock"
        lock = SingleInstanceLock(deep)
        lock.acquire()
        try:
            assert deep.exists()
        finally:
            lock.release()

    def test_fh_attribute_is_none_before_acquire(self, tmp_path):
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        assert lock._fh is None

    def test_guid_attribute_is_empty_string_before_acquire(self, tmp_path):
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        assert lock._guid == ""

    def test_atexit_registered_flag_prevents_re_registration(self, tmp_path, monkeypatch):
        """_atexit_registered flag is set after first acquire."""
        lock = SingleInstanceLock(tmp_path / "daemon.lock")
        assert lock._atexit_registered is False
        lock.acquire()
        try:
            assert lock._atexit_registered is True
        finally:
            lock.release()
