"""Cross-platform spawn / wait / terminate helpers.

Windows is the only target today, but POSIX paths are sketched so the door
isn't slammed shut. Children inherit the supervisor's stdout/stderr (so log
lines interleave naturally in the launching console). stdin is `DEVNULL`.
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
