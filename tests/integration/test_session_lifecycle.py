"""Integration tests: StreamingRecorder session open/close lifecycle.

Tests real Resampler + VADGate state-machine wiring together with a mocked
sounddevice InputStream so that no physical microphone is required.

Thread safety and clean shutdown are the primary concerns validated here:
- Zero utterances emitted for pure silence.
- Open followed immediately by close leaves no dangling threads.
- A second close() on an already-closed recorder is a silent no-op.
- A second open() while already open is a silent no-op.

CUDA is NOT required for these tests — VADGate is constructed with a stub
VADIterator so that no silero model weights are downloaded. The Resampler is
real (uses soxr) to exercise the actual resampling path.

Patching strategy
-----------------
``VADGate.__init__`` does ``from silero_vad import VADIterator`` at call time
(a local import). The correct patch target is therefore ``silero_vad.VADIterator``
— patching the name in the silero_vad module before it is imported locally.
"""

from __future__ import annotations

import queue
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import numpy.typing as npt
import pytest

# ---------------------------------------------------------------------------
# CUDA guard (kept for parity; hotkey tests do not require CUDA)
# ---------------------------------------------------------------------------

try:
    import ctranslate2  # type: ignore[import]

    HAS_CUDA = ctranslate2.get_cuda_device_count() > 0
except Exception:
    HAS_CUDA = False

requires_cuda = pytest.mark.skipif(not HAS_CUDA, reason="CUDA not available")


# ---------------------------------------------------------------------------
# Stub VADIterator: silero-compatible interface, always silent
# ---------------------------------------------------------------------------


class _AlwaysSilentVADIterator:
    """Silero VADIterator drop-in that always signals silence."""

    def __init__(self, model: Any, **kwargs: Any) -> None:
        pass

    def __call__(self, frame: npt.NDArray[np.float32], sr: int = 16000) -> dict:  # type: ignore[override]
        return {}

    def reset_states(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NATIVE_RATE = 48000  # pretend WASAPI device runs at 48 kHz
_VAD_RATE = 16000
_VAD_FRAME_SIZE = 512  # samples at 16 kHz — matches streaming_recorder._VAD_FRAME_SIZE


def _make_device_info(rate: int = _NATIVE_RATE) -> dict:
    return {"default_samplerate": float(rate), "name": "Mock Microphone"}


def _make_mock_stream() -> MagicMock:
    stream = MagicMock()
    stream.start.return_value = None
    stream.stop.return_value = None
    stream.close.return_value = None
    return stream


# ---------------------------------------------------------------------------
# Context manager: patch silero + sounddevice together
# ---------------------------------------------------------------------------


class _RecorderContext:
    """Helper that builds and patches a StreamingRecorder for testing.

    Usage::

        with _RecorderContext(utterance_sink=...) as ctx:
            ctx.recorder.open_session()
            ...
            ctx.recorder.close_session()
    """

    def __init__(self, utterance_sink: Any = None) -> None:
        self._utterance_sink = utterance_sink or (lambda _: None)
        self._patches: list[Any] = []
        self.recorder: Any = None
        self.mock_stream: MagicMock = MagicMock()
        self.mock_stream_cls: MagicMock = MagicMock()

    def __enter__(self) -> _RecorderContext:
        from voice_commander.streaming_recorder import StreamingRecorder
        from voice_commander.vad_gate import VADGate

        self.mock_stream = _make_mock_stream()
        self.mock_stream_cls = MagicMock(return_value=self.mock_stream)

        p_devices = patch("sounddevice.query_devices", return_value=_make_device_info())
        p_stream = patch("sounddevice.InputStream", self.mock_stream_cls)
        p_silero = patch("silero_vad.VADIterator", _AlwaysSilentVADIterator)

        for p in (p_devices, p_stream, p_silero):
            p.start()
            self._patches.append(p)

        # Build a real VADGate (which will pick up our patched VADIterator).
        # The model arg is unused by _AlwaysSilentVADIterator; pass a sentinel.
        vad_gate = VADGate(
            model=object(),
            threshold=0.5,
            min_speech_ms=250,
            min_silence_ms=100,
            speech_pad_ms=30,
            pre_roll_ms=320,
        )

        self.recorder = StreamingRecorder(
            device=None,
            channels=1,
            vad_gate=vad_gate,
            utterance_sink=self._utterance_sink,
            vad_sample_rate=_VAD_RATE,
        )
        return self

    def __exit__(self, *args: Any) -> None:
        for p in reversed(self._patches):
            p.stop()


# ---------------------------------------------------------------------------
# Helper: pump silence frames into raw_q
# ---------------------------------------------------------------------------


def _pump_silence(recorder: Any, duration_s: float = 3.0) -> None:
    """Push silence chunks into the recorder's raw_q, then a sentinel.

    Simulates what the PortAudio callback does when no sound is detected.
    Writing directly into raw_q bypasses the real InputStream so that no
    audio device is needed. At _NATIVE_RATE the stream sends large chunks;
    we inject small frames at _VAD_RATE since our mock doesn't actually
    run through the resampler (we want deterministic VAD input).
    """
    silence = np.zeros(_VAD_FRAME_SIZE, dtype=np.float32)
    total_frames = int(_VAD_RATE * duration_s) // _VAD_FRAME_SIZE
    for _ in range(total_frames):
        try:
            recorder._raw_q.put_nowait(silence)
        except queue.Full:
            break
    # Sentinel to end the VAD loop.
    recorder._raw_q.put(None)


# ---------------------------------------------------------------------------
# Test 1 — silence session emits zero utterances and shuts down cleanly
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_silence_session_zero_utterances() -> None:
    """3 s of silence → zero utterances, all threads joined within timeout."""
    utterances: list[npt.NDArray[np.float32]] = []

    with _RecorderContext(utterance_sink=utterances.append) as ctx:
        ctx.recorder.open_session()
        assert ctx.recorder.is_open

        # Pump silence from a background thread so we don't block the test.
        feeder = threading.Thread(target=_pump_silence, args=(ctx.recorder, 3.0), daemon=True)
        feeder.start()
        feeder.join(timeout=10.0)
        assert not feeder.is_alive(), "Feeder thread timed out"

        ctx.recorder.close_session()

    assert not ctx.recorder.is_open
    assert utterances == [], f"Expected 0 utterances, got {len(utterances)}"

    # VAD worker thread must have exited after close.
    if ctx.recorder._vad_thread is not None:
        assert not ctx.recorder._vad_thread.is_alive(), (
            "VAD worker thread still alive after close_session()"
        )


# ---------------------------------------------------------------------------
# Test 2 — open and immediately close: no errors, no utterances, no threads
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_session_open_close_clean() -> None:
    """Open and immediately close the session — no errors, threads cleaned up."""
    utterances: list[npt.NDArray[np.float32]] = []

    with _RecorderContext(utterance_sink=utterances.append) as ctx:
        ctx.recorder.open_session()
        assert ctx.recorder.is_open

        # Close immediately — the VAD worker may not have processed anything.
        ctx.recorder.close_session()

    assert not ctx.recorder.is_open
    assert utterances == []

    # sounddevice stream must have been stopped and closed exactly once.
    ctx.mock_stream.stop.assert_called_once()
    ctx.mock_stream.close.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3 — double close is a silent no-op
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_double_close_no_error() -> None:
    """Close an already-closed session twice — second call must not raise."""
    with _RecorderContext() as ctx:
        ctx.recorder.open_session()
        ctx.recorder.close_session()

        # Second close must not raise — it's a documented no-op.
        ctx.recorder.close_session()

    assert not ctx.recorder.is_open

    # The internal stream should only have been stopped/closed once.
    ctx.mock_stream.stop.assert_called_once()
    ctx.mock_stream.close.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4 — open while already open is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_double_open_no_error() -> None:
    """Call open_session() while already open — second call must be a no-op."""
    with _RecorderContext() as ctx:
        ctx.recorder.open_session()
        assert ctx.recorder.is_open

        # Second open while already OPEN must not start a second stream.
        ctx.recorder.open_session()

        # Only one InputStream should have been constructed.
        assert ctx.mock_stream_cls.call_count == 1, (
            f"Expected 1 InputStream instantiation, got {ctx.mock_stream_cls.call_count}"
        )

        ctx.recorder.close_session()

    assert not ctx.recorder.is_open


# ---------------------------------------------------------------------------
# Test 5 — mute/unmute lifecycle within an active session
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_mute_unmute_lifecycle(tmp_path: Any) -> None:
    """Full mute/unmute lifecycle within an active session."""
    from unittest.mock import MagicMock

    from voice_commander.daemon import StreamingDaemon
    from voice_commander.feedback import CapturingFeedbackSink
    from voice_commander.transcriber import TranscriptionResult

    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_open = False
    transcriber = MagicMock()
    matcher = MagicMock()
    dispatcher = MagicMock()

    result = TranscriptionResult(
        text="copy", confidence=0.95, language="en", duration_ms=500, no_speech_prob=0.05,
    )
    transcriber.transcribe.return_value = result
    match_result = MagicMock()
    matcher.match.return_value = match_result

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        matcher=matcher,
        dispatcher=dispatcher,
        output_dir=str(tmp_path / "outputs"),
    )

    # Start pipeline thread
    pipeline_thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    pipeline_thread.start()

    try:
        # 1. Open session
        daemon.on_scroll_lock()
        assert daemon._session_active is True
        assert daemon._muted is False

        # 2. Mute
        daemon.on_mute_toggle()
        assert daemon._muted is True

        # 3. Inject utterance while muted — should NOT dispatch
        done1 = threading.Event()
        original = daemon._process_utterance

        def _p1(utt: Any) -> None:
            original(utt)
            done1.set()

        daemon._process_utterance = _p1  # type: ignore[assignment]
        daemon._utt_q.put(np.zeros(1600, dtype=np.float32))
        assert done1.wait(timeout=5.0), "pipeline did not process utterance"
        assert dispatcher.dispatch.call_count == 0

        # 4. Unmute
        daemon.on_mute_toggle()
        assert daemon._muted is False

        # 5. Inject utterance while unmuted — SHOULD dispatch
        done2 = threading.Event()

        def _p2(utt: Any) -> None:
            original(utt)
            done2.set()

        daemon._process_utterance = _p2  # type: ignore[assignment]
        daemon._utt_q.put(np.zeros(1600, dtype=np.float32))
        assert done2.wait(timeout=5.0), "pipeline did not process utterance"
        assert dispatcher.dispatch.call_count == 1

        # 6. Close session
        daemon.on_scroll_lock()
        assert daemon._session_active is False
    finally:
        daemon._utt_q.put(None)
        pipeline_thread.join(timeout=3.0)
