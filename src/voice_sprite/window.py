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
    """Transparent, borderless, always-on-top sprite window."""

    def __init__(
        self,
        width: int,
        height: int,
        x: int,
        y: int,
        renderer: SpriteRenderer,
        bubble: SpeechBubble,
    ) -> None:
        super().__init__(
            width=width,
            height=height,
            style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
            vsync=False,
        )
        self.set_location(x, y)
        self._renderer = renderer
        self._bubble = bubble
        self._image: pyglet.image.AbstractImage | None = None
        self._sprite: pyglet.sprite.Sprite | None = None
        self._label: pyglet.text.Label | None = None
        self._muted = False
        self._mute_color = (128, 128, 128)
        # Cache for on_draw region lookup — recomputed only when frame_region changes.
        self._cached_frame_key: tuple[int, int, int, int] | None = None
        self._cached_region: pyglet.image.AbstractImage | None = None

    def load_charsheet_image(self, png_path: str) -> None:
        """Load the charsheet PNG into a pyglet image.

        Applies GL_NEAREST filtering so 32-px pixel-art cells upscale to the
        configured window size without blur (bilinear default smears pixels).
        """
        self._image = pyglet.image.load(png_path)
        # pyglet.image.load() returns an ImageData; touching .get_texture() forces
        # texture creation so we can override the default GL_LINEAR filter.
        texture = self._image.get_texture()
        gl = pyglet.gl
        gl.glBindTexture(texture.target, texture.id)
        gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def on_draw(self) -> None:
        self.clear()
        if self._image is None:
            return

        frame_key = self._renderer.frame_region
        x, y, w, h = frame_key
        # pyglet uses bottom-left origin; charsheet uses top-left rows.
        # Cache the region so we avoid a get_region() allocation every draw at 60 fps;
        # recompute only when the active frame (x, y, w, h) actually changes.
        if frame_key != self._cached_frame_key or self._cached_region is None:
            img_h = self._image.height
            self._cached_region = self._image.get_region(x, img_h - y - h, w, h)
            self._cached_frame_key = frame_key
        region = self._cached_region

        # Upscale pixel-art frame to fill the window. Cells are small (32px)
        # but the window is sized for visibility (~128px+) — nearest-neighbor
        # scaling keeps edges crisp.
        scale = min(self.width / w, self.height / h)
        sprite_x = (self.width - w * scale) / 2
        sprite_y = (self.height - h * scale) / 2

        if self._sprite is None:
            self._sprite = pyglet.sprite.Sprite(region, x=sprite_x, y=sprite_y)
        else:
            self._sprite.image = region
            self._sprite.x = sprite_x
            self._sprite.y = sprite_y
        self._sprite.scale = scale

        if self._muted:
            self._sprite.color = self._mute_color
        else:
            self._sprite.color = (255, 255, 255)

        self._sprite.draw()

        # Speech bubble
        if self._bubble.visible:
            if self._label is None:
                self._label = pyglet.text.Label(
                    self._bubble.text,
                    font_name="Segoe UI",
                    font_size=10,
                    x=w // 2,
                    y=h + 4,
                    anchor_x="center",
                    anchor_y="bottom",
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
        """Apply click-through, topmost, no-taskbar flags (Windows only)."""
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
