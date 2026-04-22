from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def load_sprite_config(
    config_path: Path,
    local_path: Path | None = None,
) -> SpriteAppConfig:
    """Load sprite config from the daemon's config.toml [sprite] + [web] sections."""
    raw = _read_toml(config_path)
    if local_path is None:
        local_path = config_path.with_name(f"{config_path.stem}.local{config_path.suffix}")
    if local_path != config_path and local_path.exists():
        raw = _deep_merge(raw, _read_toml(local_path))

    sprite_raw = raw.get("sprite", {})
    web_raw = raw.get("web", {})

    # Derive daemon URL from web config
    host = web_raw.get("host", "127.0.0.1")
    port = web_raw.get("port", 8765)
    daemon_url = f"http://{host}:{port}"

    return SpriteAppConfig(
        daemon_url=daemon_url,
        corner=sprite_raw.get("corner", "bottom_right"),
        base_size_px=sprite_raw.get("base_size_px", 128),
        offset_x=sprite_raw.get("offset_x", 16),
        offset_y=sprite_raw.get("offset_y", 16),
        asset_path=sprite_raw.get("asset_path", "assets/sprite"),
        bubble_fade_ms=sprite_raw.get("bubble_fade_ms", 2000),
        heartbeat_timeout_ms=sprite_raw.get("heartbeat_timeout_ms", 3000),
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
