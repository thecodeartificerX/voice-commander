"""Unit tests for voice_commander.tools._system (speak tool).

Tests mock pynput.keyboard.Controller so no actual keypresses are synthesized.
The silero_vad stub prevents collection failure in environments without
the model weights installed.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Stub heavy deps at the module level before any voice_commander imports.
# This prevents ModuleNotFoundError when silero_vad / torch are absent.
# ---------------------------------------------------------------------------

for _mod in ("silero_vad", "torch", "sounddevice", "soxr"):
    sys.modules.setdefault(_mod, MagicMock())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_speak_presses_and_releases_ctrl_r() -> None:
    """speak() calls Controller.press(Key.ctrl_r) then Controller.release(Key.ctrl_r)."""
    mock_controller_instance = MagicMock()
    mock_key = MagicMock()
    mock_key.ctrl_r = "ctrl_r_sentinel"

    with (
        patch("voice_commander.tools._system._controller", mock_controller_instance),
        patch("voice_commander.tools._system.keyboard") as mock_kb,
    ):
        mock_kb.Key.ctrl_r = "ctrl_r_sentinel"

        from voice_commander.tools._system import speak  # noqa: PLC0415

        speak()

    mock_controller_instance.press.assert_called_once_with("ctrl_r_sentinel")
    mock_controller_instance.release.assert_called_once_with("ctrl_r_sentinel")


def test_speak_press_before_release() -> None:
    """speak() must call press before release (correct order)."""
    calls_in_order: list[str] = []
    mock_controller_instance = MagicMock()
    mock_controller_instance.press.side_effect = lambda _k: calls_in_order.append("press")
    mock_controller_instance.release.side_effect = lambda _k: calls_in_order.append("release")

    with (
        patch("voice_commander.tools._system._controller", mock_controller_instance),
        patch("voice_commander.tools._system.keyboard") as mock_kb,
    ):
        mock_kb.Key.ctrl_r = "ctrl_r_sentinel"

        from voice_commander.tools._system import speak  # noqa: PLC0415

        speak()

    assert calls_in_order == ["press", "release"], (
        f"Expected ['press', 'release'], got {calls_in_order}"
    )
