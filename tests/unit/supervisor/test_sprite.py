"""SpriteChild — best-effort spawn, never blocks supervisor on failure."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from voice_commander.supervisor.sprite import SpriteChild


def test_spawn_returns_instance() -> None:
    fake_handle = MagicMock(name="ChildHandle")
    with patch("voice_commander.supervisor.sprite.spawn", return_value=fake_handle) as mock_spawn:
        sprite = SpriteChild.spawn()
        assert sprite.handle is fake_handle
        mock_spawn.assert_called_once()


def test_spawn_swallows_failure(caplog: pytest.LogCaptureFixture) -> None:
    """If spawning the sprite fails, log a warning and return a no-op instance."""
    with (
        patch(
            "voice_commander.supervisor.sprite.spawn",
            side_effect=FileNotFoundError("uv not on PATH"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        sprite = SpriteChild.spawn()
    assert sprite.handle is None
    assert any("sprite" in r.message.lower() for r in caplog.records)


def test_terminate_noop_when_handle_none() -> None:
    sprite = SpriteChild(handle=None)
    sprite.terminate(grace_s=1.0)  # must not raise


def test_terminate_calls_terminate_helper() -> None:
    fake_handle = MagicMock(name="ChildHandle")
    with patch("voice_commander.supervisor.sprite.terminate") as mock_term:
        sprite = SpriteChild(handle=fake_handle)
        sprite.terminate(grace_s=2.5)
        mock_term.assert_called_once_with(fake_handle, grace_s=2.5)
