from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar, get_type_hints

T = TypeVar("T")


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
class MatchingConfig:
    threshold: float = 85.0
    scorer: str = "WRatio"


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
class LLMRouterConfig:
    enabled: bool = False
    endpoint_url: str = "http://localhost:1234/v1"
    model_id: str = "google/gemma-4-e4b"
    timeout_ms: int = 600
    # warmup_timeout_ms is a one-shot startup cost paid once at daemon init,
    # not a per-call budget. Keep it generous (default 5000ms) so the first
    # real user call always hits a pre-filled prefix KV cache.
    warmup_timeout_ms: int = 5000
    max_plan_steps: int = 8
    warmup_on_startup: bool = True


@dataclass(frozen=True)
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    web: WebConfig = field(default_factory=WebConfig)
    llm_router: LLMRouterConfig = field(default_factory=LLMRouterConfig)

    @classmethod
    def load(cls, path: Path, local_path: Path | None = None) -> Config:
        raw = _read_toml(path)
        if local_path is None:
            local_path = path.with_name(f"{path.stem}.local{path.suffix}")
        if local_path != path and local_path.exists():
            raw = _deep_merge(raw, _read_toml(local_path))
        vad_raw = raw.get("vad", {})
        gates_raw = vad_raw.pop("gates", {})
        return cls(
            hotkey=_section(HotkeyConfig, raw.get("hotkey", {})),
            audio=_section(AudioConfig, raw.get("audio", {})),
            transcription=_section(TranscriptionConfig, raw.get("transcription", {})),
            matching=_section(MatchingConfig, raw.get("matching", {})),
            feedback=_section(FeedbackConfig, raw.get("feedback", {})),
            vad=_section(VadConfig, {**vad_raw, "gates": _section(VadGatesConfig, gates_raw)}),
            logging=_section(LoggingConfig, raw.get("logging", {})),
            web=_section(WebConfig, raw.get("web", {})),
            llm_router=_section(LLMRouterConfig, raw.get("llm_router", {})),
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
