"""Sprite child management.

The sprite is best-effort: if it fails to spawn, the supervisor logs a
warning and runs the daemon without a companion overlay. We do not
respawn the sprite when the daemon restarts — its SSE connection
auto-reconnects to the new daemon's web port.
"""

from __future__ import annotations

import dataclasses
import logging
import sys

from .process import ChildHandle, spawn, terminate

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class SpriteChild:
    """Wraps an optional ``ChildHandle`` for the sprite process."""

    handle: ChildHandle | None

    @classmethod
    def spawn(cls) -> "SpriteChild":
        """Spawn ``voice-sprite``. Returns an instance with ``handle=None`` on failure."""
        argv = [sys.executable, "-m", "voice_sprite"]
        try:
            handle = spawn(argv, name="voice-sprite")
        except (OSError, FileNotFoundError) as exc:
            logger.warning("Sprite failed to spawn (%s); continuing without it", exc)
            return cls(handle=None)
        return cls(handle=handle)

    def terminate(self, *, grace_s: float) -> None:
        if self.handle is None:
            return
        terminate(self.handle, grace_s=grace_s)
