from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

# Register CUDA runtime DLLs from nvidia-* pip packages at module import time.
# `register()` is idempotent and a no-op on non-Windows or when the nvidia
# wheels are absent. Sequencing matters: any later `import torch` (silero-vad
# in the daemon factory) maps its own cuBLAS, and CTranslate2 then resolves
# whichever was registered first. Keeping the preload at module-load preserves
# the historical ordering. The expensive `from faster_whisper import ...` is
# deferred to `Transcriber.load()` to keep import-time cost low.
from . import _cuda_setup

_cuda_setup.register()

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    duration_ms: int
    confidence: float  # [0, 1]
    no_speech_prob: float = 0.0  # average no_speech_prob across segments


class Transcriber:
    """Wraps faster-whisper for speech-to-text.

    Lifecycle: call load() once at startup, transcribe() per utterance,
    unload() at shutdown.  transcribe() is called on the pipeline worker
    thread; load/unload run on the main thread.
    """

    def __init__(
        self,
        model_size: str = "small.en",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        # Lazy import target; only resolved when load() runs.
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        # Lazy import: defer the model download / VRAM allocation that
        # `from faster_whisper import ...` triggers until first transcribe.
        from faster_whisper import WhisperModel

        logger.info(
            "Loading faster-whisper model=%s device=%s compute=%s",
            self._model_size,
            self._device,
            self._compute_type,
        )
        self._model = WhisperModel(
            self._model_size, device=self._device, compute_type=self._compute_type
        )
        logger.info("Model loaded")

    def unload(self) -> None:
        """Release the underlying model. Idempotent. Safe to call if load() was never called."""
        if self._model is None:
            return
        logger.info("Unloading faster-whisper model")
        del self._model
        self._model = None
        import gc

        gc.collect()

    def transcribe(self, source: Path | npt.NDArray[np.float32]) -> TranscriptionResult:
        """Transcribe audio from a WAV path or a 16 kHz float32 mono ndarray."""
        if self._model is None:
            raise RuntimeError("Transcriber.load() must be called before transcribe()")

        if isinstance(source, np.ndarray):
            if source.ndim != 1:
                raise ValueError(f"Expected 1-D audio array, got shape {source.shape}")
            if source.dtype != np.float32:
                raise ValueError(f"Expected float32 audio, got {source.dtype}")
            audio_input: npt.NDArray[np.float32] | str = source
            use_vad = False
        else:
            audio_input = str(source)
            use_vad = True

        segments_iter, info = self._model.transcribe(
            audio_input,
            language="en",
            beam_size=5,
            vad_filter=use_vad,
            vad_parameters={"min_silence_duration_ms": 300} if use_vad else None,
        )
        segments = list(segments_iter)
        text = " ".join(s.text for s in segments).strip()
        confidences = [s.avg_logprob for s in segments if s.avg_logprob is not None]
        conf = _normalize_logprob(sum(confidences) / len(confidences)) if confidences else 0.0
        no_speech_probs = [s.no_speech_prob for s in segments if s.no_speech_prob is not None]
        avg_no_speech = sum(no_speech_probs) / len(no_speech_probs) if no_speech_probs else 0.0
        return TranscriptionResult(
            text=text,
            language=info.language,
            duration_ms=int(info.duration * 1000),
            confidence=conf,
            no_speech_prob=avg_no_speech,
        )


def _normalize_logprob(avg_logprob: float) -> float:
    # avg_logprob is typically in [-1.5, 0]. Map to [0, 1] via exp.
    return max(0.0, min(1.0, math.exp(avg_logprob)))
