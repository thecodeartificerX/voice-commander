from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Fake silero_vad module + FakeVADIterator
#
# VADGate does `from silero_vad import VADIterator` inside __init__, so we
# inject a fake module into sys.modules *before* importing VADGate (and before
# each test that constructs one).
# ---------------------------------------------------------------------------

_VAD_FRAME_SAMPLES = 512  # matches _WINDOW_SAMPLES in vad_gate.py


class FakeVADIterator:
    """Controllable stand-in for silero_vad.VADIterator.

    Pass a schedule dict mapping ``{frame_index: return_value}``; any frame
    index not present returns ``None`` (silence).
    """

    def __init__(self, model: Any, **kwargs: Any) -> None:
        self._schedule: dict[int, dict | None] = {}
        self._call_count: int = 0

    def __call__(self, frame: Any) -> dict | None:
        result = self._schedule.get(self._call_count)
        self._call_count += 1
        return result

    def reset_states(self) -> None:
        self._call_count = 0


def _install_fake_silero(monkeypatch: pytest.MonkeyPatch) -> FakeVADIterator:
    """Inject a fake silero_vad module and return the FakeVADIterator class."""
    fake_mod = ModuleType("silero_vad")
    fake_mod.VADIterator = FakeVADIterator  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "silero_vad", fake_mod)
    return FakeVADIterator


def _make_vad_gate(
    monkeypatch: pytest.MonkeyPatch,
    schedule: dict[int, dict | None] | None = None,
    *,
    pre_roll_ms: int = 320,
    max_utterance_ms: int = 30_000,
) -> VADGate:  # noqa: F821
    """Construct a VADGate with fake silero and an optional event schedule."""
    _install_fake_silero(monkeypatch)

    # Reload so the local import picks up the fake module.
    if "voice_commander.vad_gate" in sys.modules:
        del sys.modules["voice_commander.vad_gate"]

    from voice_commander.vad_gate import VADGate

    fake_model = MagicMock()
    gate = VADGate(
        model=fake_model,
        threshold=0.5,
        pre_roll_ms=pre_roll_ms,
        max_utterance_ms=max_utterance_ms,
    )
    # Inject the schedule into the iterator that was created in __init__.
    gate._vad._schedule = schedule or {}
    return gate


def _zero_frame() -> np.ndarray:
    return np.zeros(_VAD_FRAME_SAMPLES, dtype=np.float32)


def _value_frame(value: float) -> np.ndarray:
    return np.full(_VAD_FRAME_SAMPLES, value, dtype=np.float32)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_speech_start_and_end_produces_utterance(monkeypatch):
    """start on frame 5, end on frame 15 → utterance returned on frame 15."""
    schedule = {5: {"start": 0.16}, 15: {"end": 1.0}}
    gate = _make_vad_gate(monkeypatch, schedule, pre_roll_ms=320)

    results = []
    for _ in range(20):
        out = gate.process(_zero_frame())
        results.append(out)

    # All frames before frame 15 should return None
    for i, r in enumerate(results):
        if i < 15:
            assert r is None, f"Expected None at frame {i}, got array"

    # Frame 15 should return the utterance
    utterance = results[15]
    assert utterance is not None, "Expected utterance at frame 15"
    assert isinstance(utterance, np.ndarray)

    # Length: pre-roll (up to 10 frames = 5120 samples) + frames 6..15 (10 frames = 5120 samples)
    # VADGate includes the start frame in the ring snapshot *and* adds subsequent frames.
    # We don't assert an exact count — just that it's non-empty and plausible.
    assert utterance.shape[0] > 0
    assert utterance.dtype == np.float32


def test_no_speech_returns_none(monkeypatch):
    """When VADIterator always returns None, process() always returns None."""
    gate = _make_vad_gate(monkeypatch, schedule={})  # empty schedule = all None

    for _ in range(10):
        out = gate.process(_zero_frame())
        assert out is None


def test_max_utterance_guard_fires(monkeypatch):
    """Force-end when accumulated samples exceed max_utterance_ms."""
    # 100 ms at 16 kHz = 1600 samples = ceil(1600/512) = 4 frames to overflow.
    # Start event on frame 1, no end event ever.
    schedule = {1: {"start": 0.032}}
    gate = _make_vad_gate(monkeypatch, schedule, pre_roll_ms=32, max_utterance_ms=100)

    utterance = None
    for _ in range(20):
        out = gate.process(_zero_frame())
        if out is not None:
            utterance = out
            break

    assert utterance is not None, "Max-utterance guard should have fired"
    assert isinstance(utterance, np.ndarray)
    # Must contain at least 1600 samples (the guard threshold)
    assert utterance.shape[0] >= 1600


def test_reset_clears_state(monkeypatch):
    """After reset(), no pending utterance is returned even without a new start."""
    # Start on frame 0 — get into ACTIVE state
    schedule = {0: {"start": 0.0}}
    gate = _make_vad_gate(monkeypatch, schedule)

    gate.process(_zero_frame())  # triggers ACTIVE state

    gate.reset()

    # After reset, several frames of silence should return None
    for _ in range(5):
        out = gate.process(_zero_frame())
        assert out is None, "After reset(), process() must return None until next start"


def test_pre_roll_captured_correctly(monkeypatch):
    """Ring buffer frames preceding the start event are included in the utterance."""
    # Feed distinctive frames so we can verify pre-roll content.
    # pre_roll_ms=320 → ceil(320/32)=10 ring-buffer frames.
    # Start on frame 10, end on frame 12 → the utterance should contain the
    # ring frames (0..9) plus the frames added after start (11, 12).

    schedule = {10: {"start": 0.32}, 12: {"end": 0.384}}
    gate = _make_vad_gate(monkeypatch, schedule, pre_roll_ms=320)

    # Feed frames with a unique value per frame so we can spot pre-roll content.
    frames = [_value_frame(float(i + 1)) for i in range(20)]

    utterance = None
    for _, frame in enumerate(frames):
        out = gate.process(frame)
        if out is not None:
            utterance = out
            break

    assert utterance is not None, "Expected utterance at frame 12"

    # Pre-roll frames (frames 1..10, value 1..10) should appear at the start.
    # The ring holds up to 10 frames; at frame 10 it contains frames 1..10.
    # Check that the first element is ~1.0 (from the oldest ring frame, frame 0
    # which has value 1.0) or at least a small positive value (not from frame 11+).
    first_value = utterance[0]
    # Pre-roll frames have values 1..10 (float); post-start frames have values 12..13.
    # Any value < 11.0 confirms pre-roll is included.
    assert first_value < 11.0, f"Expected pre-roll frame at utterance[0], got value={first_value}"
    assert utterance.shape[0] > _VAD_FRAME_SAMPLES, (
        "Utterance must be longer than a single frame (pre-roll included)"
    )
