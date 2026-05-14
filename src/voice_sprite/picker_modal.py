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


# ---- layout constants (tuned for 7 cards + game-style chrome) -------------

HEADER_H = 48
FOOTER_H = 38
CARD_H = 52
CARD_GAP = 6
CARD_PAD_X = 14
CONTENT_PAD_Y = 10
MAX_CARDS = 7
DEFAULT_W = 560

DEFAULT_H = HEADER_H + CONTENT_PAD_Y + (CARD_H * MAX_CARDS) + (CARD_GAP * (MAX_CARDS - 1)) + CONTENT_PAD_Y + FOOTER_H

# Color palette — dark slate + gold accent.
BG_RGBA = (16, 18, 24, 245)
HEADER_BG = (24, 27, 36)
HEADER_ACCENT = (245, 194, 66)
TITLE_FG = (245, 194, 66, 255)
SUBTITLE_FG = (140, 148, 162, 255)
CARD_BG = (31, 35, 44)
CARD_BORDER = (60, 66, 80)
BADGE_BG = (245, 194, 66)
BADGE_FG = (20, 22, 28, 255)
APP_FG = (235, 238, 244, 255)
TITLE_TEXT_FG = (165, 174, 188, 255)
FOOTER_FG = (110, 118, 132, 255)
FOOTER_KEY_FG = (245, 194, 66, 255)


@dataclass
class ModalGeometry:
    width: int = DEFAULT_W
    height: int = DEFAULT_H


@dataclass
class ModalState:
    verb: str = ""
    rows: list[str] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    visible: bool = False

    def open(self, verb: str, items: list[dict[str, Any]]) -> None:
        self.verb = verb
        self.rows = format_rows(items)
        self.cards = format_cards(items)
        self.visible = True

    def close(self) -> None:
        self.verb = ""
        self.rows = []
        self.cards = []
        self.visible = False


def format_rows(items: list[dict[str, Any]]) -> list[str]:
    """Legacy single-line rendering ("1. Chrome — title"). Kept for tests."""
    out: list[str] = []
    for item in items:
        n = item.get("n")
        label = item.get("label", "")
        if n is None:
            continue
        out.append(f"{n}. {label}")
    return out


def format_cards(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two-line card rendering data: ``[{n, app, title}, ...]``.

    Falls back to splitting ``label`` on ``" — "`` when ``app``/``title``
    aren't supplied (older daemons / legacy provider payloads).
    """
    out: list[dict[str, Any]] = []
    for item in items:
        n = item.get("n")
        if n is None:
            continue
        app = (item.get("app") or "").strip()
        title = (item.get("title") or "").strip()
        if not app and not title:
            label = item.get("label", "")
            if " — " in label:
                app, _, title = label.partition(" — ")
            else:
                app = label
        out.append({"n": int(n), "app": app, "title": title})
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
        # taken by the sprite process, not the user. Use SetWindowPos with
        # SWP_NOACTIVATE directly so the modal renders on top but never
        # takes the active-window slot.
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
    from pyglet import shapes

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
            self._shapes: list[Any] = []
            # Explicit ordered groups so card borders render BEHIND their
            # bodies and text renders ABOVE everything. Without ordered
            # groups pyglet's draw order between same-batch shapes is
            # implementation-defined and the border peeks through.
            self._g_back = pyglet.graphics.Group(order=0)
            self._g_card_border = pyglet.graphics.Group(order=1)
            self._g_card_body = pyglet.graphics.Group(order=2)
            self._g_badge = pyglet.graphics.Group(order=3)
            self._g_text = pyglet.graphics.Group(order=4)

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
            from ctypes import wintypes

            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040
            SWP_NOACTIVATE = 0x0010
            user32 = ctypes.windll.user32
            # MUST set argtypes — ctypes' default int → c_int (32-bit)
            # silently truncates HWND on x64. HWND_TOPMOST=-1 then arrives
            # as 0x????????FFFFFFFF instead of all-FFs, SetWindowPos can't
            # match it in the Z-order and returns FALSE → modal never shows.
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
            # window). Constructing Labels/Shapes under the wrong context
            # binds their GL handles there; subsequent on_draw — which runs
            # under *this* window's context — hits GL 0x1282.
            self.switch_to()

            # Discard previous draw objects.
            for lbl in self._labels:
                lbl.delete()
            self._labels = []
            for shp in self._shapes:
                try:
                    shp.delete()
                except Exception:
                    pass
            self._shapes = []

            logger.info(
                "picker_modal.refresh verb=%s cards=%d preview=%r window_size=%dx%d",
                self._state.verb,
                len(self._state.cards),
                [(c["n"], c["app"]) for c in self._state.cards[:3]],
                self.width,
                self.height,
            )

            self._build_chrome()
            self._build_cards()
            self._build_footer()

        def _build_chrome(self) -> None:
            w = self.width
            h = self.height

            # Header strip
            header = shapes.Rectangle(
                x=0, y=h - HEADER_H, width=w, height=HEADER_H,
                color=HEADER_BG, batch=self._batch, group=self._g_back,
            )
            self._shapes.append(header)

            accent = shapes.Rectangle(
                x=0, y=h - HEADER_H, width=4, height=HEADER_H,
                color=HEADER_ACCENT, batch=self._batch, group=self._g_badge,
            )
            self._shapes.append(accent)

            verb = (self._state.verb or "PICK").upper()
            title = pyglet.text.Label(
                verb,
                font_name="Segoe UI",
                font_size=15,
                weight="bold",
                x=18,
                y=h - HEADER_H // 2,
                anchor_x="left",
                anchor_y="center",
                color=TITLE_FG,
                batch=self._batch,
                group=self._g_text,
            )
            self._labels.append(title)

            subtitle = pyglet.text.Label(
                "say a number",
                font_name="Segoe UI",
                font_size=10,
                x=w - 18,
                y=h - HEADER_H // 2,
                anchor_x="right",
                anchor_y="center",
                color=SUBTITLE_FG,
                batch=self._batch,
                group=self._g_text,
            )
            self._labels.append(subtitle)

        def _build_cards(self) -> None:
            w = self.width
            h = self.height
            cards_top = h - HEADER_H - CONTENT_PAD_Y
            card_w = w - (CARD_PAD_X * 2)

            for i, card in enumerate(self._state.cards[:MAX_CARDS]):
                card_y = cards_top - CARD_H - i * (CARD_H + CARD_GAP)
                self._draw_card(CARD_PAD_X, card_y, card_w, card)

        def _draw_card(self, x: int, y: int, w: int, card: dict[str, Any]) -> None:
            n = card["n"]
            app = card.get("app") or ""
            title = card.get("title") or ""

            # Border (drawn 1px larger, lower group → behind body)
            border = shapes.RoundedRectangle(
                x=x - 1, y=y - 1, width=w + 2, height=CARD_H + 2, radius=11,
                color=CARD_BORDER, batch=self._batch, group=self._g_card_border,
            )
            self._shapes.append(border)

            # Card body (rounded)
            body = shapes.RoundedRectangle(
                x=x, y=y, width=w, height=CARD_H, radius=10,
                color=CARD_BG, batch=self._batch, group=self._g_card_body,
            )
            self._shapes.append(body)

            # Number badge (gold rounded square, 36×36, vertically centred)
            badge_size = 36
            badge_x = x + 10
            badge_y = y + (CARD_H - badge_size) // 2
            badge = shapes.RoundedRectangle(
                x=badge_x, y=badge_y, width=badge_size, height=badge_size, radius=8,
                color=BADGE_BG, batch=self._batch, group=self._g_badge,
            )
            self._shapes.append(badge)

            badge_label = pyglet.text.Label(
                str(n),
                font_name="Segoe UI",
                font_size=16,
                weight="bold",
                x=badge_x + badge_size // 2,
                y=badge_y + badge_size // 2,
                anchor_x="center",
                anchor_y="center",
                color=BADGE_FG,
                batch=self._batch,
                group=self._g_text,
            )
            self._labels.append(badge_label)

            # App name (bold, primary line). Baseline anchored well above
            # card centre so the title row below has clean breathing room.
            text_x = badge_x + badge_size + 14
            text_w = w - (text_x - x) - 12
            app_label = pyglet.text.Label(
                app or "(unknown)",
                font_name="Segoe UI",
                font_size=12,
                weight="bold",
                x=text_x,
                y=y + 32,
                anchor_x="left",
                anchor_y="baseline",
                color=APP_FG,
                batch=self._batch,
                group=self._g_text,
                width=text_w,
                multiline=False,
            )
            self._labels.append(app_label)

            # Window/tab title (smaller, muted) on the lower baseline.
            if title:
                title_label = pyglet.text.Label(
                    title,
                    font_name="Segoe UI",
                    font_size=9,
                    x=text_x,
                    y=y + 12,
                    anchor_x="left",
                    anchor_y="baseline",
                    color=TITLE_TEXT_FG,
                    batch=self._batch,
                    group=self._g_text,
                    width=text_w,
                    multiline=False,
                )
                self._labels.append(title_label)

        def _build_footer(self) -> None:
            w = self.width
            footer_bg = shapes.Rectangle(
                x=0, y=0, width=w, height=FOOTER_H,
                color=HEADER_BG, batch=self._batch, group=self._g_back,
            )
            self._shapes.append(footer_bg)

            footer = pyglet.text.Label(
                'say a number  ·  say "cancel" to exit',
                font_name="Segoe UI",
                font_size=10,
                x=18,
                y=FOOTER_H // 2,
                anchor_x="left",
                anchor_y="center",
                color=FOOTER_FG,
                batch=self._batch,
                group=self._g_text,
            )
            self._labels.append(footer)

        def on_draw(self) -> None:
            r, g, b, a = BG_RGBA
            pyglet.gl.glClearColor(r / 255.0, g / 255.0, b / 255.0, a / 255.0)
            self.clear()
            self._batch.draw()
