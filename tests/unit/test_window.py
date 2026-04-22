"""Tests for SpriteWindow.on_draw() — headless via __new__ bypass + pyglet mock.

SpriteWindow inherits from pyglet.window.Window, so its __init__ creates a real
OS window + GL context. We bypass this by using __new__ and setting instance
attributes directly — then patch the module-level pyglet reference so on_draw()'s
GL calls go to a MagicMock instead of the driver.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import voice_sprite.window as _mod


def _make_window(
    renderer: MagicMock,
    bubble: MagicMock,
    *,
    hud_renderer: MagicMock | None = None,
    image: MagicMock | None = None,
) -> _mod.SpriteWindow:
    """Create SpriteWindow without invoking GL or OS window creation."""
    win = _mod.SpriteWindow.__new__(_mod.SpriteWindow)
    win._renderer = renderer
    win._bubble = bubble
    win._hud_renderer = hud_renderer
    win._image = image
    win._sprite = None
    win._label = None
    win._muted = False
    win._mute_color = (128, 128, 128)
    win._cached_frame_key = None
    win._cached_region = None
    win._render_scale = 1.0
    win._y_nudge_px = 0
    win._sprite_region_x = 0
    win._sprite_region_w = 100
    # Bypass pyglet's width/height property setters (they call set_size → OS
    # window resize which fails on __new__-constructed instances).
    win._width = 100
    win._height = 100
    return win


def test_on_draw_returns_early_when_no_image():
    """on_draw with no image loaded must not attempt any rendering."""
    renderer = MagicMock()
    bubble = MagicMock()
    hud = MagicMock()

    with patch.object(_mod, "pyglet") as pg:
        win = _make_window(renderer, bubble, hud_renderer=hud, image=None)
        win.clear = MagicMock()
        win.on_draw()

    hud.draw.assert_not_called()


def test_on_draw_calls_hud_renderer_when_image_set():
    """on_draw with an image must call hud_renderer.draw() exactly once."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False
    hud = MagicMock()

    with patch.object(_mod, "pyglet") as pg:
        fake_region = MagicMock()
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = fake_region

        pg.sprite.Sprite.return_value = MagicMock()

        win = _make_window(renderer, bubble, hud_renderer=hud, image=fake_image)
        win.clear = MagicMock()
        win.on_draw()

    hud.draw.assert_called_once()


def test_on_draw_skips_hud_when_hud_renderer_none():
    """on_draw with hud_renderer=None must not raise."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False

    with patch.object(_mod, "pyglet") as pg:
        fake_region = MagicMock()
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = fake_region
        pg.sprite.Sprite.return_value = MagicMock()

        win = _make_window(renderer, bubble, hud_renderer=None, image=fake_image)
        win.clear = MagicMock()
        win.on_draw()  # must not raise


def test_set_muted_toggles_flag():
    renderer = MagicMock()
    bubble = MagicMock()
    win = _make_window(renderer, bubble)

    assert win._muted is False
    win.set_muted(True)
    assert win._muted is True
    win.set_muted(False)
    assert win._muted is False
