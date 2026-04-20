from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar, get_type_hints

T = TypeVar("T")


@dataclass(frozen=True)
class HotkeyConfig:
    key: str = "scroll_lock"


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
class LoggingConfig:
    level: str = "INFO"
    file: str = "voice-commander.log"
    max_bytes: int = 5_000_000
    backup_count: int = 5


@dataclass(frozen=True)
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def load(cls, path: Path) -> "Config":
        raw: dict[str, Any] = {}
        if path.exists():
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        return cls(
            hotkey=_section(HotkeyConfig, raw.get("hotkey", {})),
            audio=_section(AudioConfig, raw.get("audio", {})),
            transcription=_section(TranscriptionConfig, raw.get("transcription", {})),
            matching=_section(MatchingConfig, raw.get("matching", {})),
            feedback=_section(FeedbackConfig, raw.get("feedback", {})),
            logging=_section(LoggingConfig, raw.get("logging", {})),
        )


def _section(cls: type[T], data: dict[str, Any]) -> T:
    assert is_dataclass(cls)
    known = {f.name: f for f in fields(cls)}  # type: ignore[arg-type]
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
    return cls(**kwargs)  # type: ignore[return-value]


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
