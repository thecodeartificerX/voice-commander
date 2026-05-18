"""Hybrid chunker: raw mic audio -> 16 kHz WAV speech chunks.

Resamples device-native audio to 16 kHz, re-frames into the 512-sample blocks
``VADGate`` requires, and drives ``VADGate`` to segment speech. ``VADGate``
emits a completed utterance on a silence gap *or* once the utterance reaches
``max_utterance_ms`` — that dual trigger is exactly the hybrid silence + hard
time-cap chunking the spec calls for. Chunks shorter than ``min_chunk_seconds``
are discarded as too short for whisper.

Reuses `voice_commander.vad_gate.VADGate`, `vad_onnx.load_silero_vad`,
`resampler.Resampler` and `dictation.store.encode_wav` without forking them.
See spec `docs/superpowers/specs/2026-05-18-streaming-dictation-design.md`
section 6.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import numpy.typing as npt

from voice_commander.dictation.store import encode_wav
from voice_commander.resampler import Resampler
from voice_commander.vad_gate import VADGate
from voice_commander.vad_onnx import load_silero_vad

_SAMPLE_RATE = 16_000
_FRAME_SAMPLES = 512


class _Vad(Protocol):
    """The slice of `VADGate` the chunker depends on."""

    def process(self, frame_16k: npt.NDArray[np.float32]) -> npt.NDArray[np.float32] | None: ...


class Chunker:
    """Turn raw native-rate mic audio into 16 kHz WAV speech chunks."""

    def __init__(
        self,
        native_rate: int,
        *,
        vad_threshold: float = 0.5,
        min_silence_ms: int = 1200,
        speech_pad_ms: int = 300,
        max_chunk_seconds: int = 15,
        min_chunk_seconds: int = 1,
        vad: _Vad | None = None,
    ) -> None:
        self._resampler: Resampler | None = (
            None if native_rate == _SAMPLE_RATE
            else Resampler(src_rate=native_rate, dst_rate=_SAMPLE_RATE)
        )
        self._vad: _Vad = vad if vad is not None else VADGate(
            load_silero_vad(),
            threshold=vad_threshold,
            min_silence_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
            max_utterance_ms=max_chunk_seconds * 1000,
        )
        self._min_samples = min_chunk_seconds * _SAMPLE_RATE
        self._frame_buf: npt.NDArray[np.float32] = np.empty(0, dtype=np.float32)

    def process_raw(self, raw: npt.NDArray[np.float32]) -> list[bytes]:
        """Feed one raw mic block; return any completed WAV chunks."""
        mono = raw if raw.ndim == 1 else raw[:, 0]
        mono = np.ascontiguousarray(mono, dtype=np.float32)
        resampled = mono if self._resampler is None else self._resampler.process(mono)
        return self._consume(resampled)

    def flush(self) -> list[bytes]:
        """Drain the resampler tail at session end; return any final chunk."""
        tail = (
            np.empty(0, dtype=np.float32) if self._resampler is None
            else self._resampler.flush()
        )
        chunks = self._consume(tail)
        self._frame_buf = np.empty(0, dtype=np.float32)  # remainder < 32 ms — drop
        return chunks

    def _consume(self, samples: npt.NDArray[np.float32]) -> list[bytes]:
        self._frame_buf = np.concatenate([self._frame_buf, samples])
        chunks: list[bytes] = []
        while len(self._frame_buf) >= _FRAME_SAMPLES:
            frame = self._frame_buf[:_FRAME_SAMPLES]
            self._frame_buf = self._frame_buf[_FRAME_SAMPLES:]
            utterance = self._vad.process(frame)
            if utterance is not None and len(utterance) >= self._min_samples:
                chunks.append(encode_wav(utterance))
        return chunks
