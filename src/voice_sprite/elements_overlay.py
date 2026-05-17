"""Transparent click-through overlay that draws numbered hint tags (ADR 0087).

The overlay covers exactly the monitor that hosts the scanned window, so all
tag coordinates share one DPI. Element rects arrive in screen coordinates;
``tag_xy`` converts them to the window's bottom-left pyglet origin.

Lifecycle: ONE persistent ``ElementsOverlayWindow`` is created lazily on first
use and then REUSED across every ``elements.show`` / ``elements.hide`` cycle —
it is never destroyed between scans. This mirrors the ``PickerModalWindow``
pattern and avoids the DWM async-destruction race that caused opaque-black
rendering on the 2nd+ overlay invocation (see ADR 0087 and
docs/references/elements-sweep-1-overlay-lifecycle.md Finding 1).
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

    **Lifecycle — single persistent instance, never destroyed between scans.**

    Created ONCE (hidden) via :meth:`__init__`, then toggled with
    :meth:`update_and_show` / :meth:`hide`.  ``apply_win32_flags()`` is
    called exactly once at construction time.  This eliminates the DWM
    async-destruction race (Finding 1 in the adversarial sweep) that caused
    opaque-black rendering on the 2nd+ invocation when the old code called
    ``close()`` + ``ElementsOverlayWindow()`` inside the same clock callback.
    """

    def __init__(self) -> None:
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
        # Created hidden (visible=False) so the window exists in DWM's
        # window list before any content is drawn. We show it later via
        # show_noactivate() which uses SetWindowPos(SWP_NOACTIVATE) to avoid
        # stealing foreground from the target application (critical — this
        # feature then clicks an element in that app).
        super().__init__(
            width=1,
            height=1,
            style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
            config=gl_config,
            vsync=False,
            visible=False,
        )

        # Make this window's GL context current before any GL state calls.
        # Mirrors picker_modal.py's refresh() pattern (see comment there about
        # GL error 0x1282 / wrong context). super().__init__() may leave a
        # different window's context current on some pyglet versions.
        self.switch_to()

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

        self._batch = pyglet.graphics.Batch()
        self._shapes: list[Any] = []
        self._labels: list[Any] = []

        # Apply Win32 flags ONCE at construction time. Never called again on
        # reuse — DWM state is preserved across show/hide cycles because the
        # HWND is never destroyed.
        self.apply_win32_flags()

    # ------------------------------------------------------------------
    # Public show / hide API — called from __main__.py on the GL thread
    # ------------------------------------------------------------------

    def update_and_show(
        self,
        monitor_rect: tuple[int, int, int, int],
        elements: list[dict[str, Any]],
    ) -> None:
        """Resize/relocate to *monitor_rect*, rebuild tag shapes, then show.

        Safe to call on every ``elements.show`` event — the window is reused,
        not recreated. The monitor may differ between scans (a new scan on a
        different physical screen), so position and size are always updated.
        """
        ml, mt, mr, mb = monitor_rect
        new_w = mr - ml
        new_h = mb - mt

        # Resize before moving — set_size then set_location to match the
        # target monitor exactly.
        self.set_size(new_w, new_h)
        self.set_location(ml, mt)

        # Switch to this window's GL context before any GL work (shapes/labels
        # create GL handles; they must be bound to this window's context so
        # on_draw — which always runs under this context — can draw them).
        # Mirrors picker_modal.py's refresh() pattern for GL error 0x1282.
        self.switch_to()

        # Discard previous tag geometry.
        for lbl in self._labels:
            lbl.delete()
        self._labels = []
        import contextlib

        for shp in self._shapes:
            with contextlib.suppress(Exception):
                shp.delete()
        self._shapes = []

        # Build new tag geometry for this scan's element list.
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

        logger.info(
            "elements overlay update_and_show monitor=%s elements=%d",
            monitor_rect,
            len(elements),
        )

        self.show_noactivate()

    def hide(self) -> None:
        """Hide the overlay without destroying it.

        The window (and its DWM transparency state) is preserved so the next
        ``update_and_show`` call can reuse the same HWND without any DWM race.
        """
        self.set_visible(False)
        logger.info("elements overlay hidden")

    def show_noactivate(self) -> None:
        """Show the overlay without activating it.

        Mirrors ``_PygletModalWindow.show_noactivate()`` in ``picker_modal.py``
        — uses raw ``SetWindowPos`` with ``SWP_NOACTIVATE`` so the overlay
        renders on top but never steals foreground from the user's application
        (critical: elements mode then clicks an element in that app).
        """
        if platform.system() != "Windows":
            # Non-Windows fallback — plain set_visible is acceptable in CI.
            self.set_visible(True)
            return

        import ctypes
        from ctypes import wintypes

        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        SWP_NOACTIVATE = 0x0010
        user32 = ctypes.windll.user32
        # MUST set argtypes — ctypes' default int → c_int (32-bit) silently
        # truncates HWND on x64. HWND_TOPMOST=-1 then arrives as a wrong
        # value, SetWindowPos can't match it and returns FALSE → overlay
        # never shows.
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        hwnd = self._overlay_hwnd()
        if hwnd is None:
            logger.error("show_noactivate: could not obtain HWND; falling back to set_visible")
            self.set_visible(True)
            return
        ok = user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE,
        )
        logger.info(
            "elements overlay show_noactivate hwnd=%d SetWindowPos=%s",
            hwnd,
            bool(ok),
        )
        try:
            self.dispatch_event("_on_internal_resize", self._width, self._height)
            self.dispatch_event("on_show")
        except Exception:  # noqa: BLE001
            logger.exception("dispatch_event during show_noactivate failed")
        self._visible = True

    # ------------------------------------------------------------------
    # pyglet event handlers
    # ------------------------------------------------------------------

    def on_draw(self) -> None:
        # Re-assert transparent clear color every frame. Canonical Windows
        # transparent-overlay pattern per pyglet issue #1271 and upstream
        # docs — pyglet internals can reset glClearColor between frames, so
        # setting it only at init results in opaque black background.
        pyglet.gl.glClearColor(0, 0, 0, 0)
        self.clear()
        self._batch.draw()

    # ------------------------------------------------------------------
    # Win32 helpers
    # ------------------------------------------------------------------

    def _overlay_hwnd(self) -> int | None:
        """Top-level HWND of this window.

        Returns ``self._hwnd`` directly (the top-level parent HWND) so that
        ``apply_click_through`` and ``show_noactivate`` operate on the correct
        handle.  ``canvas.hwnd`` is the child view HWND — WS_EX_TOOLWINDOW,
        WS_EX_NOACTIVATE, SetWindowPos Z-ordering and DwmEnableBlurBehindWindow
        are all no-ops on child windows (Finding 3 in the adversarial sweep).
        """
        hwnd = getattr(self, "_hwnd", None)
        if hwnd is None:
            has_canvas_hwnd = hasattr(self, "canvas") and hasattr(self.canvas, "hwnd")
            hwnd = self.canvas.hwnd if has_canvas_hwnd else None
        return hwnd

    def apply_win32_flags(self) -> None:
        """Make the window click-through, topmost and non-activating.

        Mirrors the exact pattern from ``SpriteWindow.apply_win32_flags`` in
        ``window.py`` — delegates to ``win32_flags.apply_click_through``,
        whose ``DwmEnableBlurBehindWindow`` call is what makes the cleared
        framebuffer composite transparently against the desktop.

        Called ONCE at construction time. Never called again on reuse — DWM
        transparency state is preserved across show/hide cycles.
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
