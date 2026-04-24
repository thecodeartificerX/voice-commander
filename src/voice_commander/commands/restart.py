"""Helper for the POST /restart endpoint: spawn a fresh daemon, exit current.

Windows-specific. Uses ``subprocess.Popen`` with ``DETACHED_PROCESS`` so the
new process outlives the dying daemon. The web response is returned before
exit so the UI sees a clean 200 and can flip to a "restarting…" banner.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)


def schedule_restart(delay_s: float = 0.5) -> None:
    """Spawn a new daemon process, then signal the current one to exit.

    The delay gives the calling HTTP handler time to return a response to
    the browser before the current process tears down.
    """

    def _do() -> None:
        import time

        time.sleep(delay_s)
        try:
            _spawn_new()
        except Exception:
            logger.exception("Failed to spawn replacement daemon")
            return
        logger.info("Replacement daemon spawned; exiting current process")
        os._exit(0)

    threading.Thread(target=_do, daemon=True, name="daemon-restart").start()


def _spawn_new() -> None:
    """Spawn a detached ``python -m voice_commander`` in the repo root."""
    argv = [sys.executable, "-m", "voice_commander"]
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS (0x00000008) | CREATE_NEW_PROCESS_GROUP (0x00000200)
        creationflags = 0x00000008 | 0x00000200
    subprocess.Popen(  # noqa: S603 — argv built from sys.executable + literal module name
        argv,
        cwd=os.getcwd(),
        creationflags=creationflags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
