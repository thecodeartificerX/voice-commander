"""Supervisor restart loop."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_commander.supervisor.exit_codes import EXIT_CLEAN, EXIT_RESTART
from voice_commander.supervisor.loop import run


def _scripted_daemon_factory(exit_codes: list[int]) -> MagicMock:
    """Return a factory whose successive children exit with the given codes."""
    queue = list(exit_codes)
    factory = MagicMock(name="daemon_factory")

    def _make_child() -> MagicMock:
        child = MagicMock(name="DaemonChild")
        child.wait.return_value = queue.pop(0)
        return child

    factory.side_effect = _make_child
    return factory


def test_clean_exit_returns_zero() -> None:
    sprite = MagicMock(name="SpriteChild")
    daemon_factory = _scripted_daemon_factory([EXIT_CLEAN])
    code = run(sprite=sprite, daemon_factory=daemon_factory)
    assert code == 0
    assert daemon_factory.call_count == 1
    sprite.terminate.assert_called_once()


def test_restart_exit_respawns() -> None:
    sprite = MagicMock(name="SpriteChild")
    daemon_factory = _scripted_daemon_factory([EXIT_RESTART, EXIT_RESTART, EXIT_CLEAN])
    code = run(sprite=sprite, daemon_factory=daemon_factory)
    assert code == 0
    assert daemon_factory.call_count == 3
    sprite.terminate.assert_called_once()


def test_crash_exit_propagates_code() -> None:
    sprite = MagicMock(name="SpriteChild")
    daemon_factory = _scripted_daemon_factory([1])
    code = run(sprite=sprite, daemon_factory=daemon_factory)
    assert code == 1
    sprite.terminate.assert_called_once()


def test_sprite_terminated_even_on_keyboard_interrupt() -> None:
    sprite = MagicMock(name="SpriteChild")

    def _make_child() -> MagicMock:
        child = MagicMock()
        child.wait.side_effect = KeyboardInterrupt
        return child

    daemon_factory = MagicMock(side_effect=_make_child)
    with pytest.raises(KeyboardInterrupt):
        run(sprite=sprite, daemon_factory=daemon_factory)
    sprite.terminate.assert_called_once()


def test_keyboard_interrupt_terminates_running_daemon() -> None:
    """Ctrl+C must propagate to the daemon, not just the supervisor."""
    sprite = MagicMock(name="SpriteChild")
    daemon = MagicMock(name="DaemonChild")
    daemon.wait.side_effect = KeyboardInterrupt
    daemon_factory = MagicMock(return_value=daemon)
    with pytest.raises(KeyboardInterrupt):
        run(sprite=sprite, daemon_factory=daemon_factory)
    daemon.terminate.assert_called_once()
