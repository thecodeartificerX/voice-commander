"""Daemon-side restart helper.

Exiting the process with code ``EXIT_RESTART`` (75) signals the supervisor
to respawn us. We schedule the exit in a background thread so the calling
HTTP handler can return a clean response first.

If the daemon is running without a supervisor (``VC_SUPERVISED`` env var
unset), we refuse — exiting 75 with no parent to interpret it would just
kill the daemon with no replacement.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from ..supervisor.exit_codes import EXIT_RESTART

logger = logging.getLogger(__name__)


class RestartUnavailable(RuntimeError):
    """Raised when the daemon is asked to restart but has no supervisor."""


def request_restart(delay_s: float = 0.5) -> None:
    """Schedule a graceful exit with code 75 so the supervisor respawns us.

    The delay gives the calling HTTP handler time to return a response to
    the browser before the current process tears down.

    Raises ``RestartUnavailable`` immediately if no supervisor is present.
    """
    if os.environ.get("VC_SUPERVISED") != "1":
        raise RestartUnavailable(
            "Restart requires the supervisor. Launch with start.ps1 or "
            "voice-commander-supervisor."
        )

    def _do() -> None:
        time.sleep(delay_s)
        try:
            # Logger handlers may already be torn down in test contexts where
            # the calling thread mocks os._exit. Swallow the resulting
            # ValueError so the thread still hits the (mocked) exit path.
            logger.info("Exiting with code %d for supervisor restart", EXIT_RESTART)
        except ValueError:
            pass
        os._exit(EXIT_RESTART)

    threading.Thread(target=_do, daemon=True, name="daemon-restart").start()
