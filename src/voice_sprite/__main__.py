from __future__ import annotations

import argparse
import logging
import os
import sys
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

    # Deferred pyglet import — avoids display probe at module-load time.
    # ImportError here means pyglet/GL libs missing; surface as startup
    # failure rather than import failure.
    import pyglet

    # Calculate position
    screen = pyglet.canvas.get_display().get_default_screen()  # type: ignore[attr-defined]
    offset_x = int(cfg.offset_x * scale)
    offset_y = int(cfg.offset_y * scale)

    positions = {
        "bottom_right": (screen.width - size - offset_x, offset_y),
        "bottom_left": (offset_x, offset_y),
        "top_right": (screen.width - size - offset_x, screen.height - size - offset_y),
        "top_left": (offset_x, screen.height - size - offset_y),
    }
    x, y = positions.get(cfg.corner, positions["bottom_right"])

    from .window import SpriteWindow

    window = SpriteWindow(width=size, height=size, x=x, y=y, renderer=renderer, bubble=bubble)
    window.load_charsheet_image(str(png_path))
    window.apply_win32_flags()

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

    pyglet.clock.schedule_interval(update, 1 / 60.0)

    logger.info("Sprite window open at (%d, %d), size=%d", x, y, size)

    try:
        pyglet.app.run()
    except KeyboardInterrupt:
        pass
    finally:
        sse.stop()
        logger.info("voice-sprite exiting")


if __name__ == "__main__":
    main()
