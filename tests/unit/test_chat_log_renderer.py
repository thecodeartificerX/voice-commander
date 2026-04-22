"""Tests for ChatLogRenderer — pyglet labels driven by ChatLog.

Pyglet Label is stubbed so tests run headless.
"""

from __future__ import annotations

import importlib
import time
from unittest.mock import MagicMock, patch

from voice_sprite.chat_log import ChatLog, ChatLogEntry
import voice_sprite.chat_log_renderer as _mod


def test_draw_creates_label_per_entry():
    """Two log entries → at least two Label instances created."""
    fake_label_cls = MagicMock()
    # Patch the module attribute AFTER import so the live reference is replaced.
    with patch.object(_mod, "pyglet") as pyglet_mock:
        pyglet_mock.text.Label = fake_label_cls
        ChatLogRenderer = _mod.ChatLogRenderer

        log = ChatLog(max_lines=5, hold_ms=10000, fade_ms=1000)
        now = time.monotonic()
        log.append(ChatLogEntry("minimized window", "ok", now))
        log.append(ChatLogEntry("no match", "miss", now))

        window = MagicMock(width=400, height=200)
        r = ChatLogRenderer(
            chat_log=log,
            window=window,
            hud_width_px=220,
            font_size=13,
            line_gap_px=4,
        )
        r.draw()
        # Two entries → at least two Label creations (cached after first draw)
        assert fake_label_cls.call_count >= 2


def test_status_color_map():
    from voice_sprite.chat_log_renderer import _color_for_status

    assert _color_for_status("ok")[:3] == (143, 215, 127)
    assert _color_for_status("error")[:3] == (255, 118, 118)
    assert _color_for_status("miss")[:3] == (255, 181, 98)


def test_hidden_when_chat_log_empty():
    """draw() with no entries must not construct any Label."""
    fake_label_cls = MagicMock()
    with patch.object(_mod, "pyglet") as pyglet_mock:
        pyglet_mock.text.Label = fake_label_cls
        ChatLogRenderer = _mod.ChatLogRenderer

        log = ChatLog(max_lines=3, hold_ms=1, fade_ms=1)
        window = MagicMock(width=400, height=200)
        r = ChatLogRenderer(log, window, hud_width_px=220, font_size=13, line_gap_px=4)
        r.draw()
        fake_label_cls.assert_not_called()
