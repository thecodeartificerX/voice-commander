"""Transparent click-through overlay that draws numbered hint tags (ADR 0087).

The overlay covers exactly the monitor that hosts the scanned window, so all
tag coordinates share one DPI. Element rects arrive in screen coordinates;
``tag_xy`` converts them to the window's bottom-left pyglet origin.
"""

from __future__ import annotations

import logging
import platform
from typing import Any

import pyglet

logger = logging.getLogger(__name__)

_TAG_H = 22  # tag box height, px
_TAG_PAD = 7  # horizontal padding inside a tag, px
_DIGIT_W = 9  # approximate per-digit width at the chosen font size, px
_TAG_FONT_SIZE = 11  # hint-tag digit font size, pt
_TAG_BG = (255, 221, 0)  # Vimium yellow
_TAG_FG = (0, 0, 0, 255)  # black text


def _label_kwargs(text: str, *, x: int, y: int) -> dict[str, Any]:
    """Static keyword arguments for a hint-tag ``pyglet.text.Label``.

    Extracted from ``ElementsOverlayWindow.__init__`` so a unit test can
    assert every key is a real parameter of the installed pyglet's
    ``Label.__init__`` without needing a GL context. This is the regression
    guard for the pyglet 1.x ``bold=True`` vs pyglet 2.1 ``weight="bold"``
    API change — a wrong kwarg otherwise only surfaces as a crash at
    window-construction time, which no other automated test exercises.
    """
    return {
        "text": text,
        "font_size": _TAG_FONT_SIZE,
        "weight": "bold",
        "color": _TAG_FG,
        "x": x,
        "y": y,
        "anchor_x": "center",
        "anchor_y": "center",
    }


def tag_xy(
    element_rect: tuple[int, int, int, int],
    monitor_rect: tuple[int, int, int, int],
    *,
    tag_w: int,
    tag_h: int,
) -> tuple[int, int]:
    """Convert an element's screen rect to the tag's bottom-left position in
    the overlay window's pyglet coordinate space (origin bottom-left)."""
    ex, ey, _ew, _eh = element_rect
    ml, mt, _mr, mb = monitor_rect
    monitor_height = mb - mt
    local_x = ex - ml
    local_y_top = ey - mt
    pyglet_y = monitor_height - local_y_top - tag_h
    return (local_x, pyglet_y)


def _tag_width(text: str) -> int:
    return _TAG_PAD * 2 + _DIGIT_W * len(text)


class ElementsOverlayWindow(pyglet.window.Window):  # type: ignore[misc]
    """A borderless, transparent, click-through, topmost overlay window.

    Follows the same canonical pyglet 2.1.8+ transparent-overlay recipe as
    ``SpriteWindow`` (see ``window.py`` and pyglet issue #1271):

    1. Explicit ``gl.Config(alpha_size=8, double_buffer=True)`` guarantees an
       alpha-enabled framebuffer even if pyglet's style-driven auto-config
       misses it on some drivers.
    2. ``style=WINDOW_STYLE_OVERLAY`` wires ``WS_POPUP | WS_EX_LAYERED |
       WS_EX_TRANSPARENT`` and calls DwmEnableBlurBehindWindow — borderless,
       click-through, topmost, per-pixel-alpha in one flag.
    3. ``glEnable(GL_BLEND)`` + ``glBlendFuncSeparate`` with ``GL_ONE`` for
       the alpha source so tag pixels composite over the transparent clear
       without collapsing the framebuffer alpha channel. Without this,
       Windows DWM composites the whole window as opaque black.
    4. ``glClearColor(0, 0, 0, 0)`` is set every frame inside ``on_draw``
       before ``window.clear()`` — canonical Windows transparent-overlay
       pattern per pyglet issue #1271 (pyglet internals can reset the clear
       color between frames).
    """

    def __init__(
        self,
        monitor_rect: tuple[int, int, int, int],
        elements: list[dict[str, Any]],
    ) -> None:
        ml, mt, mr, mb = monitor_rect
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
        # Created visible (no visible=False): pyglet's _create() then runs
        # _set_transparency() — DwmEnableBlurBehindWindow + SetLayeredWindow-
        # Attributes — and shows the window in one step. This is the exact
        # lifecycle SpriteWindow uses. Creating hidden and showing later
        # leaves a window whose layered per-pixel alpha never composites,
        # so DWM draws an opaque black background.
        super().__init__(
            width=mr - ml,
            height=mb - mt,
            style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
            config=gl_config,
            vsync=False,
        )
        # Alpha compositing — tag pixels blend over the transparent clear.
        # The separate alpha blend (GL_ONE for the alpha source) is critical
        # on Windows layered windows: a plain glBlendFunc(SRC_ALPHA,
        # ONE_MINUS_SRC_ALPHA) multiplies destination alpha toward zero and
        # DWM ends up compositing the whole window as opaque black instead
        # of transparent. Mirrors SpriteWindow.__init__ in window.py.
        gl = pyglet.gl
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFuncSeparate(
            gl.GL_SRC_ALPHA,
            gl.GL_ONE_MINUS_SRC_ALPHA,
            gl.GL_ONE,
            gl.GL_ONE_MINUS_SRC_ALPHA,
        )
        gl.glClearColor(0, 0, 0, 0)

        # Verify the driver actually granted an alpha-enabled framebuffer.
        # On some Windows GPUs / RDP sessions alpha_size=8 is silently
        # downgraded to 0, leaving no alpha channel to composite from — an
        # opaque black background no matter what glClearColor is set. If
        # this logs 0, the fix is not in Python.
        granted_alpha = getattr(self.context.config, "alpha_size", None)
        logger.info(
            "elements overlay GL config granted alpha_size=%s (need 8 for transparency)",
            granted_alpha,
        )

        self.set_location(ml, mt)
        self._batch = pyglet.graphics.Batch()
        self._shapes: list[Any] = []
        self._labels: list[Any] = []
        for element in elements:
            text = str(element["index"])
            tag_w = _tag_width(text)
            x, y = tag_xy(
                tuple(element["rect"]),
                monitor_rect,
                tag_w=tag_w,
                tag_h=_TAG_H,
            )
            self._shapes.append(
                pyglet.shapes.Rectangle(
                    x, y, tag_w, _TAG_H, color=_TAG_BG, batch=self._batch
                )
            )
            self._labels.append(
                pyglet.text.Label(
                    **_label_kwargs(text, x=x + tag_w // 2, y=y + _TAG_H // 2),
                    batch=self._batch,
                )
            )

    def on_draw(self) -> None:
        # Re-assert transparent clear color every frame. Canonical Windows
        # transparent-overlay pattern per pyglet issue #1271 and upstream
        # docs — pyglet internals can reset glClearColor between frames, so
        # setting it only at init results in opaque black background.
        pyglet.gl.glClearColor(0, 0, 0, 0)
        self.clear()
        self._batch.draw()

    def _overlay_hwnd(self) -> int | None:
        """HWND of this window — ``canvas.hwnd`` with a ``_hwnd`` fallback."""
        hwnd = self.canvas.hwnd if hasattr(self.canvas, "hwnd") else None
        if hwnd is None:
            hwnd = getattr(self, "_hwnd", None)
        return hwnd

    def apply_win32_flags(self) -> None:
        """Make the window click-through, topmost and non-activating.

        Mirrors the exact pattern from ``SpriteWindow.apply_win32_flags`` in
        ``window.py`` — delegates to ``win32_flags.apply_click_through``,
        whose ``DwmEnableBlurBehindWindow`` call is what makes the cleared
        framebuffer composite transparently against the desktop.
        """
        if platform.system() != "Windows":
            logger.warning("Win32 flags only apply on Windows")
            return
        from .win32_flags import apply_click_through

        hwnd = self._overlay_hwnd()
        if hwnd is None:
            logger.error("Could not obtain HWND for elements overlay window")
            return
        apply_click_through(hwnd)
