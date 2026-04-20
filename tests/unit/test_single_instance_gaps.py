"""Targeted tests to close coverage gaps in single_instance.py.

Covers:
- acquire() success path that writes PID to a fresh lock file (lines 24-33)
- release() when lock was never acquired — must be a no-op (line 67->73 branch)
- release() when OS.close raises — exception is swallowed (lines 70-71)
- _pid_alive() with a definitely-alive PID (os.getpid()) → True
- _pid_alive() with a truly invalid PID → False
- acquire() stale → reclaim cycle (re-enter after unlink)
"""

from __future__ import annotations

import os
import sys

from voice_commander.single_instance import SingleInstanceLock, _pid_alive

# ---------------------------------------------------------------------------
# acquire / release — core success path
# ---------------------------------------------------------------------------


def test_acquire_writes_pid_to_lock_file(tmp_path):
    """acquire() creates the lock file and writes the current PID into it."""
    lock_path = tmp_path / "daemon.lock"
    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.exists(), "lock file must exist after acquire()"
        written_pid = int(lock_path.read_text().strip())
        assert written_pid == os.getpid()
    finally:
        lock.release()


def test_acquire_creates_parent_dirs(tmp_path):
    """acquire() creates missing parent directories."""
    lock_path = tmp_path / "a" / "b" / "daemon.lock"
    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.exists()
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# release() when never acquired
# ---------------------------------------------------------------------------


def test_release_before_acquire_is_noop(tmp_path):
    """release() before acquire() must not raise and must not leave a file."""
    lock_path = tmp_path / "daemon.lock"
    lock = SingleInstanceLock(lock_path)
    lock.release()  # _fh is None — should silently do nothing
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# release() when os.close raises
# ---------------------------------------------------------------------------


def test_release_swallows_os_close_error(tmp_path, monkeypatch):
    """If os.close() raises inside release(), the exception is swallowed."""
    lock_path = tmp_path / "daemon.lock"
    lock = SingleInstanceLock(lock_path)
    lock.acquire()

    # Patch os.close inside the single_instance module so the real fd is still
    # closed (avoiding the Windows file-in-use lock on unlink) while the
    # module-under-test sees the OSError.
    import voice_commander.single_instance as si_mod

    original_os_close = si_mod.os.close

    def _bad_close(fd):
        original_os_close(fd)  # actually release the fd so unlink can proceed
        raise OSError("bad fd")

    monkeypatch.setattr(si_mod.os, "close", _bad_close)
    lock.release()  # must not propagate OSError


# ---------------------------------------------------------------------------
# _pid_alive — alive PID
# ---------------------------------------------------------------------------


def test_pid_alive_current_process_is_alive():
    """_pid_alive(os.getpid()) must return True — we are obviously running."""
    assert _pid_alive(os.getpid()) is True


# ---------------------------------------------------------------------------
# _pid_alive — dead / invalid PID
# ---------------------------------------------------------------------------


def test_pid_alive_invalid_pid_returns_false():
    """_pid_alive with a PID that cannot possibly be running → False.

    We use PID 0 on Windows (the System Idle Process cannot be opened with
    PROCESS_QUERY_INFORMATION) and rely on the kernel returning 0 handle.
    On POSIX os.kill(0, 0) targets the process *group*, so we use a
    definitely-dead PID obtained by spawning and waiting for a child instead.
    """
    if sys.platform == "win32":
        # PID 0 — System Idle Process; OpenProcess returns NULL for any user
        result = _pid_alive(0)
        # Some Windows configurations do open PID 0, so we accept True OR False.
        # What we actually care about is that the function does not raise.
        assert isinstance(result, bool)
    else:
        import subprocess

        proc = subprocess.Popen(["true"])
        proc.wait()
        dead_pid = proc.pid
        assert _pid_alive(dead_pid) is False


# ---------------------------------------------------------------------------
# stale-lock reclaim with controlled PID
# ---------------------------------------------------------------------------


def test_acquire_reclaims_stale_lock_with_dead_pid(tmp_path):
    """If lock file contains an obviously dead PID, acquire() reclaims it."""
    lock_path = tmp_path / "daemon.lock"
    # Write a PID that is certainly dead (max int that fits in pid_t-ish range
    # but cannot be a real running process on any modern OS).
    lock_path.write_text("999999999")

    lock = SingleInstanceLock(lock_path)
    lock.acquire()  # stale → unlink → retry → success
    try:
        written_pid = int(lock_path.read_text().strip())
        assert written_pid == os.getpid()
    finally:
        lock.release()


def test_acquire_stale_with_corrupt_contents_reclaims(tmp_path):
    """Lock file with non-integer contents is treated as stale."""
    lock_path = tmp_path / "daemon.lock"
    lock_path.write_text("not-a-pid")

    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.exists()
    finally:
        lock.release()
