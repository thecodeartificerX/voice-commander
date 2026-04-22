from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar, get_type_hints

T = TypeVar("T")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HotkeyConfig:
    key: str = "scroll_lock"
    mute_key: str = ""


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
    ``env var → config.local.toml → config.toml → hardcoded default``, and the
    winning source is logged at INFO so silent fallbacks never happen.
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
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    web: WebConfig = field(default_factory=WebConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    # Per-field source strings for [llm], keyed by field name. Populated by
    # :meth:`load`; empty when the config is constructed directly. Consumed by
    # :func:`log_llm_sources` at daemon startup so every field's origin is
    # visible.
    llm_sources: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path, local_path: Path | None = None) -> Config:
        raw = _read_toml(path)
        if local_path is None:
            local_path = path.with_name(f"{path.stem}.local{path.suffix}")
        local_raw: dict[str, Any] = {}
        if local_path != path and local_path.exists():
            local_raw = _read_toml(local_path)
            raw = _deep_merge(raw, local_raw)
        if "llm_router" in raw:
            raise ValueError(
                "Config section '[llm_router]' was renamed to '[llm]' in ADR 0040. "
                "Rename the section in your config file."
            )
        vad_raw = raw.get("vad", {})
        gates_raw = vad_raw.pop("gates", {})

        # Build LLMConfig via the per-field resolution helper so we can record
        # each field's winning source (env / local / file / default).
        llm_values, llm_sources = _resolve_llm_fields(
            file_llm=raw.get("llm", {}),
            local_llm=local_raw.get("llm", {}),
        )

        return cls(
            hotkey=_section(HotkeyConfig, raw.get("hotkey", {})),
            audio=_section(AudioConfig, raw.get("audio", {})),
            transcription=_section(TranscriptionConfig, raw.get("transcription", {})),
            feedback=_section(FeedbackConfig, raw.get("feedback", {})),
            vad=_section(VadConfig, {**vad_raw, "gates": _section(VadGatesConfig, gates_raw)}),
            logging=_section(LoggingConfig, raw.get("logging", {})),
            web=_section(WebConfig, raw.get("web", {})),
            llm=LLMConfig(**llm_values),
            llm_sources=llm_sources,
        )


# ---------------------------------------------------------------------------
# Per-field resolution + source logging
# ---------------------------------------------------------------------------


_ENV_PREFIX = "VC_LLM_"


def _resolve_llm_fields(
    *, file_llm: dict[str, Any], local_llm: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve every LLMConfig field from env / local.toml / config.toml / default.

    Returns ``(values, sources)`` where ``sources[field]`` is one of:

    * ``"env:VC_LLM_<FIELD>"``
    * ``"config.local.toml"``
    * ``"config.toml"``
    * ``"default"``
    """
    hints = get_type_hints(LLMConfig)
    known_fields = {f.name: f for f in fields(LLMConfig)}

    # Surface unknown keys the same way _section does, so typos get caught.
    for src_name, src in (("config.toml [llm]", file_llm), ("config.local.toml [llm]", local_llm)):
        for key in src:
            if key not in known_fields:
                raise ValueError(f"Unknown config key '{key}' for LLMConfig in {src_name}")

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

        if field_name in local_llm:
            value = local_llm[field_name]
            _check_type(value, expected, f"LLMConfig.{field_name}")
            values[field_name] = value
            sources[field_name] = "config.local.toml"
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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


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
