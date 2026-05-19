"""Unit tests for the StreamingRecorder frame tap (streaming-window dictation)."""

from __future__ import annotations

import numpy as np

from voice_commander.streaming_recorder import StreamingRecorder
from voice_commander.vad_gate import VADGate


class _FakeVad:
    """Minimal VAD model stub — never reports speech start/end."""

    def __call__(self, *_args, **_kwargs):
        return 0.0  # below any threshold — no speech detected

    def reset_states(self):
        pass


def _make_recorder() -> StreamingRecorder:
    gate = VADGate(model=_FakeVad())
    return StreamingRecorder(
        device=None,
        channels=1,
        vad_gate=gate,
        utterance_sink=lambda _a: None,
    )


def test_frame_tap_unset_by_default():
    rec = _make_recorder()
    assert rec._frame_tap is None


def test_set_frame_tap_stores_callback():
    rec = _make_recorder()
    captured: list[np.ndarray] = []
    cb = captured.append  # bind once — .append creates a new bound-method each access
    rec.set_frame_tap(cb)
    assert rec._frame_tap is cb


def test_set_frame_tap_none_clears_callback():
    rec = _make_recorder()
    rec.set_frame_tap(lambda _f: None)
    rec.set_frame_tap(None)
    assert rec._frame_tap is None


def test_invoke_frame_tap_forwards_frame():
    rec = _make_recorder()
    captured: list[np.ndarray] = []
    rec.set_frame_tap(captured.append)
    frame = np.zeros(512, dtype=np.float32)
    rec._invoke_frame_tap(frame)
    assert len(captured) == 1
    assert captured[0] is frame


def test_invoke_frame_tap_swallows_callback_exception():
    rec = _make_recorder()

    def _boom(_frame):
        raise RuntimeError("tap exploded")

    rec.set_frame_tap(_boom)
    # Must not raise — a bad tap must never kill the VAD worker thread.
    rec._invoke_frame_tap(np.zeros(512, dtype=np.float32))


def test_invoke_frame_tap_noop_when_unset():
    rec = _make_recorder()
    # No tap registered — must be a silent no-op.
    rec._invoke_frame_tap(np.zeros(512, dtype=np.float32))


def test_vad_loop_feeds_frame_tap_per_frame():
    """The VAD worker invokes the tap once per 512-sample frame."""
    import collections

    rec = _make_recorder()
    captured: list[np.ndarray] = []
    rec.set_frame_tap(captured.append)

    # Simulate the VAD-loop inner body: feed three whole frames.
    pending: collections.deque[np.ndarray] = collections.deque()
    pending_samples = 0
    block = np.ones(512 * 3, dtype=np.float32)
    pending.append(block)
    pending_samples += block.shape[0]

    from voice_commander.streaming_recorder import _VAD_FRAME_SIZE, _pop_frame

    while pending_samples >= _VAD_FRAME_SIZE:
        frame = _pop_frame(pending, _VAD_FRAME_SIZE)
        pending_samples -= _VAD_FRAME_SIZE
        rec._invoke_frame_tap(frame)
        rec._vad_gate.process(frame)

    assert len(captured) == 3
    assert all(f.shape == (512,) for f in captured)
