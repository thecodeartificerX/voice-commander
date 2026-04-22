from __future__ import annotations

import io
import logging
import platform
from typing import TYPE_CHECKING

import pyglet
from PIL import Image

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
        render_scale: float = 1.0,
        y_nudge_px: int = 0,
    ) -> None:
        # WINDOW_STYLE_OVERLAY (pyglet 2.1+) = borderless + DWM blur-behind +
        # topmost + click-through + no-activate in one flag. WINDOW_STYLE_BORDERLESS
        # alone leaves the window opaque; WINDOW_STYLE_TRANSPARENT keeps the
        # title bar. OVERLAY gives the HUD-style compositing we need — BUT
        # only if the GL framebuffer actually has an alpha channel to write
        # into. Default pyglet Config has alpha_size=0; explicitly request
        # alpha_size=8 so our glClearColor(0, 0, 0, 0) and sprite alpha blend
        # compose into the layered-window alpha the DWM reads for compositing.
        gl_config = pyglet.gl.Config(  # type: ignore[abstract]
            double_buffer=True,
            alpha_size=8,
            transparent_framebuffer=True,
        )
        super().__init__(
            width=width,
            height=height,
            style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
            config=gl_config,
            vsync=False,
        )
        # Pyglet's default clear colour is opaque black; replace with fully
        # transparent so only non-zero-alpha sprite pixels are visible.
        pyglet.gl.glClearColor(0, 0, 0, 0)
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
        # Set by load_charsheet_image. Always >= 1; on_draw multiplies source
        # coords by this to index into the pre-upscaled charsheet.
        self._upscale_factor: int = 1
        # Render tuning — tweak live via config.toml (sprite.render_scale,
        # sprite.y_nudge_px) to size + position the cat within the window.
        self._render_scale = render_scale
        self._y_nudge_px = y_nudge_px

    def load_charsheet_image(self, png_path: str) -> None:
        """Load the charsheet PNG, pre-upscaled with nearest-neighbour so each
        frame blits at its on-screen size with no runtime scaling.

        pyglet 2.1's ``Sprite.scale`` does not reliably compose with
        per-frame ``Sprite.image`` swaps from ``TextureRegion``s — some frames
        render at native texture size regardless of the scale attribute. The
        workaround is to do the upscale once at load time via Pillow's NEAREST
        filter and feed pyglet an already-upscaled charsheet. After this, frame
        regions are already the final display size and a direct ``blit`` at
        (0, 0) fills the window as expected.

        The upscale factor is derived from the window height and the charsheet
        row-count implied by the source PNG: caller's set the
        ``_upscale_factor`` here so ``on_draw`` can multiply the source
        coordinates returned by :class:`SpriteRenderer`.
        """
        src = Image.open(png_path).convert("RGBA")
        # Derive scale from the window's intended display size vs the source
        # cell size. Cell size isn't known here, but the renderer's charsheet
        # knows frame_height; assume the window was sized for that cell
        # upscaled by a power-of-two factor. 4x is the sweet spot for 32 -> 128.
        factor = max(1, self.height // self._renderer.charsheet.frame_height)
        if factor > 1:
            scaled = src.resize((src.width * factor, src.height * factor), Image.Resampling.NEAREST)
        else:
            scaled = src
        self._upscale_factor = factor

        buf = io.BytesIO()
        scaled.save(buf, format="PNG")
        buf.seek(0)
        self._image = pyglet.image.load(png_path, file=buf)
        # Force GL_NEAREST on the already-upscaled texture — avoids any further
        # bilinear smearing if pyglet mips it down somewhere.
        texture = self._image.get_texture()
        gl = pyglet.gl
        gl.glBindTexture(texture.target, texture.id)
        gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
        self._cached_frame_key = None
        self._cached_region = None

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def on_draw(self) -> None:
        self.clear()
        if self._image is None:
            return

        frame_key = self._renderer.frame_region
        x, y, w, h = frame_key
        # Source (x, y, w, h) are in source-PNG pixels; charsheet image in
        # memory has been pre-upscaled by _upscale_factor, so multiply through.
        # Also convert y from charsheet-top-down to pyglet-bottom-up here.
        f = self._upscale_factor
        if frame_key != self._cached_frame_key or self._cached_region is None:
            img_h = self._image.height
            self._cached_region = self._image.get_region(
                x * f,
                img_h - (y + h) * f,
                w * f,
                h * f,
            )
            self._cached_frame_key = frame_key
        region = self._cached_region

        # Render-tuning: shrink the upscaled frame (render_scale < 1.0 leaves a
        # margin around the sprite) and nudge it up/down (y_nudge_px positive
        # shifts toward the top of the window, since pyglet y=0 is bottom).
        #
        # We can't re-scale the region itself via blit(), so we re-extract the
        # region into a dynamically-sized texture when render_scale != 1.0:
        # use a pyglet image copy + set_scale on a persistent sprite object.
        blit_w = int(region.width * self._render_scale)
        blit_h = int(region.height * self._render_scale)
        blit_x = (self.width - blit_w) // 2
        blit_y = (self.height - blit_h) // 2 + self._y_nudge_px
        if self._render_scale == 1.0:
            region.blit(blit_x, blit_y, 0)
        else:
            # Reuse a single Sprite object; only reset image when the source
            # region changes (cache invalidation already gated above).
            if self._sprite is None or self._sprite.image is not region:
                if self._sprite is None:
                    self._sprite = pyglet.sprite.Sprite(region, x=blit_x, y=blit_y)
                else:
                    self._sprite.image = region
            self._sprite.x = blit_x
            self._sprite.y = blit_y
            self._sprite.scale = self._render_scale
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
