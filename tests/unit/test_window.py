"""Tests for SpriteWindow.on_draw() — headless via __new__ bypass + pyglet mock.

SpriteWindow inherits from pyglet.window.Window, so its __init__ creates a real
OS window + GL context. We bypass this by using __new__ and setting instance
attributes directly — then patch the module-level pyglet reference so on_draw()'s
GL calls go to a MagicMock instead of the driver.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
    win._badge_label = None
    win._cancel_badge_label = None
    win._processing_badge_label = None  # ADR 0096 D5: processing state badge
    win._mode_badge_label = None  # named-mode persistent badge
    win._active_mode_badge = None  # named-mode badge text (None = hidden)
    win._dictating = False
    win._processing = False  # ADR 0096 D5: processing state flag
    win._cancelled_cue = False
    win._dim = False
    win._dim_color = (102, 102, 102)
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

    with patch.object(_mod, "pyglet"):
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


def test_set_dim_toggles_flag():
    renderer = MagicMock()
    bubble = MagicMock()
    win = _make_window(renderer, bubble)

    assert win._dim is False
    win.set_dim(True)
    assert win._dim is True
    win.set_dim(False)
    assert win._dim is False


# ---------------------------------------------------------------------------
# Pure rendering-math helpers — tested directly via _mod._function_name
# ---------------------------------------------------------------------------


def test_flip_y_first_row():
    """Top row (y=0, h=32) of a 96-px image maps to y_bottom=64."""
    assert _mod._flip_y(96, 0, 32) == 64


def test_flip_y_second_row():
    """Second row (y=32, h=32) maps to y_bottom=32."""
    assert _mod._flip_y(96, 32, 32) == 32


def test_compute_fit_scale_height_constrained():
    """Region is wider than tall relative to frame — height is the bottleneck."""
    assert _mod._compute_fit_scale(100, 96, 32, 48) == pytest.approx(2.0)


def test_compute_fit_scale_width_constrained():
    """Region is taller than wide relative to frame — width is the bottleneck."""
    assert _mod._compute_fit_scale(64, 100, 32, 16) == pytest.approx(2.0)


def test_compute_display_size():
    """Frame 32x48 at scale 2.0 → (64.0, 96.0)."""
    dw, dh = _mod._compute_display_size(32, 48, 2.0)
    assert dw == pytest.approx(64.0)
    assert dh == pytest.approx(96.0)


def test_compute_sprite_xy_centred():
    """Frame centred in region with no nudge — x and y symmetric."""
    sx, sy = _mod._compute_sprite_xy(0, 100, 100, 60, 60, 0)
    assert sx == pytest.approx(20.0)
    assert sy == pytest.approx(20.0)


def test_compute_sprite_xy_with_nudge():
    """Vertical nudge shifts sprite_y by nudge amount."""
    _, sy_no_nudge = _mod._compute_sprite_xy(0, 100, 100, 60, 60, 0)
    _, sy_nudge = _mod._compute_sprite_xy(0, 100, 100, 60, 60, 10)
    assert sy_nudge == pytest.approx(sy_no_nudge + 10)


def test_compute_sprite_xy_region_offset():
    """Non-zero sprite_region_x shifts sprite_x."""
    sx, _ = _mod._compute_sprite_xy(50, 100, 100, 60, 60, 0)
    assert sx == pytest.approx(70.0)


def test_compute_label_xy():
    """Label anchor is (width//2, height-4)."""
    assert _mod._compute_label_xy(200, 150) == (100, 146)


def test_compute_label_color_full_opacity():
    """opacity=1.0 → alpha=255."""
    assert _mod._compute_label_color(1.0) == (255, 255, 255, 255)


def test_compute_label_color_half_opacity():
    """opacity=0.5 → alpha=127 (truncated int)."""
    assert _mod._compute_label_color(0.5) == (255, 255, 255, 127)


def test_compute_label_color_zero_opacity():
    """opacity=0.0 → alpha=0."""
    assert _mod._compute_label_color(0.0) == (255, 255, 255, 0)


# ---------------------------------------------------------------------------
# on_draw() branch coverage — bubble, cached region, sprite update, dim
# ---------------------------------------------------------------------------


def test_on_draw_bubble_visible_creates_label():
    """on_draw with bubble.visible=True and no existing label creates a Label."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = True
    bubble.opacity = 0.8

    with patch.object(_mod, "pyglet") as pg:
        fake_region = MagicMock()
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = fake_region
        pg.sprite.Sprite.return_value = MagicMock()

        win = _make_window(renderer, bubble, image=fake_image)
        win.clear = MagicMock()
        win.on_draw()

    pg.text.Label.assert_called_once_with(
        bubble.text,
        font_name="Segoe UI",
        font_size=10,
        x=50,
        y=96,
        anchor_x="center",
        anchor_y="top",
        color=(255, 255, 255, 204),
    )


def test_on_draw_bubble_visible_updates_existing_label():
    """on_draw with bubble.visible=True and existing label updates text/color."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = True
    bubble.text = "hello"
    bubble.opacity = 1.0

    with patch.object(_mod, "pyglet") as pg:
        fake_region = MagicMock()
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = fake_region
        pg.sprite.Sprite.return_value = MagicMock()

        win = _make_window(renderer, bubble, image=fake_image)
        existing_label = MagicMock()
        win._label = existing_label
        win.clear = MagicMock()
        win.on_draw()

    existing_label.draw.assert_called_once()
    assert existing_label.text == "hello"
    assert existing_label.color == (255, 255, 255, 255)


def test_on_draw_uses_cached_region_on_same_frame():
    """on_draw skips get_region() when frame_key is unchanged."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False

    with patch.object(_mod, "pyglet") as pg:
        cached = MagicMock()
        fake_image = MagicMock()
        fake_image.height = 96
        pg.sprite.Sprite.return_value = MagicMock()

        win = _make_window(renderer, bubble, image=fake_image)
        win._cached_frame_key = (0, 0, 32, 48)  # pre-warm cache
        win._cached_region = cached
        win.clear = MagicMock()
        win.on_draw()

    # get_region must NOT be called — we used the cache
    fake_image.get_region.assert_not_called()


def test_on_draw_sprite_update_when_image_changes():
    """on_draw updates sprite.image when region changes between frames."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False

    with patch.object(_mod, "pyglet"):
        new_region = MagicMock()
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = new_region

        existing_sprite = MagicMock()
        existing_sprite.image = MagicMock()  # different object → triggers elif

        win = _make_window(renderer, bubble, image=fake_image)
        win._sprite = existing_sprite
        win.clear = MagicMock()
        win.on_draw()

    assert existing_sprite.image is new_region


def test_on_draw_dim_color():
    """on_draw sets sprite color to dim_color when _dim=True."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False

    with patch.object(_mod, "pyglet") as pg:
        fake_region = MagicMock()
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = fake_region
        fake_sprite = MagicMock()
        pg.sprite.Sprite.return_value = fake_sprite

        win = _make_window(renderer, bubble, image=fake_image)
        win._dim = True
        win.clear = MagicMock()
        win.on_draw()

    assert fake_sprite.color == (102, 102, 102)


def test_on_draw_dictating_draws_badge():
    """on_draw with _dictating=True lazily creates and draws the DICTATING badge."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False

    with patch.object(_mod, "pyglet") as pg:
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = MagicMock()
        pg.sprite.Sprite.return_value = MagicMock()
        fake_badge = MagicMock()
        pg.text.Label.return_value = fake_badge

        win = _make_window(renderer, bubble, image=fake_image)
        win._dictating = True
        win.clear = MagicMock()
        win.on_draw()

    pg.text.Label.assert_called_once_with(
        "● DICTATING",
        font_name="Segoe UI",
        font_size=9,
        weight="bold",
        x=50,
        y=2,
        anchor_x="center",
        anchor_y="bottom",
        color=(245, 194, 66, 255),
    )
    fake_badge.draw.assert_called_once()


def test_on_draw_not_dictating_skips_badge():
    """on_draw with _dictating=False never creates the badge label."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False

    with patch.object(_mod, "pyglet") as pg:
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = MagicMock()
        pg.sprite.Sprite.return_value = MagicMock()

        win = _make_window(renderer, bubble, image=fake_image)
        win.clear = MagicMock()
        win.on_draw()

    pg.text.Label.assert_not_called()


# ---------------------------------------------------------------------------
# Cancelled-cue badge — ADR 0089 (FIX 1)
# ---------------------------------------------------------------------------


def test_set_cancelled_cue_method_exists():
    """SpriteWindow must expose set_cancelled_cue(bool) for the renderer to call."""
    renderer = MagicMock()
    bubble = MagicMock()
    win = _make_window(renderer, bubble)
    # Must not raise AttributeError
    win.set_cancelled_cue(True)
    assert win._cancelled_cue is True
    win.set_cancelled_cue(False)
    assert win._cancelled_cue is False


def test_on_draw_cancelled_cue_draws_cancel_badge():
    """on_draw with _cancelled_cue=True lazily creates and draws the CANCELLED badge."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False

    with patch.object(_mod, "pyglet") as pg:
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = MagicMock()
        pg.sprite.Sprite.return_value = MagicMock()
        fake_cancel_badge = MagicMock()
        pg.text.Label.return_value = fake_cancel_badge

        win = _make_window(renderer, bubble, image=fake_image)
        win._cancelled_cue = True
        win.clear = MagicMock()
        win.on_draw()

    # The cancel badge label must be created with a DISTINCT text and color
    # from the DICTATING badge (yellow) and the speech-bubble label (white).
    # Expect red-orange color to be visually distinct.
    call_kwargs = pg.text.Label.call_args
    label_text = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("text", "")
    assert "CANCEL" in label_text.upper(), (
        f"cancel badge text must contain 'CANCEL', got: {label_text!r}"
    )
    fake_cancel_badge.draw.assert_called_once()


def test_on_draw_cancelled_cue_false_skips_cancel_badge():
    """on_draw with _cancelled_cue=False must not render any cancel badge."""
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False

    with patch.object(_mod, "pyglet") as pg:
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = MagicMock()
        pg.sprite.Sprite.return_value = MagicMock()

        win = _make_window(renderer, bubble, image=fake_image)
        win._cancelled_cue = False
        win.clear = MagicMock()
        win.on_draw()

    # No Label created (bubble.visible=False, not dictating, not cancelled)
    pg.text.Label.assert_not_called()


def test_on_draw_cancelled_and_dictating_both_render():
    """If somehow both _cancelled_cue and _dictating are True, both badges render.

    This should not normally occur (dictating is cleared before cancelled_cue
    is set), but the render path must not crash.
    """
    renderer = MagicMock()
    renderer.frame_region = (0, 0, 32, 48)
    bubble = MagicMock()
    bubble.visible = False

    with patch.object(_mod, "pyglet") as pg:
        fake_image = MagicMock()
        fake_image.height = 96
        fake_image.get_region.return_value = MagicMock()
        pg.sprite.Sprite.return_value = MagicMock()
        pg.text.Label.return_value = MagicMock()

        win = _make_window(renderer, bubble, image=fake_image)
        win._dictating = True
        win._cancelled_cue = True
        win.clear = MagicMock()
        # Must not raise
        win.on_draw()

    # Two labels created: DICTATING badge + CANCELLED badge
    assert pg.text.Label.call_count == 2


# ---------------------------------------------------------------------------
# load_charsheet_image coverage
# ---------------------------------------------------------------------------


def test_load_charsheet_image_sets_image_and_clears_cache():
    """load_charsheet_image stores _image and resets cached state."""
    renderer = MagicMock()
    bubble = MagicMock()
    win = _make_window(renderer, bubble)
    win._cached_frame_key = (0, 0, 32, 32)
    win._cached_region = MagicMock()
    win._sprite = MagicMock()

    with patch.object(_mod, "pyglet") as pg:
        fake_image = MagicMock()
        pg.image.load.return_value = fake_image
        fake_texture = MagicMock()
        fake_image.get_texture.return_value = fake_texture

        win.load_charsheet_image("fake/path.png")

    assert win._image is fake_image
    assert win._cached_frame_key is None
    assert win._cached_region is None
    assert win._sprite is None


# ---------------------------------------------------------------------------
# apply_win32_flags coverage
# ---------------------------------------------------------------------------


def test_apply_win32_flags_non_windows_logs_warning():
    """apply_win32_flags on non-Windows logs a warning and returns early."""
    renderer = MagicMock()
    bubble = MagicMock()
    win = _make_window(renderer, bubble)

    with (
        patch("voice_sprite.window.platform") as mock_platform,
        patch("voice_sprite.window.logger") as mock_logger,
    ):
        mock_platform.system.return_value = "Linux"
        win.apply_win32_flags()

    mock_logger.warning.assert_called_once()


def test_apply_win32_flags_no_hwnd_logs_error():
    """apply_win32_flags logs an error when HWND cannot be obtained."""
    renderer = MagicMock()
    bubble = MagicMock()
    win = _make_window(renderer, bubble)

    # canvas with no 'hwnd' attr, no '_hwnd' fallback either
    win.canvas = MagicMock(spec=[])  # spec=[] → hasattr returns False for anything

    with (
        patch("voice_sprite.window.platform") as mock_platform,
        patch("voice_sprite.window.logger") as mock_logger,
        patch.dict("sys.modules", {"voice_sprite.win32_flags": MagicMock()}),
    ):
        mock_platform.system.return_value = "Windows"
        win.apply_win32_flags()

    mock_logger.error.assert_called_once()


def test_apply_win32_flags_calls_apply_click_through():
    """apply_win32_flags delegates to apply_click_through when HWND found."""
    renderer = MagicMock()
    bubble = MagicMock()
    win = _make_window(renderer, bubble)

    fake_hwnd = 12345
    win.canvas = MagicMock()
    win.canvas.hwnd = fake_hwnd

    mock_win32 = MagicMock()

    with (
        patch("voice_sprite.window.platform") as mock_platform,
        patch.dict("sys.modules", {"voice_sprite.win32_flags": mock_win32}),
    ):
        mock_platform.system.return_value = "Windows"
        win.apply_win32_flags()

    mock_win32.apply_click_through.assert_called_once_with(fake_hwnd)
