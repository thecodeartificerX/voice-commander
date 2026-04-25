"""Supervisor restart loop.

Pure logic, no subprocess calls. Tests inject ``sprite`` and
``daemon_factory`` mocks directly. The CLI wires real implementations
in ``cli.py``.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from .exit_codes import EXIT_CLEAN, EXIT_RESTART

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_S = 5.0


class _ChildProto(Protocol):
    def wait(self) -> int: ...
    def terminate(self, *, grace_s: float) -> None: ...


class _SpriteProto(Protocol):
    def terminate(self, *, grace_s: float) -> None: ...


def run(
    *,
    sprite: _SpriteProto,
    daemon_factory: Callable[[], _ChildProto],
) -> int:
    """Spawn the daemon in a loop, branching on its exit code.

    Returns the supervisor's own exit code:

    - 0 — daemon exited cleanly.
    - <code> — daemon crashed with that non-zero, non-75 code.

    Sprite is terminated once on the way out.
    """
    try:
        while True:
            daemon = daemon_factory()
            try:
                code = daemon.wait()
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt; terminating daemon")
                daemon.terminate(grace_s=SHUTDOWN_GRACE_S)
                raise

            if code == EXIT_RESTART:
                logger.info("Daemon requested restart (code=%d); respawning", code)
                continue
            if code == EXIT_CLEAN:
                logger.info("Daemon exited cleanly (code=%d)", code)
                return 0
            logger.error("Daemon crashed (code=%d); supervisor exiting", code)
            return code
    finally:
        sprite.terminate(grace_s=SHUTDOWN_GRACE_S)
