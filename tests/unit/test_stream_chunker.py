"""Unit tests for the streaming dictation chunker."""

from __future__ import annotations

import numpy as np

from voice_commander.dictation_stream.chunker import Chunker


class _FakeVad:
    """Emits a fixed utterance every ``period`` frames fed to ``process``."""

    def __init__(self, period: int, utterance: np.ndarray) -> None:
        self._period = period
        self._utterance = utterance
        self._count = 0

    def process(self, frame: np.ndarray) -> np.ndarray | None:
        assert frame.shape == (512,)
        self._count += 1
        return self._utterance if self._count % self._period == 0 else None


def test_emits_wav_chunk_when_vad_completes_utterance():
    one_second = np.ones(16_000, dtype=np.float32)
    chunker = Chunker(16_000, min_chunk_seconds=1, vad=_FakeVad(period=2, utterance=one_second))
    raw = np.zeros(512 * 4, dtype=np.float32)  # 4 frames -> 2 utterances at period 2
    chunks = chunker.process_raw(raw)
    assert len(chunks) == 2
    assert all(isinstance(c, bytes) and c[:4] == b"RIFF" for c in chunks)


def test_drops_chunk_shorter_than_min_length():
    half_second = np.ones(8_000, dtype=np.float32)  # 0.5 s < 1 s minimum
    chunker = Chunker(16_000, min_chunk_seconds=1, vad=_FakeVad(period=2, utterance=half_second))
    chunks = chunker.process_raw(np.zeros(512 * 4, dtype=np.float32))
    assert chunks == []


def test_reframes_audio_across_multiple_calls():
    one_second = np.ones(16_000, dtype=np.float32)
    chunker = Chunker(16_000, min_chunk_seconds=1, vad=_FakeVad(period=1, utterance=one_second))
    # 300 + 300 = 600 samples buffered across two calls -> one full 512 frame.
    assert chunker.process_raw(np.zeros(300, dtype=np.float32)) == []
    chunks = chunker.process_raw(np.zeros(300, dtype=np.float32))
    assert len(chunks) == 1


def test_accepts_2d_mono_block():
    one_second = np.ones(16_000, dtype=np.float32)
    chunker = Chunker(16_000, min_chunk_seconds=1, vad=_FakeVad(period=1, utterance=one_second))
    raw = np.zeros((512, 1), dtype=np.float32)  # sounddevice shape (frames, channels)
    chunks = chunker.process_raw(raw)
    assert len(chunks) == 1
