from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HudConfig:
    enabled: bool = True
    max_lines: int = 5
    hold_ms: int = 4000
    fade_ms: int = 3000
    font_size: int = 13
    width_px: int = 220
    offset_x: int = -230
    offset_y: int = 0
    llm_summary_timeout_ms: int = 800
    llm_fallback_enabled: bool = True


@dataclass(frozen=True)
class SpriteAppConfig:
    """Configuration for the voice_sprite process."""

    daemon_url: str = "http://127.0.0.1:8765"
    corner: str = "bottom_right"
    base_size_px: int = 128
    offset_x: int = 16
    offset_y: int = 16
    asset_path: str = "assets/sprite"
    bubble_fade_ms: int = 2000
    heartbeat_timeout_ms: int = 3000
    render_scale: float = 0.75
    y_nudge_px: int = 16
    # Cursor-follow
    follow_cursor: bool = True
    follow_poll_hz: int = 30
    margin_x: int = 8
    margin_y: int = 8
    # HUD
    hud: HudConfig = field(default_factory=HudConfig)


def load_sprite_config(
    config_path: Path,
    local_path: Path | None = None,
) -> SpriteAppConfig:
    """Load sprite config from config.toml (+ optional config.local.toml)."""
    raw = _read_toml(config_path)
    if local_path is None:
        local_path = config_path.with_name(f"{config_path.stem}.local{config_path.suffix}")
    if local_path != config_path and local_path.exists():
        raw = _deep_merge(raw, _read_toml(local_path))

    sprite_raw = raw.get("sprite", {})
    web_raw = raw.get("web", {})
    hud_raw = raw.get("hud", {})

    host = web_raw.get("host", "127.0.0.1")
    port = web_raw.get("port", 8765)
    daemon_url = f"http://{host}:{port}"

    hud = HudConfig(
        enabled=bool(hud_raw.get("enabled", True)),
        max_lines=int(hud_raw.get("max_lines", 5)),
        hold_ms=int(hud_raw.get("hold_ms", 4000)),
        fade_ms=int(hud_raw.get("fade_ms", 3000)),
        font_size=int(hud_raw.get("font_size", 13)),
        width_px=int(hud_raw.get("width_px", 220)),
        offset_x=int(hud_raw.get("offset_x", -230)),
        offset_y=int(hud_raw.get("offset_y", 0)),
        llm_summary_timeout_ms=int(hud_raw.get("llm_summary_timeout_ms", 800)),
        llm_fallback_enabled=bool(hud_raw.get("llm_fallback_enabled", True)),
    )

    return SpriteAppConfig(
        daemon_url=daemon_url,
        corner=sprite_raw.get("corner", "bottom_right"),
        base_size_px=int(sprite_raw.get("base_size_px", 128)),
        offset_x=int(sprite_raw.get("offset_x", 16)),
        offset_y=int(sprite_raw.get("offset_y", 16)),
        asset_path=sprite_raw.get("asset_path", "assets/sprite"),
        bubble_fade_ms=int(sprite_raw.get("bubble_fade_ms", 2000)),
        heartbeat_timeout_ms=int(sprite_raw.get("heartbeat_timeout_ms", 3000)),
        render_scale=float(sprite_raw.get("render_scale", 0.75)),
        y_nudge_px=int(sprite_raw.get("y_nudge_px", 16)),
        follow_cursor=bool(sprite_raw.get("follow_cursor", True)),
        follow_poll_hz=int(sprite_raw.get("follow_poll_hz", 30)),
        margin_x=int(sprite_raw.get("margin_x", 8)),
        margin_y=int(sprite_raw.get("margin_y", 8)),
        hud=hud,
    )


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
