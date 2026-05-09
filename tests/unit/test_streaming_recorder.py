"""Unit tests for voice_commander.streaming_recorder.StreamingRecorder.

sounddevice is monkeypatched throughout so no real microphone access occurs.
Hardware tests (requiring an actual audio device) are marked with
@pytest.mark.hardware and are skipped in CI.
"""

from __future__ import annotations

import queue
import threading

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helpers: fake sounddevice primitives
# ---------------------------------------------------------------------------


def _make_fake_sd(monkeypatch, *, native_rate: float = 48000.0):
    """Patch sd.query_devices and sd.InputStream on the streaming_recorder module."""

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device: {"default_samplerate": native_rate},
    )

    class FakeInputStream:
        def __init__(self, **kwargs):
            self.callback = kwargs.get("callback")
            self.started = False
            self._closed = False

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

        def close(self):
            self._closed = True

    # Keep a reference so tests can retrieve the instance.
    instances: list[FakeInputStream] = []

    original_cls = FakeInputStream

    class _TrackingFakeInputStream(original_cls):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.InputStream",
        _TrackingFakeInputStream,
    )

    return instances


class MockResampler:
    """Pass-through resampler: returns the input unchanged."""

    def __init__(self, src_rate: int, dst_rate: int = 16000):
        pass

    def process(self, chunk: np.ndarray) -> np.ndarray:
        return chunk

    def reset(self) -> None:
        pass


class MockVADGate:
    """Returns a fake utterance ndarray on the Nth call to process()."""

    def __init__(self, trigger_frame: int = 3):
        self._count = 0
        self._trigger = trigger_frame

    def process(self, frame: np.ndarray):
        self._count += 1
        if self._count == self._trigger:
            return np.ones(1600, dtype=np.float32)  # fake utterance
        return None

    def reset(self) -> None:
        self._count = 0


def _make_recorder(monkeypatch, *, vad_gate=None, utterance_sink=None, native_rate=48000.0, device_name=""):
    """Construct a StreamingRecorder with all hardware patched out."""
    from voice_commander.streaming_recorder import StreamingRecorder

    stream_instances = _make_fake_sd(monkeypatch, native_rate=native_rate)
    monkeypatch.setattr("voice_commander.streaming_recorder.Resampler", MockResampler)

    if vad_gate is None:
        vad_gate = MockVADGate()
    if utterance_sink is None:

        def utterance_sink(_: object) -> None:
            return None

    recorder = StreamingRecorder(
        device=None,
        channels=1,
        vad_gate=vad_gate,
        utterance_sink=utterance_sink,
        device_name=device_name,
    )
    return recorder, stream_instances


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_open_session_sets_state_to_open(monkeypatch):
    recorder, _ = _make_recorder(monkeypatch)

    assert recorder.is_open is False
    recorder.open_session()
    assert recorder.is_open is True
    # Cleanup
    recorder.close_session()


def test_close_session_returns_to_idle(monkeypatch):
    recorder, _ = _make_recorder(monkeypatch)

    recorder.open_session()
    assert recorder.is_open is True

    recorder.close_session()
    assert recorder.is_open is False


def test_double_open_is_no_op(monkeypatch):
    recorder, stream_instances = _make_recorder(monkeypatch)

    recorder.open_session()
    recorder.open_session()  # second call must not raise

    # Only one InputStream should have been created.
    assert len(stream_instances) == 1
    assert recorder.is_open is True

    recorder.close_session()


def test_close_when_not_open_is_no_op(monkeypatch):
    recorder, _ = _make_recorder(monkeypatch)

    # Must not raise; recorder stays idle.
    recorder.close_session()
    assert recorder.is_open is False


def test_vad_worker_processes_frames_and_calls_utterance_sink(monkeypatch):
    """Push synthetic frames through the captured PortAudio callback and verify
    the utterance_sink fires when MockVADGate returns an utterance."""

    received: list[np.ndarray] = []
    sink_event = threading.Event()

    def utterance_sink(arr: np.ndarray) -> None:
        received.append(arr)
        sink_event.set()

    vad_gate = MockVADGate(trigger_frame=3)
    recorder, stream_instances = _make_recorder(
        monkeypatch, vad_gate=vad_gate, utterance_sink=utterance_sink
    )

    recorder.open_session()

    # Retrieve the PortAudio callback that was registered.
    stream = stream_instances[-1]
    assert stream.callback is not None, "PortAudio callback was not registered"

    # Simulate the VAD_FRAME_SIZE (512) so each chunk = one complete frame.
    frame = np.zeros(512, dtype=np.float32)

    class _FakeStatus:
        input_overflow = False

    status = _FakeStatus()

    # Push 3 frames — the 3rd triggers the mock VADGate.
    for _ in range(3):
        stream.callback(frame.reshape(-1, 1), 512, None, status)

    triggered = sink_event.wait(timeout=3.0)
    assert triggered, "utterance_sink was never called within 3 s"
    assert len(received) == 1
    assert received[0].shape == (1600,)

    recorder.close_session()


def test_input_overflow_logged_does_not_crash(monkeypatch, caplog):
    """When the PortAudio status indicates input_overflow, the recorder logs a
    warning but does not raise."""
    import logging

    recorder, stream_instances = _make_recorder(monkeypatch)
    recorder.open_session()

    stream = stream_instances[-1]
    frame = np.zeros(512, dtype=np.float32)

    class _OverflowStatus:
        input_overflow = True

        def __bool__(self):
            return True

    with caplog.at_level(logging.WARNING, logger="voice_commander.streaming_recorder"):
        stream.callback(frame.reshape(-1, 1), 512, None, _OverflowStatus())

    assert any("overflow" in record.message.lower() for record in caplog.records)

    recorder.close_session()


def test_queue_full_drops_frame_without_crash(monkeypatch):
    """Fill raw_q to its maxsize (64) and push one more frame; no exception."""
    recorder, stream_instances = _make_recorder(monkeypatch)
    recorder.open_session()

    stream = stream_instances[-1]
    frame = np.zeros(512, dtype=np.float32)

    class _OkStatus:
        input_overflow = False

        def __bool__(self):
            return False

    status = _OkStatus()

    # Pause the VAD worker so it doesn't drain the queue while we fill it.
    # We achieve this by putting a sentinel now to stop the worker, then
    # refill the queue with real frames and push one more.
    # Simpler approach: fill the queue directly through the public attribute.
    while True:
        try:
            recorder._raw_q.put_nowait(frame)
        except queue.Full:
            break

    # Queue is now full. Pushing via callback must not raise.
    stream.callback(frame.reshape(-1, 1), 512, None, status)

    # Still open, no exception raised.
    assert recorder.is_open is True
    recorder.close_session()


# ---------------------------------------------------------------------------
# Teardown on partial failure tests
# ---------------------------------------------------------------------------


def test_open_session_cleans_up_stream_when_vad_thread_spawn_fails(monkeypatch):
    """If an exception is raised after the InputStream is started but before
    open_session() completes, _teardown() must close the stream and the
    recorder must return to IDLE state.

    We simulate the failure by making threading.Thread.__init__ raise after
    the stream has already been started.
    """
    from voice_commander.streaming_recorder import StreamingRecorder

    stream_instances = _make_fake_sd(monkeypatch, native_rate=48000.0)
    monkeypatch.setattr("voice_commander.streaming_recorder.Resampler", MockResampler)

    # Patch threading.Thread so that it raises when the target is _vad_loop.
    original_thread_cls = threading.Thread

    class _BoomThread(original_thread_cls):
        def __init__(self, *args, **kwargs):
            # Only explode when spawning the vad-worker thread (identified by name).
            if kwargs.get("name") == "vad-worker":
                raise RuntimeError("thread pool exhausted (simulated)")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("voice_commander.streaming_recorder.threading.Thread", _BoomThread)

    recorder = StreamingRecorder(
        device=None,
        channels=1,
        vad_gate=MockVADGate(),
        utterance_sink=lambda _: None,
    )

    with pytest.raises(RuntimeError, match="thread pool exhausted"):
        recorder.open_session()

    # The recorder must be back in IDLE (not stuck in OPENING).
    assert recorder.is_open is False

    # The InputStream that was created must have been closed.
    assert len(stream_instances) == 1
    assert stream_instances[0]._closed is True, (
        "InputStream was not closed after open_session() failure"
    )

    # The internal _stream reference must be cleared.
    assert recorder._stream is None


def test_open_session_cleans_up_when_stream_start_raises(monkeypatch):
    """If sd.InputStream.start() raises, _teardown() is called and the
    recorder stays IDLE."""
    from voice_commander.streaming_recorder import StreamingRecorder

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device: {"default_samplerate": 48000.0},
    )

    class _ExplodingInputStream:
        def __init__(self, **kwargs):
            self.callback = kwargs.get("callback")
            self._closed = False

        def start(self):
            raise OSError("device in use (simulated)")

        def stop(self):
            pass

        def close(self):
            self._closed = True

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.InputStream",
        _ExplodingInputStream,
    )
    monkeypatch.setattr("voice_commander.streaming_recorder.Resampler", MockResampler)

    recorder = StreamingRecorder(
        device=None,
        channels=1,
        vad_gate=MockVADGate(),
        utterance_sink=lambda _: None,
    )

    with pytest.raises(OSError, match="device in use"):
        recorder.open_session()

    assert recorder.is_open is False
    assert recorder._stream is None


def test_orphaned_vad_thread_blocks_new_session(monkeypatch):
    """If the VAD worker thread is still alive when open_session() is called
    a second time (after a previous session was somehow not cleaned up),
    open_session() must raise RuntimeError and leave the recorder IDLE.

    We simulate this by injecting a live thread as ``_vad_thread`` before
    calling open_session().
    """
    from voice_commander.streaming_recorder import StreamingRecorder

    _make_fake_sd(monkeypatch, native_rate=48000.0)
    monkeypatch.setattr("voice_commander.streaming_recorder.Resampler", MockResampler)

    recorder = StreamingRecorder(
        device=None,
        channels=1,
        vad_gate=MockVADGate(),
        utterance_sink=lambda _: None,
    )

    # Plant a live thread to simulate an orphaned worker.
    barrier = threading.Event()
    orphan = threading.Thread(target=barrier.wait, daemon=True)
    orphan.start()
    recorder._vad_thread = orphan

    try:
        with pytest.raises(RuntimeError, match="previous VAD worker thread is still alive"):
            recorder.open_session()

        # Recorder must be IDLE after the rejected open attempt.
        assert recorder.is_open is False
    finally:
        barrier.set()  # unblock the orphan thread so the test exits cleanly
        orphan.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Device name resolution tests
# ---------------------------------------------------------------------------


def test_resolve_by_name_fast_path(monkeypatch):
    """Saved index still points to the correct device — return it immediately."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    fake_devices = [
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
        {"name": "Headset Mic", "max_input_channels": 1, "default_samplerate": 16000.0},
    ]
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device=None: fake_devices if device is None else fake_devices[device],
    )

    result = _resolve_device_by_name(1, "Headset Mic")
    assert result == 1


def test_resolve_by_name_drift(monkeypatch):
    """Device moved to a new index — return the new index."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    fake_devices = [
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
        {"name": "USB Audio", "max_input_channels": 1, "default_samplerate": 48000.0},
        {"name": "Headset Mic", "max_input_channels": 1, "default_samplerate": 16000.0},
    ]
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device=None: fake_devices if device is None else fake_devices[device],
    )

    # Saved index 0 points to "Speakers", but we want "Headset Mic" which is now at 2.
    result = _resolve_device_by_name(0, "Headset Mic")
    assert result == 2


def test_resolve_by_name_not_found(monkeypatch):
    """Device not in list — return None (system default fallback)."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    fake_devices = [
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
    ]
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device=None: fake_devices if device is None else fake_devices[device],
    )

    result = _resolve_device_by_name(0, "Headset Mic")
    assert result is None


def test_resolve_by_name_empty_name(monkeypatch):
    """Empty device_name — return saved_index without querying devices."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    queried = []
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device=None: queried.append(device) or [],
    )

    result = _resolve_device_by_name(5, "")
    assert result == 5
    assert queried == [], "sd.query_devices must not be called when device_name is empty"


def test_open_session_uses_resolved_device_on_drift(monkeypatch):
    """open_session() must open the InputStream with the resolved (current) index."""
    from voice_commander.streaming_recorder import StreamingRecorder

    # Device list: "Headset Mic" is at index 2, not the saved index 0.
    fake_devices = [
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
        {"name": "USB Audio", "max_input_channels": 1, "default_samplerate": 48000.0},
        {"name": "Headset Mic", "max_input_channels": 1, "default_samplerate": 16000.0},
    ]

    device_args_seen: list = []

    def fake_query_devices(device=None):
        device_args_seen.append(device)
        if device is None:
            return fake_devices
        return fake_devices[device]

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        fake_query_devices,
    )

    stream_device_args: list = []

    class TrackingInputStream:
        def __init__(self, **kwargs):
            stream_device_args.append(kwargs.get("device"))
            self.callback = kwargs.get("callback")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.InputStream",
        TrackingInputStream,
    )
    monkeypatch.setattr("voice_commander.streaming_recorder.Resampler", MockResampler)

    recorder = StreamingRecorder(
        device=0,                # stale saved index
        channels=1,
        vad_gate=MockVADGate(),
        utterance_sink=lambda _: None,
        device_name="Headset Mic",
    )
    recorder.open_session()

    # The recorder's internal _device must have been updated to the resolved index.
    assert recorder._device == 2, f"Expected device=2, got {recorder._device}"
    # The InputStream must have been opened with the resolved index.
    assert stream_device_args == [2], f"Expected InputStream(device=2), got {stream_device_args}"

    recorder.close_session()


def test_recovery_aborts_when_session_closing(monkeypatch):
    """_attempt_stream_recovery() returns False when state is not OPEN."""
    from voice_commander.streaming_recorder import _SessionState

    recorder, _ = _make_recorder(monkeypatch)
    recorder.open_session()

    # Forcibly set state to CLOSING to simulate close_session() in progress.
    with recorder._state_lock:
        recorder._state = _SessionState.CLOSING

    result = recorder._attempt_stream_recovery()
    assert result is False

    # Clean up — manually reset state and stream to allow gc without errors.
    with recorder._state_lock:
        recorder._state = _SessionState.IDLE
    recorder._stream = None
    recorder._vad_thread = None
    recorder._resampler = None


def test_recovery_aborts_on_sentinel_in_queue(monkeypatch):
    """If a None sentinel is found while draining, recovery aborts and restores it."""
    recorder, _ = _make_recorder(monkeypatch)
    recorder.open_session()

    # Place a sentinel on the queue (simulates _teardown() in progress).
    recorder._raw_q.put(None)

    result = recorder._attempt_stream_recovery()

    assert result is False
    # The sentinel must be back on the queue so _vad_loop can see it.
    assert recorder._raw_q.get_nowait() is None

    # Clean up.
    with recorder._state_lock:
        from voice_commander.streaming_recorder import _SessionState
        recorder._state = _SessionState.IDLE
    recorder._stream = None
    recorder._vad_thread = None
    recorder._resampler = None


def test_recovery_succeeds_and_opens_new_stream(monkeypatch):
    """_attempt_stream_recovery() opens a fresh stream and returns True."""
    stream_instances: list = []

    class TrackingInputStream:
        def __init__(self, **kwargs):
            self.callback = kwargs.get("callback")
            self._closed = False
            stream_instances.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            self._closed = True

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device=None: {"default_samplerate": 48000.0},
    )
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.InputStream",
        TrackingInputStream,
    )
    monkeypatch.setattr("voice_commander.streaming_recorder.Resampler", MockResampler)

    from voice_commander.streaming_recorder import StreamingRecorder

    recorder = StreamingRecorder(
        device=None,
        channels=1,
        vad_gate=MockVADGate(),
        utterance_sink=lambda _: None,
    )
    recorder.open_session()
    assert len(stream_instances) == 1

    # Simulate the first stream dying.
    dead_stream = stream_instances[0]

    result = recorder._attempt_stream_recovery()

    assert result is True
    # A second InputStream must have been opened.
    assert len(stream_instances) == 2
    # The old stream should have been stopped (closed by recovery cleanup).
    # The new stream should be active.
    assert recorder._stream is stream_instances[1]

    recorder.close_session()
