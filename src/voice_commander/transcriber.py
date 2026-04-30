from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np
import numpy.typing as npt
import soundfile

# Register CUDA runtime DLLs from nvidia-* pip packages at module import time.
# `register()` is idempotent and a no-op on non-Windows or when the nvidia
# wheels are absent. Sequencing matters: any later `import torch` (silero-vad
# in the daemon factory) maps its own cuBLAS, and CTranslate2 then resolves
# whichever was registered first. Keeping the preload at module-load preserves
# the historical ordering. The expensive `from faster_whisper import ...` is
# still deferred to `Transcriber.load()` so remote-mode daemons skip it.
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


class TranscriberProtocol(Protocol):
    """Structural type implemented by both local and remote transcribers."""

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(
        self, source: Path | npt.NDArray[np.float32]
    ) -> TranscriptionResult: ...


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
        self._model: Any = None

    def load(self) -> None:
        if self._model is not None:
            return
        # Lazy: pull faster_whisper into the process only when the local
        # backend is actually selected. Remote-mode daemons skip the import
        # entirely (and the model download / VRAM allocation that follows).
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


class RemoteTranscriber:
    """POSTs utterance audio to a remote whisper.cpp ``server`` endpoint.

    Targets the whisper.cpp ``examples/server`` ``POST /inference`` contract:
    multipart/form-data with a ``file`` field carrying a 16 kHz mono WAV and
    ``response_format=verbose_json`` so the response includes ``avg_logprob``
    and ``no_speech_prob`` per segment. Confidence is computed identically to
    the local backend (mean ``avg_logprob`` → :func:`_normalize_logprob`) so
    the daemon's ``min_confidence`` miss-gate behaves the same regardless of
    backend.

    Network failures (timeout, connect refused, HTTP error, malformed JSON)
    are caught and surfaced as an empty ``TranscriptionResult`` with
    ``confidence=0.0`` and ``no_speech_prob=1.0``. The daemon's existing
    confidence gate then fires a miss chime; the daemon stays alive.
    """

    def __init__(self, endpoint_url: str, timeout_ms: int = 5000) -> None:
        if not endpoint_url:
            raise ValueError("RemoteTranscriber requires a non-empty endpoint_url")
        self._endpoint_url = endpoint_url
        # Split timeout: small connect budget, remainder for read.
        read_s = max(0.5, timeout_ms / 1000.0 - 0.5)
        self._timeout = httpx.Timeout(
            connect=0.5,
            read=read_s,
            write=2.0,
            pool=2.0,
        )
        self._client: httpx.Client | None = None

    def load(self) -> None:
        if self._client is not None:
            return
        logger.info("Remote transcription backend → %s", self._endpoint_url)
        self._client = httpx.Client(timeout=self._timeout, http2=False)

    def unload(self) -> None:
        if self._client is None:
            return
        self._client.close()
        self._client = None

    def transcribe(
        self, source: Path | npt.NDArray[np.float32]
    ) -> TranscriptionResult:
        if self._client is None:
            raise RuntimeError("RemoteTranscriber.load() must be called before transcribe()")

        wav_bytes, duration_ms = _to_wav_bytes(source)

        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"response_format": "verbose_json", "language": "en"}

        try:
            resp = self._client.post(self._endpoint_url, files=files, data=data)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning("remote transcribe network error: %s", exc)
            return _empty_result(duration_ms)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "remote transcribe HTTP %s: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            return _empty_result(duration_ms)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("remote transcribe failure: %s", exc)
            return _empty_result(duration_ms)

        return _parse_verbose_json(payload, duration_ms)


def _to_wav_bytes(
    source: Path | npt.NDArray[np.float32],
) -> tuple[bytes, int]:
    """Serialize ``source`` to 16 kHz mono PCM_16 WAV bytes.

    Returns ``(wav_bytes, duration_ms)``. ``duration_ms`` is computed from the
    sample count for ndarray input, or 0 for Path input (whisper.cpp returns
    its own duration in the verbose_json payload).
    """
    if isinstance(source, np.ndarray):
        if source.ndim != 1:
            raise ValueError(f"Expected 1-D audio array, got shape {source.shape}")
        if source.dtype != np.float32:
            raise ValueError(f"Expected float32 audio, got {source.dtype}")
        buf = io.BytesIO()
        soundfile.write(buf, source, 16000, format="WAV", subtype="PCM_16")
        duration_ms = int(len(source) / 16000.0 * 1000.0)
        return buf.getvalue(), duration_ms

    return Path(source).read_bytes(), 0


def _parse_verbose_json(
    payload: dict[str, object], fallback_duration_ms: int
) -> TranscriptionResult:
    """Convert a whisper.cpp ``verbose_json`` response into a TranscriptionResult.

    Confidence: ``exp(mean(avg_logprob))`` clamped to ``[0, 1]`` so the
    metric is comparable to the local backend. Missing/empty segments fall
    back to ``confidence=0.0`` and ``no_speech_prob=1.0``.
    """
    text_raw = payload.get("text", "")
    text = text_raw.strip() if isinstance(text_raw, str) else ""
    segments_raw = payload.get("segments") or []
    segments: list[object] = list(segments_raw) if isinstance(segments_raw, list) else []

    logprobs: list[float] = []
    no_speech: list[float] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        lp = seg.get("avg_logprob")
        if isinstance(lp, (int, float)):
            logprobs.append(float(lp))
        ns = seg.get("no_speech_prob")
        if isinstance(ns, (int, float)):
            no_speech.append(float(ns))

    confidence = _normalize_logprob(sum(logprobs) / len(logprobs)) if logprobs else 0.0
    avg_no_speech = sum(no_speech) / len(no_speech) if no_speech else 0.0

    duration_ms = fallback_duration_ms
    dur_raw = payload.get("duration")
    if isinstance(dur_raw, (int, float)):
        duration_ms = int(float(dur_raw) * 1000.0)

    return TranscriptionResult(
        text=text,
        language=str(payload.get("language", "en")),
        duration_ms=duration_ms,
        confidence=confidence,
        no_speech_prob=avg_no_speech,
    )


def _empty_result(duration_ms: int) -> TranscriptionResult:
    """Sentinel returned on remote-backend failure — confidence gate fires miss."""
    return TranscriptionResult(
        text="",
        language="en",
        duration_ms=duration_ms,
        confidence=0.0,
        no_speech_prob=1.0,
    )
