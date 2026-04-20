from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    duration_ms: int
    confidence: float  # [0, 1]


class Transcriber:
    def __init__(
        self,
        model_size: str = "small.en",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: WhisperModel | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        logger.info(
            "Loading faster-whisper model=%s device=%s compute=%s",
            self._model_size, self._device, self._compute_type,
        )
        self._model = WhisperModel(
            self._model_size, device=self._device, compute_type=self._compute_type
        )
        logger.info("Model loaded")

    def transcribe(self, wav: Path) -> TranscriptionResult:
        if self._model is None:
            raise RuntimeError("Transcriber.load() must be called before transcribe()")
        segments_iter, info = self._model.transcribe(
            str(wav),
            language="en",
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        segments = list(segments_iter)
        text = " ".join(s.text for s in segments).strip()
        confidences = [s.avg_logprob for s in segments if s.avg_logprob is not None]
        conf = _normalize_logprob(sum(confidences) / len(confidences)) if confidences else 0.0
        return TranscriptionResult(
            text=text,
            language=info.language,
            duration_ms=int(info.duration * 1000),
            confidence=conf,
        )


def _normalize_logprob(avg_logprob: float) -> float:
    # avg_logprob is typically in [-1.5, 0]. Map to [0, 1] via exp.
    return max(0.0, min(1.0, math.exp(avg_logprob)))
