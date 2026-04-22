from __future__ import annotations

import argparse
import logging
import os
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


def main() -> None:
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
    from .plan_outcome_handler import handle_plan_outcome
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

    # SSE event handler
    def on_event(event_type: str, data: dict[str, Any]) -> None:
        result = sm.on_event(event_type, data)
        if result is not None:
            renderer.set_state(result)
            logger.info("State → %s", result.value)
        window.set_muted(sm.muted)
        renderer.set_muted(sm.muted)
        if event_type == "plan_outcome":
            handle_plan_outcome(data, summarizer, chat_log)
        if event_type == "tool_fired" and "name" in data:
            bubble.show(data["name"])

    def on_disconnect() -> None:
        sm.force_crashed()
        renderer.set_state(SpriteState.CRASHED)
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
        renderer.tick(dt)
        bubble.tick(dt)
        chat_log.tick(time.monotonic())

    pyglet.clock.schedule_interval(update, 1 / 60.0)

    logger.info("Sprite window open at (%d, %d), size=%d", x, y, size)

    try:
        pyglet.app.run()
    except KeyboardInterrupt:
        pass
    finally:
        if llm_client is not None:
            llm_client.close()
        sse.stop()
        logger.info("voice-sprite exiting")


if __name__ == "__main__":
    main()
