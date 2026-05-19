"""Unit tests for DictationWindow — the growing-window dictation buffer."""

from __future__ import annotations

import io
import wave

import numpy as np

from voice_commander.dictation.window import DictationWindow

_SR = 16000


def _frame(n: int = 512, value: float = 0.1) -> np.ndarray:
    return np.full(n, value, dtype=np.float32)


def _wav_sample_count(wav_bytes: bytes) -> int:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnframes()


def test_no_emission_before_step():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    # 1000 ms at 16 kHz = 16000 samples. Feed less than one step.
    for _ in range(20):  # 20 * 512 = 10240 samples < 16000
        assert win.append(_frame()) is None


def test_emits_whole_window_at_step_cadence():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    emitted: list[bytes] = []
    # Feed exactly one step worth: ceil(16000 / 512) = 32 frames.
    for _ in range(32):
        wav = win.append(_frame())
        if wav is not None:
            emitted.append(wav)
    assert len(emitted) == 1
    # The emitted WAV is the WHOLE window so far (>= 16000 samples).
    assert _wav_sample_count(emitted[0]) >= _SR


def test_emission_window_grows():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    emitted: list[bytes] = []
    for _ in range(64):  # two steps' worth
        wav = win.append(_frame())
        if wav is not None:
            emitted.append(wav)
    assert len(emitted) == 2
    # Second emission contains MORE audio than the first (growing window).
    assert _wav_sample_count(emitted[1]) > _wav_sample_count(emitted[0])


def test_committed_offset_starts_at_zero():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    assert win.committed_offset_s == 0.0


def test_commit_trims_buffer_and_advances_offset():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    for _ in range(64):  # ~2.0 s of audio
        win.append(_frame())
    # Commit the first 1.0 s (absolute timestamp).
    win.commit(1.0)
    assert win.committed_offset_s == 1.0
    # The next emitted window must be shorter than the full 2 s buffer.
    for _ in range(32):  # add another step so an emission fires
        wav = win.append(_frame())
        if wav is not None:
            # buffer now ~ (2.0 - 1.0 trimmed) + 1.0 new = ~2.0 s, not 3.0 s
            assert _wav_sample_count(wav) < int(_SR * 3.0)
            return
    raise AssertionError("expected an emission after the post-commit step")


def test_commit_is_idempotent_for_stale_timestamp():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    for _ in range(64):
        win.append(_frame())
    win.commit(1.0)
    # A timestamp at or before the current offset trims nothing.
    win.commit(1.0)
    win.commit(0.5)
    assert win.committed_offset_s == 1.0


def test_flush_returns_remaining_window():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    for _ in range(10):  # below the step threshold — no auto emission
        win.append(_frame())
    wav = win.flush()
    assert wav is not None
    assert _wav_sample_count(wav) == 10 * 512


def test_flush_returns_none_when_buffer_empty():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=25000)
    assert win.flush() is None


def test_cap_force_commit_signalled():
    win = DictationWindow(window_step_ms=1000, window_cap_ms=1000)
    for _ in range(96):
        win.append(_frame())
    # cap_exceeded() reports the over-cap condition; the session responds by
    # committing the oldest audio down to the cap.
    assert win.cap_exceeded()
    win.commit(win.committed_offset_s + win.uncommitted_seconds() - 1.0)
    assert not win.cap_exceeded()
    assert win.uncommitted_seconds() <= 1.0 + 1e-6
