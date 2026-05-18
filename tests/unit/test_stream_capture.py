"""Unit tests for streaming dictation mic capture (sounddevice mocked)."""

from __future__ import annotations

import queue

import numpy as np

import voice_commander.dictation_stream.capture as capture_mod
from voice_commander.dictation_stream.capture import MicCapture


class _FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


def _patch_sounddevice(monkeypatch, native_rate=48000):
    created = {}

    def _fake_input_stream(**kwargs):
        stream = _FakeStream(**kwargs)
        created["stream"] = stream
        return stream

    monkeypatch.setattr(
        capture_mod.sd, "query_devices", lambda kind: {"default_samplerate": float(native_rate)}
    )
    monkeypatch.setattr(capture_mod.sd, "InputStream", _fake_input_stream)
    return created


def test_start_opens_stream_at_native_rate(monkeypatch):
    created = _patch_sounddevice(monkeypatch, native_rate=44100)
    mc = MicCapture(queue.Queue())
    mc.start()
    assert mc.native_rate == 44100
    assert created["stream"].kwargs["samplerate"] == 44100
    assert created["stream"].kwargs["dtype"] == "float32"
    assert created["stream"].started is True


def test_callback_enqueues_copied_audio(monkeypatch):
    _patch_sounddevice(monkeypatch)
    q: queue.Queue = queue.Queue()
    mc = MicCapture(q)
    mc.start()
    block = np.ones((480, 1), dtype=np.float32)
    mc._on_audio(block, 480, None, None)
    got = q.get_nowait()
    assert got.shape == (480, 1)
    block[0, 0] = 99.0  # mutate original — queued copy must be unaffected
    assert got[0, 0] == 1.0


def test_stop_closes_stream_and_pushes_sentinel(monkeypatch):
    created = _patch_sounddevice(monkeypatch)
    q: queue.Queue = queue.Queue()
    mc = MicCapture(q)
    mc.start()
    mc.stop()
    assert created["stream"].closed is True
    assert q.get_nowait() is None
