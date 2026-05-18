"""Configuration for the streaming dictation experiment.

Reads the ``[dictation_stream]`` table from ``config.toml`` into a frozen
dataclass. Fully self-contained — the shipped ``voice_commander.config``
module is never touched, and an absent table yields all defaults. An unknown
``[dictation_stream]`` table elsewhere in ``config.toml`` is harmless: the
daemon's loader ignores unknown top-level tables.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class StreamDictationConfig:
    """Streaming dictation tunables (spec 2026-05-18 section 9)."""

    ws_url: str = "ws://192.168.4.200:8765/ws/transcribe"
    language: str = "en"
    vad_threshold: float = 0.5
    min_silence_duration_ms: int = 1200
    speech_pad_ms: int = 300
    max_chunk_seconds: int = 15
    min_chunk_seconds: int = 1
    idle_timeout_seconds: int = 30


def load(path: Path) -> StreamDictationConfig:
    """Load the ``[dictation_stream]`` table from a TOML file.

    Returns all defaults if the file or the table is absent. Raises
    ``ValueError`` if the table carries a key the dataclass does not define.
    """
    if not path.exists():
        return StreamDictationConfig()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    section = raw.get("dictation_stream", {})
    known = {f.name for f in fields(StreamDictationConfig)}
    unknown = set(section) - known
    if unknown:
        raise ValueError(f"Unknown [dictation_stream] config keys: {sorted(unknown)}")
    return StreamDictationConfig(**section)
