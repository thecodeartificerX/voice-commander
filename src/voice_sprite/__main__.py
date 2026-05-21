from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

logger = logging.getLogger("voice_sprite")


def _configure_logging() -> None:
    os.makedirs("outputs", exist_ok=True)
    handler = RotatingFileHandler(
        "outputs/sprite.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(threadName)s %(name)s %(levelname)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
        force=True,
    )


def _should_reload(mtime_old: float, mtime_new: float, threshold: float = 0.0) -> bool:
    """Return True if mtimes differ by more than *threshold* seconds (strict >)."""
    return abs(mtime_new - mtime_old) > threshold


# Duration the "CANCELLED" badge stays visible before auto-clearing.
_CANCELLED_CUE_DURATION_S: float = 2.5


def _apply_processing_state(
    sm: Any,
    window: Any,
) -> None:
    """Show or clear the processing badge on *window* based on *sm.processing*.

    When ``sm.processing`` is ``True`` the badge is made visible.
    When ``sm.processing`` is ``False`` the badge is hidden immediately.

    This helper is module-level (not a closure) so it can be unit-tested
    without spinning up a full pyglet window.  Mirrors the ``_apply_cancelled_cue``
    pattern (ADR 0096 D5).
    """
    window.set_processing(sm.processing)


def _apply_cancelled_cue(
    sm: Any,
    window: Any,
    schedule_fn: Any,
) -> None:
    """Show or clear the cancelled-cue badge on *window* based on *sm.cancelled_cue*.

    When ``sm.cancelled_cue`` is ``True`` the badge is made visible and a
    timer is scheduled (via *schedule_fn*, which must accept a
    ``(callback, delay)`` signature matching ``pyglet.clock.schedule_once``)
    to auto-clear it after ``_CANCELLED_CUE_DURATION_S`` seconds.  The auto-
    clear also resets ``sm.cancelled_cue`` so subsequent ticks do not
    re-schedule.

    When ``sm.cancelled_cue`` is ``False`` the badge is hidden immediately.

    This helper is module-level (not a closure) so it can be unit-tested
    without spinning up a full pyglet window.
    """
    if sm.cancelled_cue:
        window.set_cancelled_cue(True)

        def _clear_cue(_dt: float) -> None:
            sm.cancelled_cue = False
            window.set_cancelled_cue(False)

        schedule_fn(_clear_cue, _CANCELLED_CUE_DURATION_S)
    else:
        window.set_cancelled_cue(False)


def _apply_dim(sm: Any, window: Any) -> None:
    """Dim the sprite whenever it is NOT listening (ADR 0097).

    Bright (full brightness) for any in-session listening pose; dim for no
    active session (IDLE/WARMUP/CRASHED) or the dictation decode wait
    (``processing``). Dictation *capture* carries a session state with
    ``processing=False`` so the cat stays bright while the mic is hot.

    Uses ``sm.target_state`` (the intended semantic state), NOT
    ``sm.current_state``: the latter can lag on an ``ANIMATED_TRANSITIONS``
    pair (e.g. IDLE→LISTENING) — nothing advances ``current_state`` past the
    play-once animation, so it would hold the dim source state. The renderer
    is likewise driven by the target state.

    Module-level (not a closure) so it can be unit-tested without a pyglet
    window — mirrors ``_apply_processing_state`` / ``_apply_cancelled_cue``.
    """
    # Deferred like all voice_sprite submodule imports (see main()): keeps module
    # import side-effect-free. state_machine has no pyglet dep, so this is safe here.
    from .state_machine import is_dim

    window.set_dim(is_dim(sm.target_state, sm.processing))


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-sprite",
        description="On-screen sprite companion for Voice Commander.",
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to Voice Commander config.toml (default: config.toml)",
    )
    return parser


def main() -> None:
    args = _make_parser().parse_args()

    _configure_logging()
    logger.info("voice-sprite starting")

    # Must run BEFORE the first pyglet window is constructed.
    import contextlib
    import ctypes

    with contextlib.suppress(AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)

    from .charsheet import CharSheetError, load_charsheet
    from .config import load_sprite_config
    from .dpi import get_primary_dpi
    from .event_client import SSEClient
    from .speech_bubble import SpeechBubble
    from .sprite_renderer import SpriteRenderer
    from .state_machine import SpriteState, StateMachine

    # Load config
    config_path = Path(args.config)
    cfg = load_sprite_config(config_path)
    logger.info("Config loaded: %s", cfg)

    # DPI scaling
    dpi = get_primary_dpi()
    scale = dpi / 96.0
    size = int(cfg.base_size_px * scale)
    logger.info("DPI=%d, scale=%.2f, size=%dpx", dpi, scale, size)

    # Load charsheet
    asset_dir = Path(cfg.asset_path)
    toml_path = asset_dir / "charsheet.toml"
    png_path = asset_dir / "charsheet.png"

    if not png_path.exists():
        logger.error("Charsheet PNG not found: %s", png_path)
        print(
            f"ERROR: {png_path} not found. "
            "See assets/sprite/README.md for generation instructions.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        charsheet = load_charsheet(toml_path, png_path)
    except CharSheetError as e:
        logger.error("Charsheet validation failed: %s", e)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # State machine + renderer + bubble
    sm = StateMachine(heartbeat_timeout_ms=cfg.heartbeat_timeout_ms)
    renderer = SpriteRenderer(charsheet)
    bubble = SpeechBubble(fade_ms=cfg.bubble_fade_ms)

    # Chat-log + summarizer stack
    from .chat_log import ChatLog
    from .chat_log_renderer import ChatLogRenderer
    from .plan_outcome_handler import (
        handle_plan_outcome,
        handle_tool_fired,
        handle_transcript,
    )

    chat_log = ChatLog(
        max_lines=cfg.hud.max_lines,
        hold_ms=cfg.hud.hold_ms,
        fade_ms=cfg.hud.fade_ms,
    )

    from .elements_overlay import ElementsOverlayWindow
    from .picker_modal import PickerModalWindow

    picker_modal = PickerModalWindow()

    # Deferred pyglet import — avoids display probe at module-load time.
    # ImportError here means pyglet/GL libs missing; surface as startup
    # failure rather than import failure. pyglet 2.x lazy-loads submodules
    # so `pyglet.display` must be imported explicitly; bare `pyglet` does
    # not expose `.display` / `.canvas` attribute access.
    import pyglet
    import pyglet.display

    # Calculate position
    screen = pyglet.display.get_display().get_default_screen()

    # Composite window: HUD to the LEFT of sprite region.
    hud_w = cfg.hud.width_px if cfg.hud.enabled else 0
    window_w = size + hud_w
    window_h = size
    sprite_region_x = hud_w
    sprite_region_w = size

    # Start position: primary bottom-right (updated on first CursorDock tick if follow enabled)
    x = screen.width - window_w - cfg.margin_x
    y = cfg.margin_y

    hud_renderer: ChatLogRenderer | None = None
    if cfg.hud.enabled:
        hud_renderer = ChatLogRenderer(
            chat_log=chat_log,
            hud_width_px=hud_w,
            font_size=cfg.hud.font_size,
            line_gap_px=4,
        )

    from .window import SpriteWindow

    window = SpriteWindow(
        width=window_w,
        height=window_h,
        x=x,
        y=y,
        renderer=renderer,
        bubble=bubble,
        render_scale=cfg.render_scale,
        dim_brightness=cfg.dim_brightness,
        y_nudge_px=cfg.y_nudge_px,
        sprite_region_x=sprite_region_x,
        sprite_region_w=sprite_region_w,
        hud_renderer=hud_renderer,
    )

    window.load_charsheet_image(str(png_path))
    window.apply_win32_flags()

    # Cursor-follow docking
    if cfg.follow_cursor:
        from .cursor_tracker import CursorDock

        dock = CursorDock(
            window=window,
            sprite_base_size_px=cfg.base_size_px,
            window_extra_w_px=cfg.hud.width_px if cfg.hud.enabled else 0,
            window_extra_h_px=0,
            margin_x=cfg.margin_x,
            margin_y=cfg.margin_y,
        )
        pyglet.clock.schedule_interval(dock.tick, 1.0 / max(1, cfg.follow_poll_hz))
        logger.info("CursorDock scheduled at %d Hz", cfg.follow_poll_hz)

    # Hot-reload: check charsheet file mtime every 2 seconds
    _last_toml_mtime = toml_path.stat().st_mtime if toml_path.exists() else 0
    _last_png_mtime = png_path.stat().st_mtime if png_path.exists() else 0

    def check_hot_reload(dt: float) -> None:
        nonlocal _last_toml_mtime, _last_png_mtime, charsheet
        try:
            toml_mt = toml_path.stat().st_mtime if toml_path.exists() else 0
            png_mt = png_path.stat().st_mtime if png_path.exists() else 0
            if _should_reload(_last_toml_mtime, toml_mt) or _should_reload(_last_png_mtime, png_mt):
                logger.info("Charsheet changed — hot-reloading")
                charsheet = load_charsheet(toml_path, png_path)
                renderer.reload_charsheet(charsheet)
                window.load_charsheet_image(str(png_path))
                _last_toml_mtime = toml_mt
                _last_png_mtime = png_mt
        except (OSError, ValueError):
            logger.exception("Hot-reload failed")

    pyglet.clock.schedule_interval(check_hot_reload, 2.0)

    # Elements-mode overlay (ADR 0087). ONE persistent ElementsOverlayWindow
    # is created lazily on the first elements.show and then REUSED across all
    # subsequent show/hide cycles via update_and_show() / hide() — it is never
    # destroyed between scans. This mirrors PickerModalWindow's lifecycle and
    # eliminates the DWM async-destruction race (Finding 1 in the adversarial
    # sweep) that caused opaque-black rendering on the 2nd+ invocation when the
    # old code called overlay.close() + ElementsOverlayWindow() inside the same
    # clock callback.
    #
    # pyglet windows must be created and manipulated on the event-loop thread,
    # so SSE events are marshalled via pyglet.clock.schedule_once — the same
    # pattern as the picker modal.
    _elements_overlay: ElementsOverlayWindow | None = None

    def _ensure_elements_overlay() -> ElementsOverlayWindow:
        nonlocal _elements_overlay
        if _elements_overlay is None:
            _elements_overlay = ElementsOverlayWindow()
        return _elements_overlay

    def _show_elements(data: dict[str, Any]) -> None:
        def _do_show(_dt: float) -> None:
            monitor_raw = data.get("monitor", [0, 0, 0, 0])
            elements = data.get("elements", [])
            if len(monitor_raw) != 4 or not elements:
                return
            monitor: tuple[int, int, int, int] = (
                int(monitor_raw[0]),
                int(monitor_raw[1]),
                int(monitor_raw[2]),
                int(monitor_raw[3]),
            )
            overlay = _ensure_elements_overlay()
            overlay.update_and_show(monitor, elements)

        pyglet.clock.schedule_once(_do_show, 0.0)

    def _hide_elements() -> None:
        def _do_hide(_dt: float) -> None:
            if _elements_overlay is not None:
                _elements_overlay.hide()

        pyglet.clock.schedule_once(_do_hide, 0.0)

    # SSE event handler
    def on_event(event_type: str, data: dict[str, Any]) -> None:
        if event_type == "elements.show":
            _show_elements(data)
            return
        if event_type == "elements.hide":
            _hide_elements()
            return
        if event_type == "picker.open":
            try:
                verb = str(data.get("verb", ""))
                items = list(data.get("items", []))
                picker_modal.show(verb, items)
            except Exception:
                logger.exception("picker_modal.show failed")
            return
        if event_type == "picker.close":
            try:
                picker_modal.hide()
            except Exception:
                logger.exception("picker_modal.hide failed")
            return

        result = sm.on_event(event_type, data)
        if result is not None:
            renderer.set_state(result)
            logger.info("State → %s", result.value)
        # Dim when not listening (ADR 0097) — see _apply_dim.
        _apply_dim(sm, window)
        window.set_dictating(sm.dictating)  # amber DICTATING badge during capture
        # "PROCESSING…" badge: shown while awaiting the server Whisper+LLM
        # round-trip after the end sentinel is sent (ADR 0096 D5).
        _apply_processing_state(sm, window)
        # Transient "CANCELLED" badge: shown for _CANCELLED_CUE_DURATION_S after a
        # dictation.end {reason:"cancel"} event.  The badge is the user's ONLY
        # feedback that dictation was discarded (no chime on cancel).
        _apply_cancelled_cue(sm, window, pyglet.clock.schedule_once)
        if event_type == "tool_fired":
            handle_tool_fired(data, chat_log)
            if "name" in data:
                bubble.show(data["name"])
        elif event_type == "plan_outcome":
            handle_plan_outcome(data, chat_log)
        elif event_type == "transcript":
            handle_transcript(data, chat_log)

    def on_disconnect() -> None:
        sm.force_crashed()
        renderer.set_state(SpriteState.CRASHED)
        _apply_dim(sm, window)
        logger.warning("SSE disconnected — sprite entering CRASHED state")

    # Start SSE client
    sse = SSEClient(
        base_url=cfg.daemon_url,
        on_event=on_event,
        on_disconnect=on_disconnect,
    )
    sse.start()

    # Main update loop
    def update(dt: float) -> None:
        changed = sm.tick(dt)
        if changed:
            renderer.set_state(sm.current_state)
            _apply_dim(sm, window)
        renderer.tick(dt)
        bubble.tick(dt)
        chat_log.tick(time.monotonic())

    pyglet.clock.schedule_interval(update, 1 / 60.0)

    logger.info("Sprite window open at (%d, %d), size=%d", x, y, size)

    # Wire SIGINT (Ctrl+C in foreground console) and Windows SIGBREAK
    # (the supervisor's CTRL_BREAK_EVENT) to a clean pyglet exit. Without
    # SIGBREAK the sprite ignores supervisor shutdown and gets force-
    # killed by taskkill /T /F after the grace window.
    def _request_exit(*_: Any) -> None:
        logger.info("voice-sprite shutdown signal received")
        pyglet.app.exit()

    signal.signal(signal.SIGINT, _request_exit)
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, _request_exit)

    try:
        pyglet.app.run()
    except KeyboardInterrupt:
        pass
    finally:
        sse.stop()
        # Close the persistent elements overlay once on real shutdown.
        if _elements_overlay is not None:
            import contextlib

            with contextlib.suppress(Exception):
                _elements_overlay.close()
        logger.info("voice-sprite exiting")


if __name__ == "__main__":
    main()
