from __future__ import annotations

import logging
import platform
from typing import TYPE_CHECKING

import pyglet

if TYPE_CHECKING:
    from .speech_bubble import SpeechBubble
    from .sprite_renderer import SpriteRenderer

logger = logging.getLogger(__name__)


class SpriteWindow(pyglet.window.Window):  # type: ignore[misc]
    """Transparent, borderless, always-on-top sprite window.

    Follows the canonical pyglet 2.1.8+ recipe for per-pixel alpha overlays
    on Windows (see pyglet issue #1271):

    1. Explicit ``gl.Config(alpha_size=8, double_buffer=True)`` guarantees
       an alpha-enabled framebuffer even if pyglet's style-driven auto-
       config misses it on some drivers.
    2. ``style=WINDOW_STYLE_OVERLAY`` wires ``WS_POPUP | WS_EX_LAYERED |
       WS_EX_TRANSPARENT`` and calls DwmEnableBlurBehindWindow — borderless,
       click-through, topmost, per-pixel-alpha in one flag.
    3. ``glEnable(GL_BLEND)`` + standard src-alpha / one-minus-src-alpha
       blend func so sprite pixels composite over the transparent clear
       instead of overwriting the framebuffer with alpha=1.
    4. ``glClearColor(0, 0, 0, 0)`` so ``window.clear()`` writes fully
       transparent pixels everywhere except where the sprite draws.
    """

    def __init__(
        self,
        width: int,
        height: int,
        x: int,
        y: int,
        renderer: SpriteRenderer,
        bubble: SpeechBubble,
        render_scale: float = 1.0,
        y_nudge_px: int = 0,
    ) -> None:
        gl_config = pyglet.gl.Config(  # type: ignore[abstract]
            alpha_size=8,
            double_buffer=True,
        )
        super().__init__(
            width=width,
            height=height,
            style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
            config=gl_config,
            vsync=False,
        )
        # Alpha compositing — sprite pixels blend over the transparent clear
        # instead of overwriting the framebuffer with opaque black.
        gl = pyglet.gl
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glClearColor(0, 0, 0, 0)

        self.set_location(x, y)
        self._renderer = renderer
        self._bubble = bubble
        self._render_scale = render_scale
        self._y_nudge_px = y_nudge_px

        self._image: pyglet.image.AbstractImage | None = None
        self._sprite: pyglet.sprite.Sprite | None = None
        self._label: pyglet.text.Label | None = None
        self._muted = False
        self._mute_color = (128, 128, 128)

        # Cache the current frame region so get_region() runs only when the
        # renderer advances to a new frame (every 125 ms at 8 fps, not every
        # 16 ms at 60 fps draw).
        self._cached_frame_key: tuple[int, int, int, int] | None = None
        self._cached_region: pyglet.image.AbstractImage | None = None

    def load_charsheet_image(self, png_path: str) -> None:
        """Load the charsheet PNG and force GL_NEAREST filtering.

        Pixel-art upscaled with GL_LINEAR (pyglet's default) looks blurry.
        GL_NEAREST keeps edges crisp when the sprite renders at e.g. 4x the
        source 32x32 cell size.
        """
        self._image = pyglet.image.load(png_path)
        texture = self._image.get_texture()
        gl = pyglet.gl
        gl.glBindTexture(texture.target, texture.id)
        gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
        # Charsheet swapped (e.g. hot-reload) — drop cached region + sprite so
        # next draw rebuilds them from the new image.
        self._cached_frame_key = None
        self._cached_region = None
        self._sprite = None

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def on_draw(self) -> None:
        self.clear()
        if self._image is None:
            return

        frame_key = self._renderer.frame_region
        x, y, w, h = frame_key
        # pyglet.image.get_region uses bottom-up y; the charsheet itself uses
        # top-down rows (row 0 = top of PNG). Convert with img_h - (y + h).
        if frame_key != self._cached_frame_key or self._cached_region is None:
            img_h = self._image.height
            self._cached_region = self._image.get_region(x, img_h - (y + h), w, h)
            self._cached_frame_key = frame_key
        region = self._cached_region

        # Fit the source frame to the window, then apply the configurable
        # render_scale shrink factor. render_scale=1.0 fills the window
        # edge-to-edge; 0.75 leaves a 12.5 % margin each side so the cat
        # does not clip at the window borders.
        fit_scale = min(self.width / w, self.height / h)
        final_scale = fit_scale * self._render_scale
        disp_w = w * final_scale
        disp_h = h * final_scale
        sprite_x = (self.width - disp_w) / 2
        sprite_y = (self.height - disp_h) / 2 + self._y_nudge_px

        if self._sprite is None:
            self._sprite = pyglet.sprite.Sprite(region, x=sprite_x, y=sprite_y)
        elif self._sprite.image is not region:
            self._sprite.image = region
        self._sprite.x = sprite_x
        self._sprite.y = sprite_y
        self._sprite.scale = final_scale
        self._sprite.color = self._mute_color if self._muted else (255, 255, 255)
        self._sprite.draw()

        if self._bubble.visible:
            if self._label is None:
                self._label = pyglet.text.Label(
                    self._bubble.text,
                    font_name="Segoe UI",
                    font_size=10,
                    x=self.width // 2,
                    y=self.height - 4,
                    anchor_x="center",
                    anchor_y="top",
                    color=(255, 255, 255, int(self._bubble.opacity * 255)),
                )
            else:
                self._label.text = self._bubble.text
                self._label.color = (
                    255,
                    255,
                    255,
                    int(self._bubble.opacity * 255),
                )
            self._label.draw()

    def apply_win32_flags(self) -> None:
        """Apply click-through, topmost, no-taskbar flags (Windows only).

        Redundant when using ``WINDOW_STYLE_OVERLAY`` (pyglet already sets
        ``WS_EX_LAYERED | WS_EX_TRANSPARENT`` internally) but harmless —
        kept as a safety net for older pyglet versions and non-overlay
        styles users may switch to.
        """
        if platform.system() != "Windows":
            logger.warning("Win32 flags only apply on Windows")
            return
        from .win32_flags import apply_click_through

        hwnd = self.canvas.hwnd if hasattr(self.canvas, "hwnd") else None
        if hwnd is None:
            # TODO: revisit on pyglet upgrade — 2.x HWND access path via window._hwnd
            hwnd = getattr(self, "_hwnd", None)
        if hwnd is None:
            logger.error("Could not obtain HWND for sprite window")
            return
        apply_click_through(hwnd)
