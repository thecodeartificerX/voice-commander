"""Cross-platform spawn / wait / terminate helpers.

Windows is the only target today, but POSIX paths are sketched so the door
isn't slammed shut. Children inherit the supervisor's stdout/stderr (so log
lines interleave naturally in the launching console). stdin is `DEVNULL`.

Windows job-object guarantee
----------------------------
On Windows every spawned child is assigned to a single process-wide Job
Object created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. When the
supervisor process dies for ANY reason (clean exit, KeyboardInterrupt,
hard kill from a parent shell, uv harness teardown), Windows closes the
Job handle and reaps every assigned child immediately. This is the only
reliable way to prevent orphan daemon/sprite processes when a launcher
chain (start.bat → powershell → uv → supervisor) collapses unexpectedly.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Windows Job Object — process-wide singleton.
# ---------------------------------------------------------------------------

_JOB_HANDLE: int | None = None  # Windows-only HANDLE


def _ensure_job_object() -> int | None:
    """Create (once) a Job Object configured to kill all children on close.

    Returns the job handle (an opaque Windows HANDLE int), or None on
    non-Windows or if the Win32 API is unavailable. Callers must assign
    each new child to the returned handle via ``_assign_to_job``.

    Idempotent — subsequent calls return the cached handle.
    """
    global _JOB_HANDLE
    if sys.platform != "win32":
        return None
    if _JOB_HANDLE is not None:
        return _JOB_HANDLE

    try:
        import win32job  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pywin32 not available; child cleanup on supervisor crash not guaranteed")
        return None

    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    _JOB_HANDLE = job
    logger.info("Created Job Object with KILL_ON_JOB_CLOSE for child supervision")
    return _JOB_HANDLE


def _assign_to_job(pid: int) -> None:
    """Assign *pid* to the supervisor's Job Object.

    On non-Windows or if pywin32 is unavailable, this is a no-op — the
    supervisor falls back to its explicit terminate() path on shutdown.
    """
    job = _ensure_job_object()
    if job is None:
        return
    try:
        import win32api
        import win32con
        import win32job
    except ImportError:
        return

    try:
        # PROCESS_TERMINATE | PROCESS_SET_QUOTA = required by AssignProcessToJobObject
        process_handle = win32api.OpenProcess(
            win32con.PROCESS_TERMINATE | win32con.PROCESS_SET_QUOTA, False, pid
        )
        win32job.AssignProcessToJobObject(job, process_handle)
        win32api.CloseHandle(process_handle)
        logger.debug("Assigned pid=%d to supervisor Job Object", pid)
    except Exception as exc:  # noqa: BLE001 — log and continue; not fatal
        logger.warning("Failed to assign pid=%d to Job Object: %s", pid, exc)


@dataclasses.dataclass(frozen=True)
class ChildHandle:
    """Opaque handle returned by :func:`spawn`."""

    name: str
    pid: int
    popen: subprocess.Popen[bytes]

    def poll(self) -> int | None:
        """Return the exit code if the child has exited, else ``None``."""
        return self.popen.poll()

    def wait(self) -> int:
        """Block until the child exits and return its exit code."""
        return self.popen.wait()


def spawn(
    argv: Sequence[str],
    *,
    name: str = "child",
    env_extra: dict[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> ChildHandle:
    """Spawn *argv* as a managed child."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)

    creationflags = 0
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP — lets us send CTRL_BREAK_EVENT during
        # shutdown without affecting the supervisor's own console group.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    logger.info("Spawning %s: %s", name, " ".join(argv))
    popen = subprocess.Popen(  # noqa: S603 — argv built by caller
        list(argv),
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    # Bind the child to the supervisor's Job Object so it dies with the
    # supervisor under any failure mode (clean shutdown, KeyboardInterrupt,
    # parent-shell hard kill, uv harness teardown). Best-effort — see
    # _ensure_job_object docstring for the no-op fallback.
    _assign_to_job(popen.pid)
    return ChildHandle(name=name, pid=popen.pid, popen=popen)


def terminate(handle: ChildHandle, *, grace_s: float) -> None:
    """Politely ask the child to exit; force-kill its process tree on timeout.

    On Windows: send ``CTRL_BREAK_EVENT`` (the child was spawned with
    ``CREATE_NEW_PROCESS_GROUP``), wait *grace_s*, then ``taskkill /T /F``
    if it's still alive. The ``/T`` walks the process tree so grandchildren
    (e.g. uv → python) die together.

    On POSIX: ``SIGTERM``, wait, ``SIGKILL``.
    """
    if (existing := handle.poll()) is not None:
        logger.debug("terminate(%s): already exited (code=%s)", handle.name, existing)
        return

    try:
        if sys.platform == "win32":
            os.kill(handle.pid, signal.CTRL_BREAK_EVENT)
        else:
            handle.popen.terminate()  # SIGTERM
    except (OSError, ProcessLookupError):
        logger.debug("terminate(%s): signal failed; child likely dead", handle.name)
        return

    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if (graceful := handle.poll()) is not None:
            logger.debug("terminate(%s): exited gracefully (code=%s)", handle.name, graceful)
            return
        time.sleep(0.05)

    logger.warning("terminate(%s): grace window elapsed; force-killing tree", handle.name)
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603,S607
            ["taskkill", "/PID", str(handle.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        with contextlib.suppress(OSError, ProcessLookupError):
            handle.popen.kill()

    try:
        handle.popen.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        logger.error("terminate(%s): force-kill did not reap; orphan possible", handle.name)
