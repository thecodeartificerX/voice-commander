"""Supervisor entry point.

Owns:

* arg parsing (``--no-sprite``)
* logging configuration (separate file from the daemon)
* single-instance lock at ``outputs/.supervisor.lock``
* sprite spawn (best-effort) and daemon factory
* delegation to ``loop.run``
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import Config
from ..single_instance import AlreadyRunning, SingleInstanceLock
from .loop import run
from .process import ChildHandle, spawn, terminate
from .sprite import SpriteChild

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-commander-supervisor",
        description="Long-lived parent process for voice-commander.",
    )
    parser.add_argument(
        "--no-sprite",
        action="store_true",
        help="Skip the sprite companion (still spawns the daemon).",
    )
    return parser


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    cfg = Config.load(Path("config.toml"))
    level_name = os.environ.get("VC_LOG_LEVEL", cfg.logging.level).upper()
    level = getattr(logging, level_name, logging.INFO)

    log_path = Path(cfg.logging.file).with_name("supervisor.log")
    handler = RotatingFileHandler(
        log_path,
        maxBytes=cfg.logging.max_bytes,
        backupCount=cfg.logging.backup_count,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(threadName)s %(name)s %(levelname)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
        force=True,
    )


# ---------------------------------------------------------------------------
# Daemon factory (production path)
# ---------------------------------------------------------------------------


class _DaemonChild:
    """Adapts ``ChildHandle`` to the loop's ``_ChildProto``."""

    def __init__(self, handle: ChildHandle) -> None:
        self._handle = handle

    def wait(self) -> int:
        return self._handle.wait()

    def terminate(self, *, grace_s: float) -> None:
        terminate(self._handle, grace_s=grace_s)


def _spawn_daemon() -> _DaemonChild:
    argv = [sys.executable, "-m", "voice_commander"]
    handle = spawn(argv, name="voice-commander", env_extra={"VC_SUPERVISED": "1"})
    return _DaemonChild(handle)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging()

    lock = SingleInstanceLock(Path("outputs/.supervisor.lock"))
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        logger.error("Supervisor already running: %s", exc)
        return 1

    try:
        sprite = SpriteChild(handle=None) if args.no_sprite else SpriteChild.spawn()
        return run(sprite=sprite, daemon_factory=_spawn_daemon)
    except KeyboardInterrupt:
        logger.info("Ctrl+C received; shutdown complete")
        return 0
    finally:
        lock.release()
