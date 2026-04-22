"""Tests for ChatLogRenderer — pyglet labels driven by ChatLog.

Pyglet Label is stubbed so tests run headless.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import voice_sprite.chat_log_renderer as _mod
from voice_sprite.chat_log import ChatLog, ChatLogEntry


def _fake_label_factory():
    """Return a callable that produces distinct, attribute-trackable label stubs.

    Using MagicMock() as the factory returns the *same* return_value for every
    call, making per-label assertions unreliable.  This factory returns a new
    SimpleNamespace each time so tests can distinguish label[0] from label[1].
    """
    created = []

    def _make(**kwargs):
        stub = SimpleNamespace(text=kwargs.get("text", ""), x=0, y=0, color=(255, 255, 255, 0))
        stub.draw = lambda: None
        created.append(stub)
        return stub

    _make.created = created
    return _make


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

        r = ChatLogRenderer(
            chat_log=log,
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
        r = ChatLogRenderer(log, hud_width_px=220, font_size=13, line_gap_px=4)
        r.draw()
        fake_label_cls.assert_not_called()


def test_stale_labels_blanked_when_entries_shrink():
    """Labels for removed entries must be blanked on the next draw call.

    Scenario: draw with 3 entries creates 3 labels.  Then entries shrink to 1
    (simulate expiry by patching entries()). The tail labels [1] and [2] must
    have their text set to "" — not left with the previous frame's content.
    """
    factory = _fake_label_factory()
    with patch.object(_mod, "pyglet") as pyglet_mock:
        pyglet_mock.text.Label.side_effect = factory
        ChatLogRenderer = _mod.ChatLogRenderer

        log = ChatLog(max_lines=5, hold_ms=10000, fade_ms=1000)
        now = time.monotonic()
        log.append(ChatLogEntry("cmd one", "ok", now))
        log.append(ChatLogEntry("cmd two", "ok", now))
        log.append(ChatLogEntry("cmd three", "ok", now))

        renderer = ChatLogRenderer(log, hud_width_px=220, font_size=13, line_gap_px=4)

        # First draw — populates label pool with 3 distinct labels.
        renderer.draw()
        assert len(factory.created) >= 3

        # Simulate entries shrinking to 1 by patching log.entries().
        surviving = [log.entries()[0]]
        with patch.object(log, "entries", return_value=surviving):
            renderer.draw()

        # Labels at index 1 and 2 must have been blanked.
        assert renderer._labels[1].text == ""
        assert renderer._labels[2].text == ""


def test_stale_labels_blanked_when_all_entries_expire():
    """When all entries expire (entries() returns []), all pooled labels must be blanked."""
    factory = _fake_label_factory()
    with patch.object(_mod, "pyglet") as pyglet_mock:
        pyglet_mock.text.Label.side_effect = factory
        ChatLogRenderer = _mod.ChatLogRenderer

        log = ChatLog(max_lines=5, hold_ms=10000, fade_ms=1000)
        now = time.monotonic()
        log.append(ChatLogEntry("cmd one", "ok", now))
        log.append(ChatLogEntry("cmd two", "ok", now))

        renderer = ChatLogRenderer(log, hud_width_px=220, font_size=13, line_gap_px=4)

        # First draw — creates 2 distinct labels.
        renderer.draw()
        assert len(renderer._labels) >= 2

        # Simulate all entries expiring — this is the edge case fixed by removing
        # the early return. Previously, stale text lingered indefinitely on quiet periods.
        with patch.object(log, "entries", return_value=[]):
            renderer.draw()

        # All pooled labels must be blanked — ghost text gone.
        for label in renderer._labels:
            assert label.text == ""
