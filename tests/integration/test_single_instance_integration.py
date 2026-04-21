"""Integration tests for the single-instance lock using real subprocesses.

These tests exercise the OS-level locking behaviour end-to-end:
  - Parent holds lock, child subprocess is blocked (AlreadyRunning).
  - Parent releases lock, child subprocess can then acquire it.
  - Crash recovery: process is killed, OS releases lock, new process acquires it.

All tests are Windows-only because the lock implementation under test uses
``msvcrt.locking``.  The skip decorator lives at the module level so the whole
file is skipped on non-Windows platforms.

Subprocess path injection
-------------------------
The worktree ``src/`` directory is injected into child processes via the
``VOICE_COMMANDER_SRC`` environment variable so that inline ``-c`` scripts
always import the *worktree* copy of ``single_instance.py`` rather than
whatever editable install happens to be on the system ``sys.path``.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only integration test",
)

# Absolute path to the worktree's src/ directory — two levels up from this
# file (tests/integration/ → tests/ → repo root) then into src/.
_WORKTREE_SRC = str(
    pathlib.Path(__file__).resolve().parent.parent.parent / "src"
)

# Prepend the worktree src onto the parent process path so imports in the
# test body also pick up the worktree implementation, not an installed copy.
if _WORKTREE_SRC not in sys.path:
    sys.path.insert(0, _WORKTREE_SRC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _child_env() -> dict[str, str]:
    """Return an environment dict that ensures children import the worktree src."""
    env = os.environ.copy()
    # Prepend worktree src to PYTHONPATH so it wins over any installed copy.
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _WORKTREE_SRC + (os.pathsep + existing if existing else "")
    return env


def _run_child(script: str, lock_path: str, *, timeout: int = 10) -> subprocess.CompletedProcess:
    """Run *script* in a fresh Python interpreter with the worktree src on PYTHONPATH.

    The child script receives the lock file path as the first positional
    argument (``sys.argv[1]``).
    """
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), lock_path],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_child_env(),
    )


def _start_child(script: str, lock_path: str) -> subprocess.Popen:
    """Launch *script* as a background subprocess and return the Popen object."""
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script), lock_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
        env=_child_env(),
    )


# ---------------------------------------------------------------------------
# Inline child scripts
# ---------------------------------------------------------------------------

# Child: try to acquire lock, print "OK" on success or "LOCKED" on AlreadyRunning.
_SCRIPT_TRY_ACQUIRE = """\
    import sys, pathlib
    from voice_commander.single_instance import SingleInstanceLock, AlreadyRunning
    lock = SingleInstanceLock(pathlib.Path(sys.argv[1]))
    try:
        lock.acquire()
        print("OK")
        sys.stdout.flush()
        lock.release()
    except AlreadyRunning:
        print("LOCKED")
        sys.stdout.flush()
"""

# Child: acquire lock, print "ACQUIRED\n", then block until stdin sends a line.
_SCRIPT_HOLD_LOCK = """\
    import sys, pathlib
    from voice_commander.single_instance import SingleInstanceLock
    lock = SingleInstanceLock(pathlib.Path(sys.argv[1]))
    lock.acquire()
    print("ACQUIRED")
    sys.stdout.flush()
    # Block until parent sends a line (or closes stdin).
    sys.stdin.readline()
    lock.release()
    print("RELEASED")
    sys.stdout.flush()
"""

# Child: acquire lock and then immediately exit normally (atexit releases it).
_SCRIPT_ACQUIRE_AND_EXIT = """\
    import sys, pathlib
    from voice_commander.single_instance import SingleInstanceLock
    lock = SingleInstanceLock(pathlib.Path(sys.argv[1]))
    lock.acquire()
    print("ACQUIRED")
    sys.stdout.flush()
    # Exit without calling lock.release() explicitly — atexit handler must fire.
"""

# Child: acquire lock and then spin forever (will be killed by parent).
_SCRIPT_ACQUIRE_AND_SPIN = """\
    import sys, pathlib, time
    from voice_commander.single_instance import SingleInstanceLock
    lock = SingleInstanceLock(pathlib.Path(sys.argv[1]))
    lock.acquire()
    print("ACQUIRED")
    sys.stdout.flush()
    while True:
        time.sleep(0.1)
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLockConflict:
    """Parent holds lock; child must see AlreadyRunning."""

    def test_child_blocked_while_parent_holds_lock(self, tmp_path):
        from voice_commander.single_instance import SingleInstanceLock

        lock_path = tmp_path / "test.lock"
        parent_lock = SingleInstanceLock(lock_path)
        parent_lock.acquire()
        try:
            result = _run_child(_SCRIPT_TRY_ACQUIRE, str(lock_path))
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "LOCKED", (
                f"Expected child to see LOCKED, got: {result.stdout!r}\nstderr: {result.stderr}"
            )
        finally:
            parent_lock.release()

    def test_child_succeeds_after_parent_releases(self, tmp_path):
        from voice_commander.single_instance import SingleInstanceLock

        lock_path = tmp_path / "test.lock"
        parent_lock = SingleInstanceLock(lock_path)
        parent_lock.acquire()
        parent_lock.release()

        result = _run_child(_SCRIPT_TRY_ACQUIRE, str(lock_path))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "OK", (
            f"Expected child to acquire lock after parent released, "
            f"got: {result.stdout!r}\nstderr: {result.stderr}"
        )


class TestLockHandoff:
    """Full handoff: child holds, parent waits, child releases, parent acquires."""

    def test_parent_blocked_then_succeeds_after_child_releases(self, tmp_path):
        from voice_commander.single_instance import AlreadyRunning, SingleInstanceLock

        lock_path = tmp_path / "test.lock"

        # Launch child that holds the lock until we signal it.
        child = _start_child(_SCRIPT_HOLD_LOCK, str(lock_path))
        try:
            # Wait for child to confirm it has the lock.
            line = child.stdout.readline()
            assert line.strip() == "ACQUIRED", (
                f"Child did not acquire lock: {line!r}\nstderr: {child.stderr.read()}"
            )

            # Parent must not be able to acquire while child holds it.
            parent_lock = SingleInstanceLock(lock_path)
            with pytest.raises(AlreadyRunning):
                parent_lock.acquire()

            # Signal child to release by sending a newline to its stdin.
            child.stdin.write("\n")
            child.stdin.flush()

            # Wait for child to confirm it released.
            release_line = child.stdout.readline()
            assert release_line.strip() == "RELEASED", (
                f"Child did not release lock cleanly: {release_line!r}"
            )
        finally:
            child.communicate(timeout=10)

        # After child released, parent should now succeed.
        parent_lock2 = SingleInstanceLock(lock_path)
        parent_lock2.acquire()
        parent_lock2.release()


class TestCrashRecovery:
    """OS releases advisory lock when a process is killed; next caller succeeds."""

    def test_lock_released_after_process_terminate(self, tmp_path):
        from voice_commander.single_instance import SingleInstanceLock

        lock_path = tmp_path / "test.lock"

        # Start child that spins forever holding the lock.
        child = _start_child(_SCRIPT_ACQUIRE_AND_SPIN, str(lock_path))

        # Wait for child to confirm lock acquisition.
        line = child.stdout.readline()
        assert line.strip() == "ACQUIRED", (
            f"Child did not acquire lock: {line!r}\nstderr: {child.stderr.read()}"
        )

        # Terminate the child — simulates a crash / SIGKILL scenario.
        child.terminate()
        child.wait(timeout=10)

        # Give the OS a moment to fully release the handle (usually instant on Windows).
        time.sleep(0.2)

        # Parent must now be able to acquire the lock.
        parent_lock = SingleInstanceLock(lock_path)
        parent_lock.acquire()
        parent_lock.release()

    def test_lock_released_after_process_kill(self, tmp_path):
        """Use kill() (TerminateProcess on Windows) for a harder crash."""
        from voice_commander.single_instance import SingleInstanceLock

        lock_path = tmp_path / "test.lock"
        child = _start_child(_SCRIPT_ACQUIRE_AND_SPIN, str(lock_path))

        line = child.stdout.readline()
        assert line.strip() == "ACQUIRED", (
            f"Child did not acquire lock: {line!r}\nstderr: {child.stderr.read()}"
        )

        child.kill()
        child.wait(timeout=10)

        time.sleep(0.2)

        parent_lock = SingleInstanceLock(lock_path)
        parent_lock.acquire()
        parent_lock.release()

    def test_clean_exit_releases_lock_via_atexit(self, tmp_path):
        """atexit handler must release the lock on normal process exit."""
        from voice_commander.single_instance import SingleInstanceLock

        lock_path = tmp_path / "test.lock"

        # Child acquires and exits normally; atexit calls release().
        result = _run_child(_SCRIPT_ACQUIRE_AND_EXIT, str(lock_path))
        assert result.returncode == 0, (
            f"Child exited non-zero\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The OS advisory lock must be gone.  A fresh acquire must succeed.
        parent_lock = SingleInstanceLock(lock_path)
        parent_lock.acquire()
        parent_lock.release()
