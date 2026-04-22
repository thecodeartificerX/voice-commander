from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .chat_log import ChatLog
    from .speech_bubble import SpeechBubble
    from .sprite_renderer import SpriteRenderer
    from .state_machine import StateMachine
    from .summarizer import Summarizer
    from .window import SpriteWindow

logger = logging.getLogger("voice_sprite")


def _configure_logging() -> None:  # pragma: no cover
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


class HUDPipeline:
    """Wires SSE events → Summarizer → ChatLog.

    Extracted from main() so the pipeline is independently testable
    without a live pyglet GL context or SSE connection.
    """

    def __init__(
        self,
        sm: StateMachine,
        renderer: SpriteRenderer,
        window: SpriteWindow,
        bubble: SpeechBubble,
        chat_log: ChatLog,
        summarizer: Summarizer,
    ) -> None:
        self._sm = sm
        self._renderer = renderer
        self._window = window
        self._bubble = bubble
        self._chat_log = chat_log
        self._summarizer = summarizer
        self._queue: queue.Queue[Any] = queue.Queue()

    def on_event(self, event_type: str, data: dict[str, Any]) -> None:
        """SSE event handler — call from SSEClient.on_event."""
        result = self._sm.on_event(event_type, data)
        if result is not None:
            self._renderer.set_state(result)
            logger.info("State → %s", result.value)
        self._window.set_muted(self._sm.muted)
        self._renderer.set_muted(self._sm.muted)
        if event_type == "plan_outcome":
            from voice_commander.plan import PlanOutcome

            raw_text: str = data.get("transcript", "")
            try:
                outcome = PlanOutcome.from_event_dict(data)
            except (KeyError, ValueError, TypeError):
                logger.exception("Failed to parse PlanOutcome — falling back to raw transcript")
                if raw_text:
                    from .chat_log import ChatLogEntry

                    self._chat_log.append(
                        ChatLogEntry(
                            text=raw_text,
                            status="error",
                            born_at_s=time.monotonic(),
                        )
                    )
            else:
                self._queue.put((outcome, raw_text))
        if event_type == "tool_fired" and "name" in data:
            self._bubble.show(data["name"])

    def worker(self) -> None:
        """Background thread target — summarizes PlanOutcome objects off the SSE thread."""
        import pyglet.clock as _pclock

        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                break
            outcome, raw_text = item
            try:
                summary = self._summarizer.summarize(outcome)
            except Exception:
                logger.exception("Summarizer failed — using last-resort text")
                summary = raw_text
            if summary:

                def _append(dt: float, _s: str = summary, _st: Any = outcome.status) -> None:
                    from .chat_log import ChatLogEntry

                    self._chat_log.append(
                        ChatLogEntry(
                            text=_s,
                            status=_st,
                            born_at_s=time.monotonic(),
                        )
                    )

                _pclock.schedule_once(_append, 0)
            self._queue.task_done()

    def stop(self) -> None:
        """Signal worker thread to exit and drain the queue."""
        self._queue.put(None)


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(
        prog="voice-sprite",
        description="On-screen sprite companion for Voice Commander.",
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to Voice Commander config.toml (default: config.toml)",
    )
    args = parser.parse_args()

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
    from .llm_summary_client import LLMSummaryClient
    from .summarizer import Summarizer
    from .summary_rules import CHAIN_DETECTORS, RULES

    chat_log = ChatLog(
        max_lines=cfg.hud.max_lines,
        hold_ms=cfg.hud.hold_ms,
        fade_ms=cfg.hud.fade_ms,
    )
    llm_client: LLMSummaryClient | None = None
    if cfg.hud.enabled and cfg.hud.llm_fallback_enabled:
        llm_client = LLMSummaryClient(
            endpoint_url=cfg.hud.llm_endpoint_url,
            model_id=cfg.hud.llm_model_id,
            timeout_ms=cfg.hud.llm_summary_timeout_ms,
        )
    summarizer = Summarizer(
        rules=RULES,
        chain_detectors=CHAIN_DETECTORS,
        llm_client=llm_client,
        llm_fallback_enabled=cfg.hud.llm_fallback_enabled,
    )

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
            if toml_mt != _last_toml_mtime or png_mt != _last_png_mtime:
                logger.info("Charsheet changed — hot-reloading")
                charsheet = load_charsheet(toml_path, png_path)
                renderer.reload_charsheet(charsheet)
                window.load_charsheet_image(str(png_path))
                _last_toml_mtime = toml_mt
                _last_png_mtime = png_mt
        except (OSError, FileNotFoundError, ValueError):
            logger.exception("Hot-reload failed")

    pyglet.clock.schedule_interval(check_hot_reload, 2.0)

    # HUD pipeline: wires SSE events → Summarizer → ChatLog.
    pipeline = HUDPipeline(
        sm=sm,
        renderer=renderer,
        window=window,
        bubble=bubble,
        chat_log=chat_log,
        summarizer=summarizer,
    )

    _summarizer_thread = threading.Thread(
        target=pipeline.worker,
        name="summarizer-worker",
        daemon=True,
    )

    def on_disconnect() -> None:
        sm.force_crashed()
        renderer.set_state(SpriteState.CRASHED)
        logger.warning("SSE disconnected — sprite entering CRASHED state")

    # Start SSE client
    sse = SSEClient(
        base_url=cfg.daemon_url,
        on_event=pipeline.on_event,
        on_disconnect=on_disconnect,
    )
    sse.start()

    # Main update loop
    def update(dt: float) -> None:
        changed = sm.tick(dt)
        if changed:
            renderer.set_state(sm.current_state)
        renderer.tick(dt)
        bubble.tick(dt)
        chat_log.tick(time.monotonic())

    pyglet.clock.schedule_interval(update, 1 / 60.0)

    logger.info("Sprite window open at (%d, %d), size=%d", x, y, size)

    _summarizer_thread.start()
    try:
        pyglet.app.run()
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()  # signal worker to exit
        _summarizer_thread.join(timeout=5.0)
        if llm_client is not None:
            llm_client.close()
        sse.stop()
        logger.info("voice-sprite exiting")


if __name__ == "__main__":
    main()
