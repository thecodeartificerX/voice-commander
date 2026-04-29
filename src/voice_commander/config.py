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
    # Default flips from "" to "ctrl_r" (ADR 0072): Right Ctrl is the Windows
    # dictation hotkey, so one keypress toggles both the dictation app and
    # voice-commander's speak-mode simultaneously. Set to "" to disable.
    mute_key: str = "ctrl_r"


@dataclass(frozen=True)
class AudioConfig:
    channels: int = 1
    device: int = -1
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
class LLMConfig:
    """Runtime config for the LLM-only routing path (ADR 0040 / spec 2026-04-21).

    Every field is resolved at daemon startup via the precedence chain
    ``env var → config.toml → hardcoded default``, and the winning source is
    logged at INFO so silent fallbacks never happen.
    """

    endpoint_url: str = "http://localhost:1234/v1"
    model_id: str = "google/gemma-4-e4b"
    default_browser: str = "chrome"
    timeout_ms: int = 1200
    # warmup_timeout_ms is a one-shot startup cost paid once at daemon init,
    # not a per-call budget. Keep it generous (default 5000ms) so the first
    # real user call always hits a pre-filled prefix KV cache.
    warmup_timeout_ms: int = 5000
    max_plan_steps: int = 12
    warmup_on_startup: bool = True
    focus_fuzzy_threshold: int = 70
    open_fuzzy_threshold: int = 70


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
class SpeakConfig:
    """Configuration for the speak-mode dictation toggle (ADR 0072).

    ``fuzzy_threshold`` controls how closely a transcript must match the word
    "speak" (via ``rapidfuzz.fuzz.ratio``) to trigger a speak-mode toggle.
    Valid range: 0–100. Default 95 requires a near-exact match.
    """

    fuzzy_threshold: int = 95


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
    llm: LLMConfig = field(default_factory=LLMConfig)
    sprite: SpriteConfig = field(default_factory=SpriteConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    speak: SpeakConfig = field(default_factory=SpeakConfig)
    # Per-field source strings for [llm], keyed by field name. Populated by
    # :meth:`load`; empty when the config is constructed directly. Consumed by
    # :func:`log_llm_sources` at daemon startup so every field's origin is
    # visible.
    llm_sources: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = _read_toml(path)
        if "llm_router" in raw:
            raise ValueError(
                "Config section '[llm_router]' was renamed to '[llm]' in ADR 0040. "
                "Rename the section in your config file."
            )
        vad_raw = raw.get("vad", {})
        gates_raw = vad_raw.pop("gates", {})

        # Build LLMConfig via the per-field resolution helper so we can record
        # each field's winning source (env / file / default).
        llm_values, llm_sources = _resolve_llm_fields(file_llm=raw.get("llm", {}))

        return cls(
            hotkey=_section(HotkeyConfig, raw.get("hotkey", {})),
            audio=_section(AudioConfig, raw.get("audio", {})),
            transcription=_section(TranscriptionConfig, raw.get("transcription", {})),
            feedback=_section(FeedbackConfig, raw.get("feedback", {})),
            vad=_section(VadConfig, {**vad_raw, "gates": _section(VadGatesConfig, gates_raw)}),
            logging=_section(LoggingConfig, raw.get("logging", {})),
            web=_section(WebConfig, raw.get("web", {})),
            llm=LLMConfig(**llm_values),
            sprite=_section(SpriteConfig, raw.get("sprite", {})),
            perception=_section(PerceptionConfig, raw.get("perception", {})),
            observability=_section(ObservabilityConfig, raw.get("observability", {})),
            speak=_section(SpeakConfig, raw.get("speak", {})),
            llm_sources=llm_sources,
        )


# ---------------------------------------------------------------------------
# Per-field resolution + source logging
# ---------------------------------------------------------------------------


_ENV_PREFIX = "VC_LLM_"


def _resolve_llm_fields(*, file_llm: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve every LLMConfig field from env / config.toml / default.

    Returns ``(values, sources)`` where ``sources[field]`` is one of:

    * ``"env:VC_LLM_<FIELD>"``
    * ``"config.toml"``
    * ``"default"``
    """
    hints = get_type_hints(LLMConfig)
    known_fields = {f.name: f for f in fields(LLMConfig)}

    # Surface unknown keys the same way _section does, so typos get caught.
    for key in file_llm:
        if key not in known_fields:
            raise ValueError(f"Unknown config key '{key}' for LLMConfig in config.toml [llm]")

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}

    for field_name, f_meta in known_fields.items():
        expected = hints[field_name]
        env_key = f"{_ENV_PREFIX}{field_name.upper()}"

        env_raw = os.environ.get(env_key)
        if env_raw is not None:
            values[field_name] = _coerce_scalar(env_raw, expected, env_key)
            sources[field_name] = f"env:{env_key}"
            continue

        if field_name in file_llm:
            value = file_llm[field_name]
            _check_type(value, expected, f"LLMConfig.{field_name}")
            values[field_name] = value
            sources[field_name] = "config.toml"
            continue

        values[field_name] = f_meta.default
        sources[field_name] = "default"

    return values, sources


def _coerce_scalar(raw: str, expected: Any, env_key: str) -> Any:
    """Convert an env-var string to *expected*. Raise ``ValueError`` on a bad cast."""
    if expected is str:
        return raw
    if expected is bool:
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Env {env_key}={raw!r} not a valid bool")
    if expected is int:
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"Env {env_key}={raw!r} not a valid int") from exc
    if expected is float:
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError(f"Env {env_key}={raw!r} not a valid float") from exc
    raise ValueError(f"Env {env_key}: unsupported target type {expected}")


def _check_type(value: Any, expected: Any, label: str) -> None:
    if not _type_ok(value, expected):
        raise TypeError(f"Config {label} expected {expected}, got {type(value).__name__}")


def log_llm_sources(cfg: Config) -> None:
    """Emit one INFO line per [llm].* field showing value + source.

    Called once at daemon startup. See spec Section 5 for the format.
    """
    if not cfg.llm_sources:
        return  # Config constructed directly (e.g. in a test); nothing to log.

    field_names = [f.name for f in fields(LLMConfig)]
    # Fixed-width key column so log lines align in a tail.
    max_key_len = max(len(name) for name in field_names)
    for name in field_names:
        value = getattr(cfg.llm, name)
        source = cfg.llm_sources.get(name, "default")
        logger.info(
            "config: llm.%-*s = %s (source: %s)",
            max_key_len,
            name,
            value,
            source,
        )


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


_USER_EDITABLE_SECTIONS: dict[str, set[str]] = {
    "llm": {
        "endpoint_url",
        "model_id",
        "default_browser",
        "timeout_ms",
        "warmup_timeout_ms",
        "max_plan_steps",
        "warmup_on_startup",
        "focus_fuzzy_threshold",
        "open_fuzzy_threshold",
    },
    "audio": {"channels", "device", "output_dir"},
    "transcription": {"model_size", "device", "compute_type", "min_confidence"},
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
    """
    for section, payload in updates.items():
        if section not in _USER_EDITABLE_SECTIONS:
            raise ConfigWriteError(f"Section [{section}] is not user-editable")
        allowed = _USER_EDITABLE_SECTIONS[section]
        for key in payload:
            if key not in allowed:
                raise ConfigWriteError(f"Key '[{section}].{key}' is not user-editable")

    existing: dict[str, Any] = _read_toml(path) if path.exists() else {}
    for section, payload in updates.items():
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
