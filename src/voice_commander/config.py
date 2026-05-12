from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar, get_type_hints

T = TypeVar("T")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HotkeyConfig:
    key: str = "scroll_lock"
    # Secondary hotkey that toggles mute *within* an active session
    # (ADR 0025). Default ``ctrl_r`` so the right Ctrl key — already a
    # common dictation push-to-talk — flips voice-commander into a
    # muted state with the audio stream torn down. Empty string disables
    # the secondary binding entirely.
    mute_key: str = "ctrl_r"


@dataclass(frozen=True)
class AudioConfig:
    channels: int = 1
    device: int = -1
    device_name: str = ""  # stable identity for name-based re-resolution on index drift
    output_dir: str = "outputs"


@dataclass(frozen=True)
class TranscriptionConfig:
    model_size: str = "small.en"
    device: str = "cuda"
    compute_type: str = "float16"
    min_confidence: float = 0.30


@dataclass(frozen=True)
class FeedbackConfig:
    sounds_dir: str = "assets/sounds"
    start_sound: str = "start.wav"
    stop_sound: str = "stop.wav"
    miss_sound: str = "miss.wav"


@dataclass(frozen=True)
class VadGatesConfig:
    min_word_count: int = 1
    max_no_speech_prob: float = 0.6


@dataclass(frozen=True)
class VadConfig:
    threshold: float = 0.4
    min_speech_duration_ms: int = 100
    min_silence_duration_ms: int = 250
    speech_pad_ms: int = 30
    pre_roll_ms: int = 300
    max_utterance_ms: int = 8000
    # sample_rate and window_samples are fixed at 16000/512 (Silero VAD model requirement)
    gates: VadGatesConfig = field(default_factory=VadGatesConfig)


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    file: str = "voice-commander.log"
    max_bytes: int = 5_000_000
    backup_count: int = 5


@dataclass(frozen=True)
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    auto_open_browser: bool = True



@dataclass(frozen=True)
class SpriteConfig:
    # Fields are consumed by the voice_sprite process; daemon carries them
    # only so Config.load() doesn't reject the shared config.toml.
    enabled: bool = True
    corner: str = "bottom_right"
    base_size_px: int = 128
    offset_x: int = 16
    offset_y: int = 16
    asset_path: str = "assets/sprite"
    bubble_fade_ms: int = 2000
    heartbeat_timeout_ms: int = 3000
    render_scale: float = 0.75
    y_nudge_px: int = 16
    follow_cursor: bool = True
    follow_poll_hz: int = 30
    margin_x: int = 8
    margin_y: int = 8


@dataclass(frozen=True)
class PerceptionConfig:
    ocr_engine: str = "auto"  # "auto" | "winrt" | "tesseract"


@dataclass(frozen=True)
class ObservabilityConfig:
    enabled: bool = True
    keep_runs: int = 1000
    db_path: str = "outputs/runs.db"
    queue_max: int = 4096
    slow_run_ms: int = 2000


@dataclass(frozen=True)
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    web: WebConfig = field(default_factory=WebConfig)
    sprite: SpriteConfig = field(default_factory=SpriteConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = _read_toml(path)
        if "llm_router" in raw:
            raise ValueError(
                "Config section '[llm_router]' was renamed to '[llm]' in ADR 0040; "
                "[llm] has since been removed entirely (ADR 0082). "
                "Remove the section from your config file."
            )
        if "llm" in raw:
            logger.warning(
                "[llm] section is deprecated and ignored — LLM routing was removed (ADR 0082)"
            )
            raw.pop("llm")
        vad_raw = raw.get("vad", {})
        gates_raw = vad_raw.pop("gates", {})

        return cls(
            hotkey=_section(HotkeyConfig, raw.get("hotkey", {})),
            audio=_section(AudioConfig, raw.get("audio", {})),
            transcription=_section(TranscriptionConfig, raw.get("transcription", {})),
            feedback=_section(FeedbackConfig, raw.get("feedback", {})),
            vad=_section(VadConfig, {**vad_raw, "gates": _section(VadGatesConfig, gates_raw)}),
            logging=_section(LoggingConfig, raw.get("logging", {})),
            web=_section(WebConfig, raw.get("web", {})),
            sprite=_section(SpriteConfig, raw.get("sprite", {})),
            perception=_section(PerceptionConfig, raw.get("perception", {})),
            observability=_section(ObservabilityConfig, raw.get("observability", {})),
        )


# ---------------------------------------------------------------------------
# Per-field resolution + source logging
# ---------------------------------------------------------------------------






def _check_type(value: Any, expected: Any, label: str) -> None:
    if not _type_ok(value, expected):
        raise TypeError(f"Config {label} expected {expected}, got {type(value).__name__}")



# ---------------------------------------------------------------------------
# TOML loading helpers
# ---------------------------------------------------------------------------


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _section(cls: type[T], data: dict[str, Any]) -> T:
    assert is_dataclass(cls)
    known = {f.name: f for f in fields(cls)}
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"Unknown config key '{key}' for {cls.__name__}")
        expected = hints[key]
        if not _type_ok(value, expected):
            raise TypeError(
                f"Config {cls.__name__}.{key} expected {expected}, got {type(value).__name__}"
            )
        kwargs[key] = value
    return cls(**kwargs)


def _type_ok(value: Any, expected: Any) -> bool:
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is bool:
        return isinstance(value, bool)
    if expected is str:
        return isinstance(value, str)
    return True


# ---------------------------------------------------------------------------
# Config writer — used by the web UI to persist edits made via the dashboard.
# ---------------------------------------------------------------------------


# Keys in [audio] that must never be written going forward (ADR 0081).
# They are still *read* (AudioConfig.device survives for back-compat) but any
# attempt to write them via update_user_config is silently dropped with a warning.
_AUDIO_LEGACY_WRITE_KEYS: frozenset[str] = frozenset({"device"})

_USER_EDITABLE_SECTIONS: dict[str, set[str]] = {
    "audio": {"channels", "device_name", "output_dir"},
    "transcription": {
        "model_size",
        "device",
        "compute_type",
        "min_confidence",
    },
}


class ConfigWriteError(ValueError):
    """Raised when an update payload contains unknown keys or bad types."""


def update_user_config(path: Path, updates: Mapping[str, Mapping[str, Any]]) -> None:
    """Merge *updates* into *path*'s TOML and rewrite it atomically.

    Only the subset of sections/keys in ``_USER_EDITABLE_SECTIONS`` may be
    edited via this function. Anything else raises :class:`ConfigWriteError`
    so the web UI cannot accidentally clobber ``[hotkey]`` or ``[vad]``.
    Comments in the original TOML are NOT preserved (stdlib TOML writer
    limitation); keep opinionated comments out of config.toml or migrate to
    tomlkit later if comment round-tripping becomes a requirement.

    Legacy audio keys (e.g. ``audio.device``) are silently dropped from the
    write payload with a warning; use ``device_name`` instead (ADR 0081).
    When rewriting the config the legacy key is also removed from the output,
    migrating old configs forward on the next save.
    """
    # Strip legacy write keys from audio payloads before validation.
    sanitized: dict[str, Mapping[str, Any]] = {}
    for section, payload in updates.items():
        if section == "audio":
            clean: dict[str, Any] = {}
            for key, value in payload.items():
                if key in _AUDIO_LEGACY_WRITE_KEYS:
                    logger.warning(
                        "update_user_config: dropping legacy key %r from audio section "
                        "(use device_name; ADR 0081)",
                        key,
                    )
                else:
                    clean[key] = value
            sanitized[section] = clean
        else:
            sanitized[section] = payload

    for section, payload in sanitized.items():
        if section not in _USER_EDITABLE_SECTIONS:
            raise ConfigWriteError(f"Section [{section}] is not user-editable")
        allowed = _USER_EDITABLE_SECTIONS[section]
        for key in payload:
            if key not in allowed:
                raise ConfigWriteError(f"Key '[{section}].{key}' is not user-editable")

    existing: dict[str, Any] = _read_toml(path) if path.exists() else {}

    # Migrate forward: remove legacy audio keys from any existing config.
    audio_existing = existing.get("audio")
    if isinstance(audio_existing, dict):
        for legacy_key in _AUDIO_LEGACY_WRITE_KEYS:
            audio_existing.pop(legacy_key, None)

    for section, payload in sanitized.items():
        sec = existing.setdefault(section, {})
        if not isinstance(sec, dict):
            raise ConfigWriteError(f"Existing [{section}] is not a table")
        for key, value in payload.items():
            sec[key] = value

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_render_toml(existing), encoding="utf-8")
    os.replace(tmp, path)


def _render_toml(data: Mapping[str, Any]) -> str:
    """Serialise *data* — a nested mapping — as a TOML document.

    Supports two levels:
    * top-level scalar keys (rare for our config) → written before any section.
    * ``[section.subsection]`` tables, each a mapping of scalar/list values.

    No arrays of tables, no inline tables, no comments — sufficient for the
    voice-commander config schema. Unsupported values raise TypeError.
    """
    lines: list[str] = []

    top_scalars = {k: v for k, v in data.items() if not isinstance(v, Mapping)}
    for key, value in top_scalars.items():
        lines.append(f"{key} = {_toml_value(value)}")
    if top_scalars:
        lines.append("")

    for section, body in data.items():
        if not isinstance(body, Mapping):
            continue
        lines.append(f"[{section}]")
        nested: dict[str, Mapping[str, Any]] = {}
        for key, value in body.items():
            if isinstance(value, Mapping):
                nested[key] = value
                continue
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
        for sub_name, sub_body in nested.items():
            lines.append(f"[{section}.{sub_name}]")
            for key, value in sub_body.items():
                lines.append(f"{key} = {_toml_value(value)}")
            lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"Cannot serialise {type(value).__name__} to TOML")
