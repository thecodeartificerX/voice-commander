from __future__ import annotations

import contextlib
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


def _resolve_device_by_name(saved_index: int | None, device_name: str) -> int | None:
    """Return the current PortAudio index for *device_name*, or *saved_index* as fallback.

    Fast path: if the device at *saved_index* still has the right name, return it
    immediately. Slow path: scan all input devices for a name match. If nothing
    matches, log a warning and return ``None`` (PortAudio system default).
    All sd exceptions are caught; returns *saved_index* on unexpected error.
    """
    if not device_name:
        return saved_index
    try:
        devices = sd.query_devices()
        # Fast path: check saved index still points to the right name.
        if saved_index is not None:
            try:
                info = devices[saved_index]
                if info["name"].strip().lower() == device_name.strip().lower():
                    return saved_index
            except (IndexError, KeyError):
                pass
        # Scan all input devices.
        for idx, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) > 0:
                if dev["name"].strip().lower() == device_name.strip().lower():
                    if idx != saved_index:
                        logger.info(
                            "StreamingRecorder: '%s' moved index %s → %d",
                            device_name, saved_index, idx,
                        )
                    return idx
        logger.warning(
            "StreamingRecorder: device '%s' not found; falling back to system default",
            device_name,
        )
        return None
    except Exception:
        logger.exception(
            "StreamingRecorder: device name resolution error; using saved index %s",
            saved_index,
        )
        return saved_index


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
        device_name: str = "",
    ) -> None:
        self._device = device if device is not None and device >= 0 else None
        self._channels = channels
        self._vad_gate = vad_gate
        self._utterance_sink = utterance_sink
        self._vad_sample_rate = vad_sample_rate
        self._device_name = device_name

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
        with self._state_lock:
            return self._state is _SessionState.OPEN

    def _teardown(self) -> None:
        # First pass: close the stream that was open when _teardown was called.
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
            with contextlib.suppress(Exception):
                self._stream.close()
            self._stream = None

        self._raw_q.put(None)

        if self._vad_thread is not None:
            self._vad_thread.join(timeout=5.0)
            if self._vad_thread.is_alive():
                logger.warning("StreamingRecorder: VAD worker did not exit within 5 s")
            self._vad_thread = None

        # Second pass: close any stream opened by _attempt_stream_recovery()
        # between the first pass and the VAD thread exit.
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
            with contextlib.suppress(Exception):
                self._stream.close()
            self._stream = None

        self._resampler = None

    def _attempt_stream_recovery(self) -> bool:
        """Close the dead stream and reopen it on the VAD worker thread.

        Returns ``True`` if a new stream is running, ``False`` if recovery
        failed or the session is already being torn down.

        Thread-safety contract: called only from the VAD worker thread.
        Does NOT call ``_teardown()``; that is ``close_session()``'s domain.
        The second-pass stream close in ``_teardown()`` handles any stream
        opened here that the first pass missed.
        """
        with self._state_lock:
            if self._state is not _SessionState.OPEN:
                logger.info(
                    "StreamingRecorder: skipping recovery — state is %s",
                    self._state.value,
                )
                return False

        logger.warning("StreamingRecorder: attempting mid-session stream recovery")

        # Close dead stream without going through _teardown().
        old_stream, self._stream = self._stream, None
        if old_stream is not None:
            with contextlib.suppress(Exception):
                old_stream.stop()
            with contextlib.suppress(Exception):
                old_stream.close()

        # Drain stale audio. Stop immediately on a None sentinel (teardown in
        # progress) — restore it so _vad_loop can exit normally.
        while True:
            try:
                item = self._raw_q.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._raw_q.put(None)
                logger.info("StreamingRecorder: recovery aborted — teardown in progress")
                return False

        # Re-resolve device by name before reopening.
        resolved = _resolve_device_by_name(self._device, self._device_name)
        if resolved != self._device:
            logger.info(
                "StreamingRecorder: recovery resolved index %s → %s",
                self._device, resolved,
            )
            self._device = resolved

        try:
            device_info: Any = sd.query_devices(self._device)
            native_rate = int(device_info["default_samplerate"])
            self._resampler = Resampler(src_rate=native_rate, dst_rate=self._vad_sample_rate)
            self._vad_gate.reset()
            # Replace queue: old stream is stopped so its callback is no longer
            # running; new stream's callback references self._raw_q at call time.
            self._raw_q = queue.Queue(maxsize=64)
            new_stream = sd.InputStream(
                samplerate=native_rate,
                channels=self._channels,
                dtype="float32",
                device=self._device,
                callback=self._on_audio,
            )
            new_stream.start()
            self._stream = new_stream
            logger.info(
                "StreamingRecorder: recovery succeeded (device=%s rate=%d)",
                self._device, native_rate,
            )
            return True
        except Exception:
            logger.exception("StreamingRecorder: recovery failed")
            self._stream = None
            return False

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

        if self._vad_thread is not None and self._vad_thread.is_alive():
            logger.warning("StreamingRecorder: previous VAD worker thread still alive")
            with self._state_lock:
                self._state = _SessionState.IDLE
            raise RuntimeError("Cannot open session: previous VAD worker thread is still alive")

        try:
            # Resolve device by name; updates self._device if the index has shifted.
            resolved = _resolve_device_by_name(self._device, self._device_name)
            if resolved != self._device:
                logger.info(
                    "StreamingRecorder: device index updated %s → %s (name=%r)",
                    self._device, resolved, self._device_name,
                )
                self._device = resolved

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
            logger.exception("StreamingRecorder: failed to open session")
            self._teardown()
            with self._state_lock:
                self._state = _SessionState.IDLE
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

        self._teardown()

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
        consecutive_errors = 0

        while True:
            chunk = self._raw_q.get()
            if chunk is None:
                logger.debug("VAD worker received sentinel; exiting")
                break

            try:
                if self._resampler is None:
                    raise RuntimeError("Resampler not initialised")
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

                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                logger.exception(
                    "StreamingRecorder: VAD worker error (consecutive=%d)",
                    consecutive_errors,
                )
                if consecutive_errors >= 5:
                    buf = np.empty(0, dtype=np.float32)
                    if self._attempt_stream_recovery():
                        # Re-check state: close_session() may have set CLOSING while
                        # we were recovering. The sentinel is on the OLD queue; we
                        # must self-terminate to unblock _teardown()'s join().
                        with self._state_lock:
                            current_state = self._state
                        if current_state is not _SessionState.OPEN:
                            logger.info(
                                "StreamingRecorder: session closed during recovery; exiting"
                            )
                            while True:
                                try:
                                    self._raw_q.get_nowait()
                                except queue.Empty:
                                    break
                            break
                        logger.info("StreamingRecorder: recovery succeeded; resuming")
                        consecutive_errors = 0
                    else:
                        logger.critical(
                            "StreamingRecorder: recovery failed; aborting VAD worker"
                        )
                        break

        logger.debug("VAD worker exited")
