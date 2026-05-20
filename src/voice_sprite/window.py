from __future__ import annotations

import logging
import platform
from typing import TYPE_CHECKING

import pyglet

if TYPE_CHECKING:
    from .chat_log_renderer import ChatLogRenderer
    from .speech_bubble import SpeechBubble
    from .sprite_renderer import SpriteRenderer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure rendering-math helpers — no GL, no self, fully unit-testable.
# ---------------------------------------------------------------------------


def _flip_y(img_h: int, y: int, h: int) -> int:
    """Convert top-down charsheet row to pyglet's bottom-up y coordinate.

    pyglet's get_region() measures y from the *bottom* of the image, but
    charsheet cell metadata uses top-down rows (row 0 = top of PNG).
    """
    return img_h - (y + h)


def _compute_fit_scale(region_w: float, region_h: float, frame_w: float, frame_h: float) -> float:
    """Largest uniform scale that fits *frame* inside *region* without clipping."""
    return min(region_w / frame_w, region_h / frame_h)


def _compute_display_size(frame_w: float, frame_h: float, scale: float) -> tuple[float, float]:
    """Pixel dimensions of the frame after applying *scale*."""
    return frame_w * scale, frame_h * scale


def _compute_sprite_xy(
    sprite_region_x: int,
    region_w: float,
    region_h: float,
    disp_w: float,
    disp_h: float,
    y_nudge_px: int,
) -> tuple[float, float]:
    """Centre the sprite inside its sub-region and apply the vertical nudge."""
    x = sprite_region_x + (region_w - disp_w) / 2
    y = (region_h - disp_h) / 2 + y_nudge_px
    return x, y


def _compute_label_xy(window_w: int, window_h: int) -> tuple[int, int]:
    """Anchor point for the speech-bubble label: centred, 4 px from the top."""
    return window_w // 2, window_h - 4


def _compute_label_color(opacity: float) -> tuple[int, int, int, int]:
    """RGBA white with the given opacity (0.0–1.0) for the speech-bubble label."""
    return (255, 255, 255, int(opacity * 255))


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
        *,
        hud_renderer: ChatLogRenderer | None = None,
        sprite_region_x: int = 0,
        sprite_region_w: int | None = None,
    ) -> None:
        gl_config = pyglet.gl.Config(  # type: ignore[abstract]
            alpha_size=8,
            double_buffer=True,
            # Explicitly disable MSAA — some Windows drivers silently drop
            # the alpha channel when pyglet auto-picks a multisampled
            # framebuffer, which causes DWM to composite our window against
            # opaque black regardless of glClearColor.
            sample_buffers=0,
            samples=0,
        )
        super().__init__(
            width=width,
            height=height,
            style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
            config=gl_config,
            vsync=False,
        )
        # Alpha compositing — sprite pixels blend over the transparent clear.
        # Separate blend for alpha is critical on Windows layered windows:
        # a standard glBlendFunc(SRC_ALPHA, ONE_MINUS_SRC_ALPHA) multiplies
        # destination alpha by (1-src_alpha) which collapses the framebuffer's
        # alpha channel toward zero-but-also-weird and DWM ends up drawing
        # opaque black where we intended transparency. Using GL_ONE for the
        # alpha source makes the output alpha = src_alpha + dst_alpha*(1-src_alpha),
        # which preserves sprite opacity correctly while leaving empty regions
        # at alpha=0.
        gl = pyglet.gl
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFuncSeparate(
            gl.GL_SRC_ALPHA,
            gl.GL_ONE_MINUS_SRC_ALPHA,
            gl.GL_ONE,
            gl.GL_ONE_MINUS_SRC_ALPHA,
        )
        # NOTE: glClearColor is also called inside on_draw() every frame.
        # Some pyglet internals can reset the clear color between frames,
        # and pyglet upstream docs + issue #1271 specifically say to set it
        # inside the draw handler before window.clear() for transparent
        # overlays. Setting it once at init is insufficient on Windows.
        gl.glClearColor(0, 0, 0, 0)

        # Verify the driver actually granted an alpha-enabled framebuffer.
        # On some Windows GPUs / RDP sessions, alpha_size=8 is silently
        # downgraded to 0 and the window has no alpha channel to composite
        # from — which produces an opaque black background no matter what
        # glClearColor we pick. If this logs 0, the fix is not in Python.
        granted_alpha = getattr(self.context.config, "alpha_size", None)
        logger.info("GL config granted alpha_size=%s (need 8 for transparency)", granted_alpha)

        self.set_location(x, y)
        self._renderer = renderer
        self._bubble = bubble
        self._render_scale = render_scale
        self._y_nudge_px = y_nudge_px
        self._hud_renderer = hud_renderer
        self._sprite_region_x = sprite_region_x
        self._sprite_region_w = sprite_region_w if sprite_region_w is not None else width

        self._image: pyglet.image.AbstractImage | None = None
        self._sprite: pyglet.sprite.Sprite | None = None
        self._label: pyglet.text.Label | None = None
        self._badge_label: pyglet.text.Label | None = None
        self._cancel_badge_label: pyglet.text.Label | None = None
        self._processing_badge_label: pyglet.text.Label | None = None
        self._dictating = False
        self._processing = False
        self._cancelled_cue = False
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

    def set_dictating(self, dictating: bool) -> None:
        self._dictating = dictating

    def set_processing(self, processing: bool) -> None:
        """Show (True) or hide (False) the 'PROCESSING…' badge.

        Called by the main update loop when the sprite enters the
        ``PROCESSING`` state (``dictation.processing`` SSE event received).
        The badge indicates the daemon has captured audio and is awaiting
        the server's Whisper + LLM round-trip (ADR 0096 D5).
        """
        self._processing = processing

    def set_cancelled_cue(self, cancelled: bool) -> None:
        """Show (True) or hide (False) the transient 'CANCELLED' badge.

        Called by the main update loop when ``StateMachine.cancelled_cue``
        transitions.  The badge is distinct from the DICTATING badge: it uses
        a red-orange colour and different text so the user can tell at a
        glance that dictation was *discarded*, not merely stopped.
        The caller is responsible for scheduling the auto-clear (typically
        2.5 s via ``pyglet.clock.schedule_once``).
        """
        self._cancelled_cue = cancelled

    def on_draw(self) -> None:
        # Re-assert transparent clear color every frame. Canonical Windows
        # transparent-overlay pattern per pyglet issue #1271 and upstream
        # docs — pyglet internals can reset glClearColor between frames, so
        # setting it only at init results in opaque black background.
        pyglet.gl.glClearColor(0, 0, 0, 0)
        self.clear()
        if self._image is None:
            return

        frame_key = self._renderer.frame_region
        x, y, w, h = frame_key
        # pyglet.image.get_region uses bottom-up y; the charsheet itself uses
        # top-down rows (row 0 = top of PNG). Convert with img_h - (y + h).
        if frame_key != self._cached_frame_key or self._cached_region is None:
            img_h = self._image.height
            self._cached_region = self._image.get_region(x, _flip_y(img_h, y, h), w, h)
            self._cached_frame_key = frame_key
        region = self._cached_region

        # Fit the source frame into the sprite sub-region of the window,
        # then apply the configurable render_scale shrink factor.
        # render_scale=1.0 fills the region edge-to-edge; 0.75 adds margin.
        region_w = self._sprite_region_w
        region_h = self.height
        fit_scale = _compute_fit_scale(region_w, region_h, w, h)
        final_scale = fit_scale * self._render_scale
        disp_w, disp_h = _compute_display_size(w, h, final_scale)
        sprite_x, sprite_y = _compute_sprite_xy(
            self._sprite_region_x, region_w, region_h, disp_w, disp_h, self._y_nudge_px
        )

        if self._sprite is None:
            self._sprite = pyglet.sprite.Sprite(region, x=sprite_x, y=sprite_y)
            # Explicit per-sprite blend mode. pyglet 2.x sprites use their
            # own shader+blend state that does NOT inherit the window-level
            # glBlendFuncSeparate we set in __init__, so we set it here too.
            # Without this, sprite pixels can land with wrong alpha and the
            # transparent framebuffer looks muddy / black-fringed on Windows.
            gl = pyglet.gl
            self._sprite.blend_src = gl.GL_SRC_ALPHA  # type: ignore[attr-defined]
            self._sprite.blend_dest = gl.GL_ONE_MINUS_SRC_ALPHA  # type: ignore[attr-defined]
        elif self._sprite.image is not region:
            self._sprite.image = region
        self._sprite.x = sprite_x
        self._sprite.y = sprite_y
        self._sprite.scale = final_scale
        self._sprite.color = self._mute_color if self._muted else (255, 255, 255)
        self._sprite.draw()

        if self._hud_renderer is not None:
            self._hud_renderer.draw()

        if self._bubble.visible:
            lx, ly = _compute_label_xy(self.width, self.height)
            lc = _compute_label_color(self._bubble.opacity)
            if self._label is None:
                self._label = pyglet.text.Label(
                    self._bubble.text,
                    font_name="Segoe UI",
                    font_size=10,
                    x=lx,
                    y=ly,
                    anchor_x="center",
                    anchor_y="top",
                    color=lc,
                )
            else:
                self._label.text = self._bubble.text
                self._label.color = lc
            self._label.draw()

        if self._dictating:
            if self._badge_label is None:
                self._badge_label = pyglet.text.Label(
                    "● DICTATING",
                    font_name="Segoe UI",
                    font_size=9,
                    weight="bold",
                    x=self.width // 2,
                    y=2,
                    anchor_x="center",
                    anchor_y="bottom",
                    color=(245, 194, 66, 255),
                )
            # Re-centre every frame: CursorDock resizes the window when the
            # sprite crosses a monitor with a different DPI, so a width
            # captured once at label creation goes stale mid-dictation.
            self._badge_label.x = self.width // 2
            self._badge_label.draw()

        if self._processing:
            if self._processing_badge_label is None:
                self._processing_badge_label = pyglet.text.Label(
                    "⏳ PROCESSING…",
                    font_name="Segoe UI",
                    font_size=9,
                    weight="bold",
                    x=self.width // 2,
                    y=2,
                    anchor_x="center",
                    anchor_y="bottom",
                    color=(100, 200, 255, 255),  # light-blue, distinct from DICTATING amber
                )
            # Re-centre every frame in case CursorDock resizes the window.
            self._processing_badge_label.x = self.width // 2
            self._processing_badge_label.draw()

        if self._cancelled_cue:
            if self._cancel_badge_label is None:
                self._cancel_badge_label = pyglet.text.Label(
                    "✕ CANCELLED",
                    font_name="Segoe UI",
                    font_size=9,
                    weight="bold",
                    x=self.width // 2,
                    y=2,
                    anchor_x="center",
                    anchor_y="bottom",
                    color=(255, 90, 90, 255),
                )
            # Re-centre every frame in case CursorDock resizes the window.
            self._cancel_badge_label.x = self.width // 2
            self._cancel_badge_label.draw()

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
