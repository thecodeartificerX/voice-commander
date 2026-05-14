"""Always-on-top centred modal window rendered by the sprite process.

The modal mirrors :class:`voice_commander.picker.session.PickerSession` state
via two SSE events — ``picker.open`` (with ``items`` payload) and
``picker.close``.

Rendering math is split out as pure functions so it can be unit-tested
without pyglet. The :class:`PickerModalWindow` glues those pieces to
pyglet at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModalGeometry:
    width: int = 320
    height: int = 200


@dataclass
class ModalState:
    verb: str = ""
    rows: list[str] = field(default_factory=list)
    visible: bool = False

    def open(self, verb: str, items: list[dict[str, Any]]) -> None:
        self.verb = verb
        self.rows = format_rows(items)
        self.visible = True

    def close(self) -> None:
        self.verb = ""
        self.rows = []
        self.visible = False


def format_rows(items: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for item in items:
        n = item.get("n")
        label = item.get("label", "")
        if n is None:
            continue
        out.append(f"{n}. {label}")
    return out


def compute_modal_position(
    geometry: ModalGeometry,
    work_area: tuple[int, int, int, int],
) -> tuple[int, int]:
    """Return ``(x, y)`` for a centred modal within *work_area* (left, top, w, h)."""
    left, top, width, height = work_area
    x = left + (width - geometry.width) // 2
    y = top + (height - geometry.height) // 2
    return x, y


# ---------------------------------------------------------------------------
# Pyglet glue (only imported when the sprite process actually runs)
# ---------------------------------------------------------------------------


class PickerModalWindow:
    """Lazy pyglet window holder — show()/hide() driven by SSE events."""

    def __init__(self, geometry: ModalGeometry | None = None) -> None:
        self._geometry = geometry or ModalGeometry()
        self._state = ModalState()
        self._window: Any = None

    @property
    def state(self) -> ModalState:
        return self._state

    def show(self, verb: str, items: list[dict[str, Any]]) -> None:
        """Open the modal. Safe to call from any thread.

        State mutation happens here; window creation + GL work is deferred to
        the pyglet main thread via :func:`pyglet.clock.schedule_once` so the
        SSE handler thread never touches a GL context.
        """
        logger.info(
            "picker_modal.show verb=%s items=%d preview=%r",
            verb,
            len(items),
            [it.get("label", "") for it in items[:3]],
        )
        self._state.open(verb, items)
        self._schedule_on_main(self._do_show)

    def hide(self) -> None:
        """Hide the modal. Safe to call from any thread (see :meth:`show`)."""
        self._state.close()
        self._schedule_on_main(self._do_hide)

    # ---- pyglet wiring (runs on the pyglet main thread only) ----

    def _schedule_on_main(self, fn: Any) -> None:
        try:
            import pyglet
        except ImportError:
            logger.warning("pyglet not available; picker modal will not render")
            return
        # schedule_once is the canonical pyglet-2 way to marshal a callable
        # back onto the event-loop thread that owns every GL context.
        pyglet.clock.schedule_once(lambda _dt: fn(), 0.0)

    def _do_show(self) -> None:
        self._ensure_window()
        if self._window is None:
            return
        # NB: do NOT call pyglet's set_visible(True). It calls self.activate()
        # which steals foreground from the user's app — Windows then refuses
        # the daemon's subsequent SetForegroundWindow because foreground was
        # taken by the sprite process, not the user. Use ShowWindow(SW_SHOWNA)
        # (show without activating) directly so the modal renders on top but
        # never takes the active-window slot.
        self._window.show_noactivate()
        try:
            self._reposition()
        except Exception:
            logger.exception("modal repositioning failed")
        self._window.refresh()

    def _do_hide(self) -> None:
        if self._window is not None:
            self._window.set_visible(False)

    def _ensure_window(self) -> None:
        if self._window is not None:
            return
        try:
            import pyglet  # noqa: F401
        except ImportError:
            logger.warning("pyglet not available; picker modal will not render")
            return
        self._window = _PygletModalWindow(self._state, self._geometry)
        self._window.apply_win32_flags()

    def _reposition(self) -> None:
        if self._window is None:
            return
        work_area = _get_cursor_work_area()
        x, y = compute_modal_position(self._geometry, work_area)
        self._window.set_location(x, y)


def _get_cursor_work_area() -> tuple[int, int, int, int]:
    """Return the work area (left, top, width, height) of the cursor's monitor."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        logger.exception("cursor work-area lookup failed; defaulting to (0,0,1920,1080)")
    return 0, 0, 1920, 1080


try:  # pragma: no cover — only loaded inside the sprite process
    import pyglet  # noqa: F401
except ImportError:
    _PygletModalWindow = None  # type: ignore[assignment]
else:
    import pyglet

    class _PygletModalWindow(pyglet.window.Window):  # type: ignore[misc]
        def __init__(self, state: ModalState, geometry: ModalGeometry) -> None:
            super().__init__(
                width=geometry.width,
                height=geometry.height,
                caption="vc-picker",
                resizable=False,
                visible=False,
                style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
            )
            self._state = state
            self._batch = pyglet.graphics.Batch()
            self._labels: list[pyglet.text.Label] = []

        def apply_win32_flags(self) -> None:
            try:
                from voice_sprite.win32_flags import apply_click_through

                apply_click_through(self._hwnd)
            except Exception:
                logger.exception("apply_click_through failed for picker modal")

        def show_noactivate(self) -> None:
            """Show the modal without activating it.

            Mirrors pyglet's :meth:`set_visible(True)` (which uses
            ``SetWindowPos`` with ``SWP_SHOWWINDOW``) but adds
            ``SWP_NOACTIVATE`` and skips the ``self.activate()`` call —
            both of which would otherwise steal foreground from the
            user's app and break the daemon's subsequent focus call.

            ``HWND_TOPMOST`` keeps the modal above the user's windows
            without needing them to lose foreground.
            """
            import ctypes

            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040
            SWP_NOACTIVATE = 0x0010
            user32 = ctypes.windll.user32
            ok = user32.SetWindowPos(
                self._hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
            logger.info(
                "picker_modal.show_noactivate hwnd=%d SetWindowPos=%s",
                self._hwnd,
                bool(ok),
            )
            try:
                self.dispatch_event("_on_internal_resize", self._width, self._height)
                self.dispatch_event("on_show")
            except Exception:
                logger.exception("dispatch_event during show_noactivate failed")
            self._visible = True

        def refresh(self) -> None:
            # Make this window's GL context current before any GL work.
            # refresh() runs from a clock callback whose ambient context is
            # whichever window pyglet drew last (usually the sprite's main
            # window). Constructing Labels under the wrong context binds
            # their VAOs/textures there, then on_draw — which runs under
            # *this* window's context — hits GL 0x1282 invalid operation.
            self.switch_to()

            # Discard previous labels.
            for lbl in self._labels:
                lbl.delete()
            self._labels = []

            logger.info(
                "picker_modal.refresh verb=%s rows=%d preview=%r window_size=%dx%d",
                self._state.verb,
                len(self._state.rows),
                self._state.rows[:3],
                self.width,
                self.height,
            )

            header = pyglet.text.Label(
                f"{self._state.verb.upper()} — SAY NUMBER",
                font_name="Segoe UI",
                font_size=10,
                weight="bold",
                x=12,
                y=self.height - 22,
                anchor_x="left",
                anchor_y="top",
                color=(255, 204, 0, 255),
                batch=self._batch,
            )
            self._labels.append(header)

            for i, row in enumerate(self._state.rows):
                lbl = pyglet.text.Label(
                    row,
                    font_name="Segoe UI",
                    font_size=12,
                    x=12,
                    y=self.height - 44 - i * 22,
                    anchor_x="left",
                    anchor_y="top",
                    color=(228, 231, 236, 255),
                    batch=self._batch,
                )
                self._labels.append(lbl)

            footer = pyglet.text.Label(
                'say "cancel" to exit',
                font_name="Segoe UI",
                font_size=9,
                x=12,
                y=10,
                anchor_x="left",
                anchor_y="bottom",
                color=(122, 130, 144, 255),
                batch=self._batch,
            )
            self._labels.append(footer)

        def on_draw(self) -> None:
            pyglet.gl.glClearColor(0.13, 0.15, 0.18, 0.95)
            self.clear()
            self._batch.draw()
