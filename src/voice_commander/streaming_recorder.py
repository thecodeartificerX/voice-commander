from __future__ import annotations

import enum
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from .resampler import Resampler
from .vad_gate import VADGate

logger = logging.getLogger(__name__)

_VAD_FRAME_SIZE = 512  # samples at 16 kHz fed to VADGate per call


class _SessionState(enum.Enum):
    IDLE = "IDLE"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"


class StreamingRecorder:
    """Manages a sounddevice InputStream and a VAD worker thread.

    Opens/closes voice command sessions.  Audio flows from the PortAudio
    callback thread into a raw queue, then the VAD worker resamples,
    frames, and gates the audio, finally calling *utterance_sink* when
    a complete utterance is detected.

    Thread topology::

        [PortAudio callback thread]      [VAD worker thread]
         sd.InputStream callback          Resampler + VADGate
         device-native float32 mono
                │                              ▲
                │ chunk.copy()                 │ ndarray frames
                ▼                              │
            raw_q ────────────────────────────►│
            (queue.Queue, maxsize=64)           │
                                               └─── calls utterance_sink(ndarray) on speech-end

    Args:
        device: PortAudio device index, or ``None`` for the system default.
        channels: Number of capture channels (should be 1 / mono).
        vad_gate: Caller-owned :class:`VADGate` instance; reset on each
            session open.
        utterance_sink: Callback invoked on the VAD worker thread with the
            completed utterance as a 1-D float32 ndarray at 16 kHz.
        vad_sample_rate: Target sample rate for the VADGate (default 16 000).
    """

    def __init__(
        self,
        device: int | None,
        channels: int,
        vad_gate: VADGate,
        utterance_sink: Callable[[npt.NDArray[np.float32]], None],
        vad_sample_rate: int = 16000,
    ) -> None:
        self._device = device if device is not None and device >= 0 else None
        self._channels = channels
        self._vad_gate = vad_gate
        self._utterance_sink = utterance_sink
        self._vad_sample_rate = vad_sample_rate

        self._state = _SessionState.IDLE
        self._state_lock = threading.Lock()

        self._stream: Any | None = None
        self._raw_q: queue.Queue[npt.NDArray[np.float32] | None] = queue.Queue(maxsize=64)
        self._resampler: Resampler | None = None
        self._vad_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """``True`` when the session is in the OPEN state."""
        return self._state is _SessionState.OPEN

    def open_session(self) -> None:
        """Open the audio stream and start the VAD worker thread.

        If a session is already open (or currently transitioning), this
        call is a no-op (with a debug log).
        """
        with self._state_lock:
            if self._state is not _SessionState.IDLE:
                logger.debug("open_session() ignored — current state is %s", self._state.value)
                return
            self._state = _SessionState.OPENING
            logger.debug("Session state → OPENING")

        try:
            # Query the device's native sample rate (WASAPI only accepts it).
            device_info: Any = sd.query_devices(self._device)
            native_rate = int(device_info["default_samplerate"])
            logger.info(
                "StreamingRecorder: opening session (device=%s, native_rate=%d, channels=%d)",
                self._device,
                native_rate,
                self._channels,
            )

            # Fresh Resampler for this session (src_rate comes from device query).
            self._resampler = Resampler(src_rate=native_rate, dst_rate=self._vad_sample_rate)

            # Reset VADGate for a clean session.
            self._vad_gate.reset()

            # Fresh queue for this session.
            self._raw_q = queue.Queue(maxsize=64)

            # Open and start the InputStream.
            self._stream = sd.InputStream(
                samplerate=native_rate,
                channels=self._channels,
                dtype="float32",
                device=self._device,
                callback=self._on_audio,
            )
            self._stream.start()

            # Spawn VAD worker thread.
            self._vad_thread = threading.Thread(
                target=self._vad_loop,
                name="vad-worker",
                daemon=True,
            )
            self._vad_thread.start()

        except Exception:
            # Roll back to IDLE so the caller can retry or handle the error.
            with self._state_lock:
                self._state = _SessionState.IDLE
            logger.exception("StreamingRecorder: failed to open session")
            raise

        with self._state_lock:
            self._state = _SessionState.OPEN
            logger.debug("Session state → OPEN")
        logger.info("StreamingRecorder: session open")

    def close_session(self) -> None:
        """Stop the audio stream and shut down the VAD worker thread.

        Blocks until the VAD worker thread has exited (up to 5 s).
        If a session is not open (or currently transitioning), this call
        is a no-op (with a debug log).
        """
        with self._state_lock:
            if self._state is not _SessionState.OPEN:
                logger.debug("close_session() ignored — current state is %s", self._state.value)
                return
            self._state = _SessionState.CLOSING
            logger.debug("Session state → CLOSING")

        logger.info("StreamingRecorder: closing session")

        # Stop and close the PortAudio stream first so the callback stops.
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.exception("StreamingRecorder: error stopping stream")
            finally:
                self._stream = None

        # Poison the queue so the VAD worker exits its loop.
        self._raw_q.put(None)

        # Join the VAD worker thread.
        if self._vad_thread is not None:
            self._vad_thread.join(timeout=5.0)
            if self._vad_thread.is_alive():
                logger.warning("StreamingRecorder: VAD worker thread did not exit within 5 s")
            self._vad_thread = None

        with self._state_lock:
            self._state = _SessionState.IDLE
            logger.debug("Session state → IDLE")
        logger.info("StreamingRecorder: session closed")

    # ------------------------------------------------------------------
    # PortAudio callback (called on the PortAudio thread)
    # ------------------------------------------------------------------

    def _on_audio(
        self,
        indata: npt.NDArray[np.float32],
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        if status:
            if status.input_overflow:
                logger.warning("StreamingRecorder: input overflow detected")
            else:
                logger.warning("StreamingRecorder: sounddevice status: %s", status)
        chunk: npt.NDArray[np.float32] = indata.copy()
        try:
            self._raw_q.put_nowait(chunk)
        except queue.Full:
            logger.warning("StreamingRecorder: raw_q full — dropping audio chunk")

    # ------------------------------------------------------------------
    # VAD worker (runs on the VAD worker thread)
    # ------------------------------------------------------------------

    def _vad_loop(self) -> None:
        """Pull raw chunks, resample, frame, gate, and fire utterance_sink."""
        logger.debug("VAD worker started")
        buf = np.empty(0, dtype=np.float32)

        while True:
            chunk = self._raw_q.get()
            if chunk is None:
                logger.debug("VAD worker received sentinel; exiting")
                break

            assert self._resampler is not None, "Resampler not initialised"
            resampled: npt.NDArray[np.float32] = self._resampler.process(chunk.flatten())
            buf = np.concatenate((buf, resampled))

            while buf.shape[0] >= _VAD_FRAME_SIZE:
                frame: npt.NDArray[np.float32] = buf[:_VAD_FRAME_SIZE]
                buf = buf[_VAD_FRAME_SIZE:]

                result = self._vad_gate.process(frame)
                if result is not None:
                    logger.debug("VAD worker: utterance complete (%d samples)", len(result))
                    try:
                        self._utterance_sink(result)
                    except Exception:
                        logger.exception("StreamingRecorder: utterance_sink raised")

        logger.debug("VAD worker exited")
