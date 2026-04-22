from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SpriteConfigError(ValueError):
    pass


@dataclass(frozen=True)
class HudConfig:
    """HUD overlay configuration.

    Validation policy (enforced by load_sprite_config):
    - max_lines: int >= 1 (ChatLog rejects <= 0)
    - hold_ms: int >= 0 (0 = instant hide)
    - fade_ms: int >= 0 (0 = no fade animation)
    - font_size: int >= 1
    - width_px: int >= 1
    - llm_summary_timeout_ms: int >= 1
    - enabled, llm_fallback_enabled: strict bool (int/str rejected)
    - llm_endpoint_url, llm_model_id: str (any TOML scalar accepted)
    Type mismatches raise SpriteConfigError.
    """

    enabled: bool = True
    max_lines: int = 5
    hold_ms: int = 4000
    fade_ms: int = 3000
    font_size: int = 13
    width_px: int = 220
    llm_summary_timeout_ms: int = 800
    llm_fallback_enabled: bool = True
    llm_endpoint_url: str = "http://localhost:1234/v1"
    llm_model_id: str = "google/gemma-4-e4b"


@dataclass(frozen=True)
class SpriteAppConfig:
    """Configuration for the voice_sprite process.

    Validation policy (enforced by load_sprite_config):
    - base_size_px: int >= 1
    - offset_x, offset_y, y_nudge_px: int, unbounded (signed)
    - bubble_fade_ms: int >= 0 (0 = no fade)
    - heartbeat_timeout_ms: int >= 1
    - render_scale: float > 0.0 (0 = invisible sprite)
    - follow_poll_hz: int in [1, 60] (> 60 wastes CPU, < 1 breaks polling)
    - margin_x, margin_y: int >= 0
    - follow_cursor: strict bool
    - corner, asset_path, daemon_url: str, not validated
    Type mismatches raise SpriteConfigError.
    """

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


def _require_int(
    table: str,
    key: str,
    raw: Any,
    *,
    min: int | None = None,
    max: int | None = None,
) -> int:
    if isinstance(raw, bool):
        raise SpriteConfigError(f"[{table}] {key}: expected int, got bool {raw!r}")
    try:
        val = int(raw)
    except (TypeError, ValueError) as exc:
        raise SpriteConfigError(
            f"[{table}] {key}: expected int, got {type(raw).__name__} {raw!r}"
        ) from exc
    if min is not None and val < min:
        raise SpriteConfigError(f"[{table}] {key}: must be >= {min}, got {val}")
    if max is not None and val > max:
        raise SpriteConfigError(f"[{table}] {key}: must be <= {max}, got {val}")
    return val


def _require_float(
    table: str,
    key: str,
    raw: Any,
    *,
    min_exclusive: float | None = None,
) -> float:
    if isinstance(raw, bool):
        raise SpriteConfigError(f"[{table}] {key}: expected float, got bool {raw!r}")
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise SpriteConfigError(
            f"[{table}] {key}: expected float, got {type(raw).__name__} {raw!r}"
        ) from exc
    if min_exclusive is not None and val <= min_exclusive:
        raise SpriteConfigError(f"[{table}] {key}: must be > {min_exclusive}, got {val}")
    return val


def _require_bool(table: str, key: str, raw: Any) -> bool:
    if not isinstance(raw, bool):
        raise SpriteConfigError(f"[{table}] {key}: expected bool, got {type(raw).__name__} {raw!r}")
    return raw


def _build_hud(hud_raw: dict[str, Any]) -> HudConfig:
    return HudConfig(
        enabled=(
            _require_bool("hud", "enabled", hud_raw["enabled"]) if "enabled" in hud_raw else True
        ),
        max_lines=(
            _require_int("hud", "max_lines", hud_raw["max_lines"], min=1)
            if "max_lines" in hud_raw
            else 5
        ),
        hold_ms=(
            _require_int("hud", "hold_ms", hud_raw["hold_ms"], min=0)
            if "hold_ms" in hud_raw
            else 4000
        ),
        fade_ms=(
            _require_int("hud", "fade_ms", hud_raw["fade_ms"], min=0)
            if "fade_ms" in hud_raw
            else 3000
        ),
        font_size=(
            _require_int("hud", "font_size", hud_raw["font_size"], min=1)
            if "font_size" in hud_raw
            else 13
        ),
        width_px=(
            _require_int("hud", "width_px", hud_raw["width_px"], min=1)
            if "width_px" in hud_raw
            else 220
        ),
        llm_summary_timeout_ms=(
            _require_int(
                "hud",
                "llm_summary_timeout_ms",
                hud_raw["llm_summary_timeout_ms"],
                min=1,
            )
            if "llm_summary_timeout_ms" in hud_raw
            else 800
        ),
        llm_fallback_enabled=(
            _require_bool("hud", "llm_fallback_enabled", hud_raw["llm_fallback_enabled"])
            if "llm_fallback_enabled" in hud_raw
            else True
        ),
        llm_endpoint_url=str(hud_raw.get("llm_endpoint_url", "http://localhost:1234/v1")),
        llm_model_id=str(hud_raw.get("llm_model_id", "google/gemma-4-e4b")),
    )


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

    return SpriteAppConfig(
        daemon_url=daemon_url,
        corner=sprite_raw.get("corner", "bottom_right"),
        base_size_px=(
            _require_int("sprite", "base_size_px", sprite_raw["base_size_px"], min=1)
            if "base_size_px" in sprite_raw
            else 128
        ),
        offset_x=(
            _require_int("sprite", "offset_x", sprite_raw["offset_x"])
            if "offset_x" in sprite_raw
            else 16
        ),
        offset_y=(
            _require_int("sprite", "offset_y", sprite_raw["offset_y"])
            if "offset_y" in sprite_raw
            else 16
        ),
        asset_path=sprite_raw.get("asset_path", "assets/sprite"),
        bubble_fade_ms=(
            _require_int(
                "sprite",
                "bubble_fade_ms",
                sprite_raw["bubble_fade_ms"],
                min=0,
            )
            if "bubble_fade_ms" in sprite_raw
            else 2000
        ),
        heartbeat_timeout_ms=(
            _require_int(
                "sprite",
                "heartbeat_timeout_ms",
                sprite_raw["heartbeat_timeout_ms"],
                min=1,
            )
            if "heartbeat_timeout_ms" in sprite_raw
            else 3000
        ),
        render_scale=(
            _require_float(
                "sprite",
                "render_scale",
                sprite_raw["render_scale"],
                min_exclusive=0.0,
            )
            if "render_scale" in sprite_raw
            else 0.75
        ),
        y_nudge_px=(
            _require_int("sprite", "y_nudge_px", sprite_raw["y_nudge_px"])
            if "y_nudge_px" in sprite_raw
            else 16
        ),
        follow_cursor=(
            _require_bool("sprite", "follow_cursor", sprite_raw["follow_cursor"])
            if "follow_cursor" in sprite_raw
            else True
        ),
        follow_poll_hz=(
            _require_int(
                "sprite",
                "follow_poll_hz",
                sprite_raw["follow_poll_hz"],
                min=1,
                max=60,
            )
            if "follow_poll_hz" in sprite_raw
            else 30
        ),
        margin_x=(
            _require_int("sprite", "margin_x", sprite_raw["margin_x"], min=0)
            if "margin_x" in sprite_raw
            else 8
        ),
        margin_y=(
            _require_int("sprite", "margin_y", sprite_raw["margin_y"], min=0)
            if "margin_y" in sprite_raw
            else 8
        ),
        hud=_build_hud(hud_raw),
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
