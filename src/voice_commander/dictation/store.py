"""WAV encoding + single-slot persistence for the last dictation."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt

_SAMPLE_RATE = 16000


def encode_wav(audio: npt.NDArray[np.float32], sample_rate: int = _SAMPLE_RATE) -> bytes:
    """Encode a float32 [-1, 1] mono array as a 16-bit PCM mono WAV (bytes)."""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class DictationStore:
    """One-slot on-disk store for the most recent dictation transcript.

    Each new dictation overwrites the previous ``last.txt``. The streaming
    pipeline never assembles a single WAV, so no audio slot exists (ADR 0092).
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def text_path(self) -> Path:
        return self._base / "last.txt"

    def save_text(self, text: str) -> None:
        self.text_path.write_text(text, encoding="utf-8")

    def read_text(self) -> str | None:
        if not self.text_path.exists():
            return None
        return self.text_path.read_text(encoding="utf-8")
