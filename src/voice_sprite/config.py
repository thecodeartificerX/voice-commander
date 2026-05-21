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

    Summaries are rule-based only (no LLM call) — the router already
    proved comprehension by selecting a tool, so the HUD just describes
    which tool ran.

    Validation policy (enforced by load_sprite_config):
    - max_lines: int >= 1 (ChatLog rejects <= 0)
    - hold_ms: int >= 0 (0 = instant hide)
    - fade_ms: int >= 0 (0 = no fade animation)
    - font_size: int >= 1
    - width_px: int >= 1
    - enabled: strict bool (int/str rejected)
    Type mismatches raise SpriteConfigError.
    """

    enabled: bool = True
    max_lines: int = 5
    hold_ms: int = 4000
    fade_ms: int = 3000
    font_size: int = 13
    width_px: int = 220


@dataclass(frozen=True)
class SpriteAppConfig:
    """Configuration for the voice_sprite process.

    Validation policy (enforced by load_sprite_config):
    - base_size_px: int >= 1
    - offset_x, offset_y, y_nudge_px: int, unbounded (signed)
    - bubble_fade_ms: int >= 0 (0 = no fade)
    - heartbeat_timeout_ms: int >= 1
    - render_scale: float > 0.0 (0 = invisible sprite)
    - dim_brightness: float in [0.0, 1.0] inclusive (brightness multiplier for not-listening states)
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
    dim_brightness: float = 0.4  # brightness multiplier (0.0–1.0) for not-listening states (ADR 0097)
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
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    # bool subclasses int in Python; reject it so TOML `true` doesn't coerce to 1.
    if isinstance(raw, bool):
        raise SpriteConfigError(f"[{table}] {key}: expected int, got bool {raw!r}")
    if isinstance(raw, float):
        raise SpriteConfigError(f"[{table}] {key}: expected int, got float {raw!r}")
    if not isinstance(raw, int):
        raise SpriteConfigError(f"[{table}] {key}: expected int, got {type(raw).__name__} {raw!r}")
    if min_val is not None and raw < min_val:
        raise SpriteConfigError(f"[{table}] {key}: must be >= {min_val}, got {raw}")
    if max_val is not None and raw > max_val:
        raise SpriteConfigError(f"[{table}] {key}: must be <= {max_val}, got {raw}")
    return raw


def _require_float(
    table: str,
    key: str,
    raw: Any,
    *,
    min_exclusive: float | None = None,
    min_inclusive: float | None = None,
    max_inclusive: float | None = None,
) -> float:
    # bool subclasses int (and int → float); reject it so TOML `true` doesn't coerce to 1.0.
    if isinstance(raw, bool):
        raise SpriteConfigError(f"[{table}] {key}: expected float, got bool {raw!r}")
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise SpriteConfigError(
            f"[{table}] {key}: expected float, got {type(raw).__name__} {raw!r}"
        ) from exc
    # min_exclusive and min_inclusive are mutually exclusive — callers pass at most one.
    if min_exclusive is not None and val <= min_exclusive:
        raise SpriteConfigError(f"[{table}] {key}: must be > {min_exclusive}, got {val}")
    if min_inclusive is not None and val < min_inclusive:
        raise SpriteConfigError(f"[{table}] {key}: must be >= {min_inclusive}, got {val}")
    if max_inclusive is not None and val > max_inclusive:
        raise SpriteConfigError(f"[{table}] {key}: must be <= {max_inclusive}, got {val}")
    return val


def _require_bool(table: str, key: str, raw: Any) -> bool:
    if not isinstance(raw, bool):
        raise SpriteConfigError(f"[{table}] {key}: expected bool, got {type(raw).__name__} {raw!r}")
    return raw


def _opt_int(
    table: str,
    d: dict[str, Any],
    key: str,
    default: int,
    *,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    if key not in d:
        return default
    return _require_int(table, key, d[key], min_val=min_val, max_val=max_val)


def _opt_float(
    table: str,
    d: dict[str, Any],
    key: str,
    default: float,
    *,
    min_exclusive: float | None = None,
    min_inclusive: float | None = None,
    max_inclusive: float | None = None,
) -> float:
    if key not in d:
        return default
    return _require_float(
        table,
        key,
        d[key],
        min_exclusive=min_exclusive,
        min_inclusive=min_inclusive,
        max_inclusive=max_inclusive,
    )


def _opt_bool(table: str, d: dict[str, Any], key: str, default: bool) -> bool:
    return _require_bool(table, key, d[key]) if key in d else default


def _build_hud(hud_raw: dict[str, Any]) -> HudConfig:
    return HudConfig(
        enabled=_opt_bool("hud", hud_raw, "enabled", True),
        max_lines=_opt_int("hud", hud_raw, "max_lines", 5, min_val=1),
        hold_ms=_opt_int("hud", hud_raw, "hold_ms", 4000, min_val=0),
        fade_ms=_opt_int("hud", hud_raw, "fade_ms", 3000, min_val=0),
        font_size=_opt_int("hud", hud_raw, "font_size", 13, min_val=1),
        width_px=_opt_int("hud", hud_raw, "width_px", 220, min_val=1),
    )


def load_sprite_config(config_path: Path) -> SpriteAppConfig:
    """Load sprite config from config.toml."""
    raw = _read_toml(config_path)

    sprite_raw = raw.get("sprite", {})
    web_raw = raw.get("web", {})
    hud_raw = raw.get("hud", {})

    host = web_raw.get("host", "127.0.0.1")
    port = web_raw.get("port", 8765)
    daemon_url = f"http://{host}:{port}"

    # Detect and warn on legacy [hud].llm_* keys (silently ignored now —
    # the HUD summarizer no longer calls an LLM at all).
    legacy_keys = [
        k
        for k in (
            "llm_endpoint_url",
            "llm_model_id",
            "llm_summary_timeout_ms",
            "llm_fallback_enabled",
        )
        if k in hud_raw
    ]
    if legacy_keys:
        import warnings

        warnings.warn(
            f"[hud].{legacy_keys} are deprecated and ignored; the HUD summarizer "
            "is rule-table only as of the LLM-summary removal. Remove these keys "
            "from config.toml.",
            DeprecationWarning,
            stacklevel=2,
        )

    return SpriteAppConfig(
        daemon_url=daemon_url,
        corner=sprite_raw.get("corner", "bottom_right"),
        base_size_px=_opt_int("sprite", sprite_raw, "base_size_px", 128, min_val=1),
        offset_x=_opt_int("sprite", sprite_raw, "offset_x", 16),
        offset_y=_opt_int("sprite", sprite_raw, "offset_y", 16),
        asset_path=sprite_raw.get("asset_path", "assets/sprite"),
        bubble_fade_ms=_opt_int("sprite", sprite_raw, "bubble_fade_ms", 2000, min_val=0),
        heartbeat_timeout_ms=_opt_int(
            "sprite", sprite_raw, "heartbeat_timeout_ms", 3000, min_val=1
        ),
        render_scale=_opt_float("sprite", sprite_raw, "render_scale", 0.75, min_exclusive=0.0),
        dim_brightness=_opt_float(
            "sprite", sprite_raw, "dim_brightness", 0.4, min_inclusive=0.0, max_inclusive=1.0
        ),
        y_nudge_px=_opt_int("sprite", sprite_raw, "y_nudge_px", 16),
        follow_cursor=_opt_bool("sprite", sprite_raw, "follow_cursor", True),
        follow_poll_hz=_opt_int("sprite", sprite_raw, "follow_poll_hz", 30, min_val=1, max_val=60),
        margin_x=_opt_int("sprite", sprite_raw, "margin_x", 8, min_val=0),
        margin_y=_opt_int("sprite", sprite_raw, "margin_y", 8, min_val=0),
        hud=_build_hud(hud_raw),
    )


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)
