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
_TAG_BG = (255, 221, 0)  # Vimium yellow
_TAG_FG = (0, 0, 0, 255)  # black text


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
    3. ``glClearColor(0, 0, 0, 0)`` is set every frame inside ``on_draw``
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
        super().__init__(
            width=mr - ml,
            height=mb - mt,
            style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
            config=gl_config,
            vsync=False,
            visible=False,
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
                pyglet.text.Label(  # type: ignore[call-arg]  # pyglet stub lacks bold=
                    text,
                    font_size=11,
                    bold=True,
                    color=_TAG_FG,
                    x=x + tag_w // 2,
                    y=y + _TAG_H // 2,
                    anchor_x="center",
                    anchor_y="center",
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

    def apply_win32_flags(self) -> None:
        """Make the window click-through, topmost and non-activating.

        Mirrors the exact pattern from ``SpriteWindow.apply_win32_flags`` in
        ``window.py`` — HWND lookup via ``canvas.hwnd`` with ``_hwnd``
        fallback, then delegates to ``win32_flags.apply_click_through``.
        """
        if platform.system() != "Windows":
            logger.warning("Win32 flags only apply on Windows")
            return
        from .win32_flags import apply_click_through

        hwnd = self.canvas.hwnd if hasattr(self.canvas, "hwnd") else None
        if hwnd is None:
            # Fallback for older pyglet versions — 2.x HWND access path
            hwnd = getattr(self, "_hwnd", None)
        if hwnd is None:
            logger.error("Could not obtain HWND for elements overlay window")
            return
        apply_click_through(hwnd)
