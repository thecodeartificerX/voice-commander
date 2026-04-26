"""Supervisor CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from voice_commander.single_instance import AlreadyRunning
from voice_commander.supervisor.cli import _build_parser, main


def test_parser_defaults() -> None:
    args = _build_parser().parse_args([])
    assert args.no_sprite is False


def test_parser_no_sprite() -> None:
    args = _build_parser().parse_args(["--no-sprite"])
    assert args.no_sprite is True


def test_main_returns_loop_exit_code(tmp_path: Path) -> None:
    """main() returns whatever loop.run returns."""
    with (
        patch("voice_commander.supervisor.cli.SingleInstanceLock"),
        patch("voice_commander.supervisor.cli.SpriteChild"),
        patch("voice_commander.supervisor.cli.run", return_value=42),
        patch("voice_commander.supervisor.cli._spawn_daemon"),
        patch("voice_commander.supervisor.cli._configure_logging"),
    ):
        rc = main(["--no-sprite"])
    assert rc == 42


def test_main_already_running_exits_one() -> None:
    with (
        patch("voice_commander.supervisor.cli.SingleInstanceLock") as fake_lock_cls,
        patch("voice_commander.supervisor.cli._configure_logging"),
    ):
        fake_lock_cls.return_value.acquire.side_effect = AlreadyRunning("pid=123")
        rc = main([])
    assert rc == 1
