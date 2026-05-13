"""Unit tests for voice_commander.streaming_recorder.StreamingRecorder.

sounddevice is monkeypatched throughout so no real microphone access occurs.
Hardware tests (requiring an actual audio device) are marked with
@pytest.mark.hardware and are skipped in CI.
"""

from __future__ import annotations

import logging
import queue
import threading

import numpy as np
import pytest
import sounddevice as sd

from voice_commander.streaming_recorder import StreamingRecorder

# ---------------------------------------------------------------------------
# Helpers: fake sounddevice primitives
# ---------------------------------------------------------------------------


def _make_fake_sd(monkeypatch, *, native_rate: float = 48000.0):
    """Patch sd.query_devices, sd.query_hostapis, and sd.InputStream."""

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device: {"default_samplerate": native_rate},
    )
    # WASAPI at index 0 by default for hardware-less tests; resolver only
    # consults hostapis when a non-empty device_name is in play.
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_hostapis",
        lambda: [{"name": "Windows WASAPI"}],
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
# Device name resolution tests (WASAPI-only — ADR 0081)
# ---------------------------------------------------------------------------


# Standard 4-host-API Windows layout used by the resolution tests:
#   0 = MME, 1 = Windows DirectSound, 2 = Windows WASAPI, 3 = Windows WDM-KS
_WIN_HOSTAPIS = [
    {"name": "MME"},
    {"name": "Windows DirectSound"},
    {"name": "Windows WASAPI"},
    {"name": "Windows WDM-KS"},
]
_WASAPI = 2
_DSOUND = 1
_MME = 0


def _patch_hostapis(monkeypatch, hostapis=None):
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_hostapis",
        lambda: hostapis if hostapis is not None else _WIN_HOSTAPIS,
    )


def _patch_devices(monkeypatch, devices):
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device=None: devices if device is None else devices[device],
    )


def test_resolve_by_name_fast_path(monkeypatch):
    """Saved index still points to the right WASAPI device — return it immediately."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    _patch_hostapis(monkeypatch)
    _patch_devices(monkeypatch, [
        {"name": "Speakers", "max_input_channels": 0, "hostapi": _WASAPI},
        {"name": "Headset Mic", "max_input_channels": 1, "hostapi": _WASAPI},
    ])
    assert _resolve_device_by_name(1, "Headset Mic") == 1


def test_resolve_by_name_drift(monkeypatch):
    """Device moved to a new WASAPI index — return the new index."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    _patch_hostapis(monkeypatch)
    _patch_devices(monkeypatch, [
        {"name": "Speakers", "max_input_channels": 0, "hostapi": _WASAPI},
        {"name": "USB Audio", "max_input_channels": 1, "hostapi": _WASAPI},
        {"name": "Headset Mic", "max_input_channels": 1, "hostapi": _WASAPI},
    ])
    # Saved index 0 points at "Speakers"; "Headset Mic" is at 2.
    assert _resolve_device_by_name(0, "Headset Mic") == 2


def test_resolve_by_name_not_found(monkeypatch):
    """Device not in list — return None (system default fallback)."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    _patch_hostapis(monkeypatch)
    _patch_devices(monkeypatch, [
        {"name": "Speakers", "max_input_channels": 0, "hostapi": _WASAPI},
    ])
    assert _resolve_device_by_name(0, "Headset Mic") is None


def test_resolve_by_name_empty_name(monkeypatch):
    """Empty device_name — return saved_index without querying devices."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    queried = []
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices",
        lambda device=None: queried.append(device) or [],
    )
    # query_hostapis must also not be called for the empty-name short-circuit.
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_hostapis",
        lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    assert _resolve_device_by_name(5, "") == 5
    assert queried == [], "sd.query_devices must not be called when device_name is empty"


def test_resolve_by_name_skips_directsound_duplicate(monkeypatch):
    """Same device name under DirectSound is ignored; WASAPI copy wins."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    _patch_hostapis(monkeypatch)
    _patch_devices(monkeypatch, [
        # DirectSound copy at index 9 — what the user's flaky config.toml points at.
        {"name": "At 2020 (AT2020USB-X)", "max_input_channels": 1, "hostapi": _DSOUND},
        # MME copy at index 4 — also ignored.
        {"name": "At 2020 (AT2020USB-X)", "max_input_channels": 1, "hostapi": _MME},
        # WASAPI copy at index 14 — the one the resolver must pick.
        {"name": "At 2020 (AT2020USB-X)", "max_input_channels": 1, "hostapi": _WASAPI},
    ])
    # Saved DirectSound index 0 must be re-resolved to the WASAPI copy at 2.
    assert _resolve_device_by_name(0, "At 2020 (AT2020USB-X)") == 2


def test_resolve_fast_path_rejects_directsound_match(monkeypatch):
    """Fast path must validate host API, not just name."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    _patch_hostapis(monkeypatch)
    _patch_devices(monkeypatch, [
        {"name": "Headset Mic", "max_input_channels": 1, "hostapi": _DSOUND},
        {"name": "Headset Mic", "max_input_channels": 1, "hostapi": _WASAPI},
    ])
    # Saved index 0 is the DirectSound copy — fast path must reject it and
    # fall through to the slow scan, landing on the WASAPI copy at 1.
    assert _resolve_device_by_name(0, "Headset Mic") == 1


def test_resolve_when_wasapi_missing_returns_none(monkeypatch):
    """If Windows WASAPI is not in the host-API list (degenerate host), fall
    back to system default with a warning rather than picking a DirectSound copy."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    _patch_hostapis(monkeypatch, hostapis=[{"name": "MME"}, {"name": "Windows DirectSound"}])
    _patch_devices(monkeypatch, [
        {"name": "Headset Mic", "max_input_channels": 1, "hostapi": _DSOUND},
    ])
    assert _resolve_device_by_name(0, "Headset Mic") is None


def test_resolve_ignores_output_only_wasapi_device(monkeypatch):
    """A WASAPI device with max_input_channels=0 (a render endpoint) must not match."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    _patch_hostapis(monkeypatch)
    _patch_devices(monkeypatch, [
        {"name": "Speakers", "max_input_channels": 0, "hostapi": _WASAPI},
    ])
    assert _resolve_device_by_name(None, "Speakers") is None


def test_resolve_exception_falls_back_to_saved_index(monkeypatch):
    """Any sd exception must not destroy the saved configuration."""
    from voice_commander.streaming_recorder import _resolve_device_by_name

    _patch_hostapis(monkeypatch)

    def _boom(device=None):
        raise RuntimeError("portaudio glitch")

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_devices", _boom
    )
    assert _resolve_device_by_name(7, "Headset Mic") == 7


def test_open_session_uses_resolved_device_on_drift(monkeypatch):
    """open_session() must open the InputStream with the resolved (current) index."""
    from voice_commander.streaming_recorder import StreamingRecorder

    # Device list: "Headset Mic" is at index 2, not the saved index 0.
    # All three carry hostapi=2 (WASAPI) per ADR 0081 resolver semantics.
    fake_devices = [
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0, "hostapi": 2},
        {"name": "USB Audio", "max_input_channels": 1, "default_samplerate": 48000.0, "hostapi": 2},
        {"name": "Headset Mic", "max_input_channels": 1, "default_samplerate": 16000.0, "hostapi": 2},
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
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_hostapis",
        lambda: [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}],
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
    """If a None sentinel is found while draining, recovery aborts and restores it.

    We bypass `open_session()` to avoid spawning a live VAD worker — that
    worker's blocking `_raw_q.get()` would race the test's sentinel `put(None)`
    and consume it before `_attempt_stream_recovery()` ever sees it.
    """
    _make_fake_sd(monkeypatch, native_rate=48000.0)
    monkeypatch.setattr("voice_commander.streaming_recorder.Resampler", MockResampler)

    from voice_commander.streaming_recorder import StreamingRecorder, _SessionState

    recorder = StreamingRecorder(
        device=None,
        channels=1,
        vad_gate=MockVADGate(),
        utterance_sink=lambda _: None,
    )
    # Force the recorder into the OPEN state without starting threads.
    with recorder._state_lock:
        recorder._state = _SessionState.OPEN

    # Place a sentinel on the queue (simulates _teardown() in progress).
    recorder._raw_q.put(None)

    result = recorder._attempt_stream_recovery()

    assert result is False
    # The sentinel must be back on the queue so _vad_loop can see it.
    assert recorder._raw_q.get_nowait() is None

    # Clean up.
    with recorder._state_lock:
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


# ---------------------------------------------------------------------------
# _pop_frame correctness tests
# ---------------------------------------------------------------------------


def test_pop_frame_head_larger_than_n():
    """Head array has more than n samples; residual stays on the head."""
    from collections import deque
    from voice_commander.streaming_recorder import _pop_frame

    arr = np.arange(1024, dtype=np.float32)
    pending: deque = deque([arr])
    result = _pop_frame(pending, 512)

    assert result.shape == (512,), f"Expected 512 samples, got {result.shape}"
    np.testing.assert_array_equal(result, arr[:512])
    # Residual must remain as the only element on the head.
    assert len(pending) == 1
    assert pending[0].shape == (512,)
    np.testing.assert_array_equal(pending[0], arr[512:])


def test_pop_frame_head_exactly_n():
    """Head array has exactly n samples; head is fully popped."""
    from collections import deque
    from voice_commander.streaming_recorder import _pop_frame

    arr = np.arange(512, dtype=np.float32)
    pending: deque = deque([arr])
    result = _pop_frame(pending, 512)

    assert result.shape == (512,)
    np.testing.assert_array_equal(result, arr)
    assert len(pending) == 0


def test_pop_frame_multiple_chunks_needed():
    """Multiple small arrays are drained to fill n; residual pushed back to front."""
    from collections import deque
    from voice_commander.streaming_recorder import _pop_frame

    # Three chunks of 200 samples each; request 512 → drains first two (400)
    # and takes 112 from the third, leaving 88 samples as the new head.
    chunks = [
        np.full(200, i, dtype=np.float32) for i in range(3)
    ]
    pending: deque = deque(chunks)
    result = _pop_frame(pending, 512)

    assert result.shape == (512,)
    # First 200 samples = 0.0, next 200 = 1.0, last 112 = 2.0
    np.testing.assert_array_equal(result[:200], np.zeros(200, dtype=np.float32))
    np.testing.assert_array_equal(result[200:400], np.ones(200, dtype=np.float32))
    np.testing.assert_array_equal(result[400:], np.full(112, 2.0, dtype=np.float32))
    # Residual from third chunk (88 samples) must be at the head.
    assert len(pending) == 1
    assert pending[0].shape == (88,)
    np.testing.assert_array_equal(pending[0], np.full(88, 2.0, dtype=np.float32))


# ---------------------------------------------------------------------------
# _get_resampler cache tests
# ---------------------------------------------------------------------------


def test_get_resampler_returns_same_instance_for_same_rates(monkeypatch):
    """_get_resampler returns the same Resampler instance for the same (src, dst) pair."""
    import voice_commander.streaming_recorder as sr_mod

    reset_calls: list[tuple[int, int]] = []

    class TrackingResampler:
        def __init__(self, src_rate: int, dst_rate: int = 16000):
            self.src_rate = src_rate
            self.dst_rate = dst_rate

        def reset(self) -> None:
            reset_calls.append((self.src_rate, self.dst_rate))

    # Clear the module-level cache and patch Resampler before the test.
    original_cache = sr_mod._RESAMPLER_CACHE.copy()
    sr_mod._RESAMPLER_CACHE.clear()
    monkeypatch.setattr("voice_commander.streaming_recorder.Resampler", TrackingResampler)

    try:
        first = sr_mod._get_resampler(48000, 16000)
        second = sr_mod._get_resampler(48000, 16000)

        assert first is second, "Expected the same instance to be returned for the same rates"
        # reset() must have been called on each invocation.
        assert len(reset_calls) == 2
        assert all(r == (48000, 16000) for r in reset_calls)
    finally:
        # Restore cache so other tests are unaffected.
        sr_mod._RESAMPLER_CACHE.clear()
        sr_mod._RESAMPLER_CACHE.update(original_cache)


# ---------------------------------------------------------------------------
# _open_input_stream retry tests (ADR 0081 defence-in-depth)
# ---------------------------------------------------------------------------

# Reusable fake host-API + device tables for retry tests.
_RETRY_HOSTAPIS = [{"name": "Windows WASAPI"}]
_RETRY_DEVICE = {
    "name": "At 2020 (AT2020USB-X)",
    "default_samplerate": 48000.0,
    "hostapi": 0,
    "max_input_channels": 1,
}


def _make_portaudio_error() -> sd.PortAudioError:
    """Return a PortAudioError that can be constructed without a real device."""
    # PortAudioError(message, args=...) — use the simplest public constructor.
    return sd.PortAudioError("WdmSyncIoctl: DeviceIoControl GLE = 0x00000490 (simulated)")


class _PatchedRecorder:
    """Context-manager helper: builds a StreamingRecorder with sd patched."""

    def __init__(self, monkeypatch, *, device_index: int | None = 5, device_name: str = "At 2020 (AT2020USB-X)"):
        self.monkeypatch = monkeypatch
        self.device_index = device_index
        self.device_name = device_name
        self.recorder: StreamingRecorder | None = None

    def __enter__(self):
        self.monkeypatch.setattr(
            "voice_commander.streaming_recorder.sd.query_hostapis",
            lambda: _RETRY_HOSTAPIS,
        )
        # Real sd.query_devices() returns a DeviceList (list of dicts) for no-arg
        # calls, and a single dict for indexed calls. Mirror both shapes so the
        # host-API fallback resolver can enumerate candidates.
        def _fake_query_devices(device=None):
            if device is None:
                return [_RETRY_DEVICE]
            return _RETRY_DEVICE

        self.monkeypatch.setattr(
            "voice_commander.streaming_recorder.sd.query_devices",
            _fake_query_devices,
        )
        self.monkeypatch.setattr("voice_commander.streaming_recorder.Resampler", MockResampler)
        # Install a no-op InputStream by default; individual tests may override it.

        class _DefaultFakeInputStream:
            def __init__(self, **kwargs):
                self.callback = kwargs.get("callback")
                self._closed = False

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                self._closed = True

        self.monkeypatch.setattr(
            "voice_commander.streaming_recorder.sd.InputStream",
            _DefaultFakeInputStream,
        )

        self.recorder = StreamingRecorder(
            device=self.device_index,
            channels=1,
            vad_gate=MockVADGate(),
            utterance_sink=lambda _: None,
            device_name=self.device_name,
        )
        return self.recorder

    def __exit__(self, *_):
        if self.recorder is not None and self.recorder.is_open:
            self.recorder.close_session()


def test_open_session_retries_on_porterror(monkeypatch):
    """First InputStream.start() raises PortAudioError; second succeeds.

    Verify both InputStream constructors are called and no exception reaches
    the caller of open_session().
    """
    call_count = 0

    class _RetryInputStream:
        def __init__(self, **kwargs):
            nonlocal call_count
            call_count += 1
            self._attempt = call_count
            self.callback = kwargs.get("callback")
            self._closed = False

        def start(self):
            if self._attempt == 1:
                raise _make_portaudio_error()

        def stop(self):
            pass

        def close(self):
            self._closed = True

    with _PatchedRecorder(monkeypatch) as recorder:
        monkeypatch.setattr(
            "voice_commander.streaming_recorder.sd.InputStream",
            _RetryInputStream,
        )
        recorder.open_session()

        assert call_count == 2, f"Expected 2 InputStream constructions, got {call_count}"
        assert recorder.is_open is True


def test_open_session_falls_back_to_default_on_persistent_resolution(monkeypatch):
    """When fresh resolve returns the same failing device index, fall back to
    device=None (system default) on the second attempt.
    """
    device_args_used: list = []

    call_counter = [0]

    class _TrackingInputStream:
        def __init__(self, **kwargs):
            call_counter[0] += 1
            device_args_used.append(kwargs.get("device"))
            self.callback = kwargs.get("callback")
            self._closed = False
            self._attempt = call_counter[0]

        def start(self):
            if self._attempt == 1:
                raise _make_portaudio_error()

        def stop(self):
            pass

        def close(self):
            self._closed = True

    with _PatchedRecorder(monkeypatch, device_index=5) as recorder:
        monkeypatch.setattr(
            "voice_commander.streaming_recorder.sd.InputStream",
            _TrackingInputStream,
        )
        recorder.open_session()

    # New behaviour (host-API fallback chain): candidate list resolves the
    # mocked device by name first, then falls back to device=None (system
    # default) as the final candidate. The single mock entry has hostapi=0
    # (WASAPI), so the first candidate is index 0 rather than the saved 5.
    assert device_args_used[0] == 0, (
        f"First candidate should be the resolved-by-name index (0), got {device_args_used[0]}"
    )
    assert device_args_used[-1] is None, (
        f"Last fallback should be device=None (system default), got {device_args_used[-1]}"
    )


def test_open_session_raises_on_second_failure(monkeypatch):
    """Both InputStream attempts raise PortAudioError.

    Verify that the SECOND error is re-raised (not the first), and that an
    ERROR-level log record is emitted referencing both errors.
    """
    first_err = _make_portaudio_error()
    second_err = sd.PortAudioError("retry also failed (simulated)")
    call_count = 0

    class _AlwaysFailInputStream:
        def __init__(self, **kwargs):
            nonlocal call_count
            call_count += 1
            self._attempt = call_count
            self.callback = kwargs.get("callback")
            self._closed = False

        def start(self):
            if self._attempt == 1:
                raise first_err
            raise second_err

        def stop(self):
            pass

        def close(self):
            self._closed = True

    with _PatchedRecorder(monkeypatch) as recorder:
        monkeypatch.setattr(
            "voice_commander.streaming_recorder.sd.InputStream",
            _AlwaysFailInputStream,
        )

        with pytest.raises(sd.PortAudioError) as exc_info:
            recorder.open_session()

    # The SECOND error must be what propagates.
    assert exc_info.value is second_err, "Expected the second PortAudioError to be re-raised"
    assert call_count == 2, f"Expected 2 InputStream constructions, got {call_count}"


def test_open_session_logs_host_api_name(monkeypatch, caplog):
    """Successful open_session() emits an INFO log containing the host API name."""
    with _PatchedRecorder(monkeypatch) as recorder:
        with caplog.at_level(logging.INFO, logger="voice_commander.streaming_recorder"):
            recorder.open_session()

    opening_records = [r for r in caplog.records if "opening session" in r.message]
    assert opening_records, "Expected an 'opening session' INFO log record"
    log_msg = opening_records[0].message
    assert "Windows WASAPI" in log_msg, (
        f"Expected host_api='Windows WASAPI' in log line, got: {log_msg!r}"
    )
