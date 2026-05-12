from __future__ import annotations

import contextlib
import enum
import logging
import queue
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from .resampler import Resampler
from .vad_gate import VADGate

logger = logging.getLogger(__name__)


@dataclass
class SelfTestResult:
    """Result of a short (~200 ms) audio device self-test.

    Attributes:
        ok: ``True`` when the device opened and streamed successfully.
        device_index: Resolved PortAudio device index, or ``None`` for the
            system default.
        host_api: Human-readable host-API name (e.g. ``"Windows WASAPI"``),
            or ``"(default)"`` when *device_index* is ``None``.
        native_rate: Device's native sample-rate in Hz.
        error: Exception message + one-line traceback when *ok* is ``False``,
            ``None`` on success.
    """

    ok: bool
    device_index: int | None
    host_api: str
    native_rate: int
    error: str | None


_VAD_FRAME_SIZE = 512  # samples at 16 kHz fed to VADGate per call

# Process-lifetime resampler cache, keyed by (src_rate, dst_rate).
# Bounded by the number of distinct sample-rate pairs seen (≤ ~5 in practice).
_RESAMPLER_CACHE: dict[tuple[int, int], Resampler] = {}


def _get_resampler(src_rate: int, dst_rate: int) -> Resampler:
    """Return a cached Resampler for (src_rate, dst_rate), resetting its state.

    The cache is process-lifetime; each entry is re-used across sessions by
    calling reset() so soxr's internal filter state is cleared before the
    new session starts feeding audio.
    """
    key = (src_rate, dst_rate)
    if key not in _RESAMPLER_CACHE:
        _RESAMPLER_CACHE[key] = Resampler(src_rate=src_rate, dst_rate=dst_rate)
    instance = _RESAMPLER_CACHE[key]
    instance.reset()
    return instance


def _pop_frame(
    pending: deque[npt.NDArray[np.float32]], n: int
) -> npt.NDArray[np.float32]:
    """Pull exactly *n* samples from the head of *pending* and return them.

    VADGate.process() passes the frame to silero VADIterator (no in-place
    mutation) and may store a reference in its internal ring buffer, so we
    return a *copy* to prevent the ring buffer from aliasing our deque chunks.

    Strategy:
    - If the leading array has >= n samples: slice the first n, replace head
      with the remainder (or pop it fully if exhausted), return a copy.
    - Otherwise: drain enough head arrays into a pre-allocated output buffer,
      push any residual tail of the last partially-consumed array back to front.
    """
    head = pending[0]
    if head.shape[0] >= n:
        frame = head[:n].copy()
        if head.shape[0] == n:
            pending.popleft()
        else:
            pending[0] = head[n:]
        return frame

    # Multi-chunk case: fill a pre-allocated buffer from the deque head.
    out = np.empty(n, dtype=np.float32)
    written = 0
    while written < n:
        chunk = pending.popleft()
        need = n - written
        if chunk.shape[0] <= need:
            out[written : written + chunk.shape[0]] = chunk
            written += chunk.shape[0]
        else:
            # Last chunk: copy what we need, push the residual back to front.
            out[written:] = chunk[:need]
            written = n
            pending.appendleft(chunk[need:])
    return out


_WASAPI_HOST_API_NAME = "Windows WASAPI"


def _find_wasapi_hostapi_index() -> int | None:
    """Return the PortAudio host-API index for Windows WASAPI, or ``None``.

    Windows enumerates the same physical input device once per host API
    (MME, Windows DirectSound, Windows WASAPI, Windows WDM-KS). DirectSound
    in particular is prone to ``PaErrorCode -9999`` host errors when device
    topology shifts (USB device added/removed, sample-rate change). WASAPI
    is the modern Windows native path and is far more reliable, so the
    resolver filters all name matches through it (ADR 0081).
    """
    try:
        for idx, api in enumerate(sd.query_hostapis()):
            if api.get("name", "").strip().lower() == _WASAPI_HOST_API_NAME.lower():
                return idx
    except Exception:
        logger.exception("StreamingRecorder: sd.query_hostapis() failed")
        return None
    return None


def _resolve_device_by_name(saved_index: int | None, device_name: str) -> int | None:
    """Return the current PortAudio index for *device_name* on Windows WASAPI.

    ``device_name`` is the authoritative identity for the configured input
    (ADR 0081). The resolver scans **WASAPI input devices only** — same
    name under DirectSound / MME / WDM-KS is ignored. *saved_index* is a
    non-authoritative cache: used only for the fast path when it still
    points to a WASAPI device whose name matches the target.

    Returns:
        * the WASAPI index for *device_name* (fast or slow path); or
        * ``None`` (PortAudio system default) when no WASAPI match exists,
          WASAPI is unavailable, or *device_name* is empty and no fallback
          index is configured.

    All sd exceptions are caught; returns *saved_index* on unexpected error
    so a transient PortAudio glitch never wipes a working configuration.
    """
    if not device_name:
        return saved_index
    target = device_name.strip().lower()
    try:
        wasapi_idx = _find_wasapi_hostapi_index()
        if wasapi_idx is None:
            logger.warning(
                "StreamingRecorder: Windows WASAPI host API not available; "
                "falling back to system default for '%s'",
                device_name,
            )
            return None

        devices = sd.query_devices()

        def _is_wasapi_input_named(dev: Any, name_target: str) -> bool:
            return (
                dev.get("hostapi") == wasapi_idx
                and dev.get("max_input_channels", 0) > 0
                and dev.get("name", "").strip().lower() == name_target
            )

        # Fast path: saved index still points to the right WASAPI device.
        if saved_index is not None:
            try:
                info = devices[saved_index]
                if _is_wasapi_input_named(info, target):
                    return saved_index
            except (IndexError, KeyError):
                pass

        # Slow path: scan all WASAPI input devices.
        for idx, dev in enumerate(devices):
            if _is_wasapi_input_named(dev, target):
                if idx != saved_index:
                    logger.info(
                        "StreamingRecorder: WASAPI '%s' resolved index %s → %d",
                        device_name, saved_index, idx,
                    )
                return idx

        logger.warning(
            "StreamingRecorder: no WASAPI input device named '%s'; "
            "falling back to system default",
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


def validate_device(
    saved_index: int | None,
    device_name: str,
    channels: int = 1,
) -> SelfTestResult:
    """Open *device_name* for ~200 ms and immediately close it.

    A module-level free function so the admin route can validate a device
    name without constructing a full :class:`StreamingRecorder`.

    Args:
        saved_index: Non-authoritative cached PortAudio index (may be
            ``None``).  Passed straight to :func:`_resolve_device_by_name`.
        device_name: Human-readable WASAPI device name, or ``""`` for the
            system default.
        channels: Number of capture channels (default 1 / mono).

    Returns:
        :class:`SelfTestResult` — ``ok=True`` on success, ``ok=False`` with
        an ``error`` description on any :class:`sd.PortAudioError` or
        unexpected exception.
    """
    resolved = _resolve_device_by_name(saved_index, device_name)

    # Determine host_api name and native_rate from the resolved device.
    host_api = "(default)"
    native_rate = 48000  # safe fallback; overwritten on success
    if resolved is not None:
        try:
            device_info: Any = sd.query_devices(resolved)
            native_rate = int(device_info["default_samplerate"])
            hostapi_index = device_info.get("hostapi")
            if hostapi_index is not None:
                hostapi_info = sd.query_hostapis()[hostapi_index]
                host_api = hostapi_info.get("name", "(unknown)")
        except Exception:
            pass
    else:
        # System default: still try to learn its native rate.
        try:
            device_info = sd.query_devices(None)
            native_rate = int(device_info["default_samplerate"])
        except Exception:
            pass

    stream = None
    try:
        stream = sd.InputStream(
            samplerate=native_rate,
            channels=channels,
            dtype="float32",
            device=resolved,
            callback=lambda *a: None,
        )
        stream.start()
        time.sleep(0.2)
        stream.stop()
        stream.close()
        stream = None
        logger.info(
            "StreamingRecorder: self-test OK (device=%s host_api=%r rate=%d)",
            resolved, host_api, native_rate,
        )
        return SelfTestResult(
            ok=True,
            device_index=resolved,
            host_api=host_api,
            native_rate=native_rate,
            error=None,
        )
    except Exception as exc:
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        error_detail = "".join(tb_lines).strip()
        logger.error(
            "StreamingRecorder: self-test FAILED (device=%s): %s",
            resolved, error_detail,
        )
        # Best-effort cleanup.
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.stop()
            with contextlib.suppress(Exception):
                stream.close()
        return SelfTestResult(
            ok=False,
            device_index=resolved,
            host_api=host_api,
            native_rate=native_rate,
            error=error_detail,
        )


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

    def self_test(self) -> SelfTestResult:
        """Open the configured audio device for ~200 ms and immediately close it.

        Designed to be called **before** the hotkey listener becomes active so
        that a broken device is detected at startup rather than on first use.

        The test opens a throwaway :class:`sd.InputStream` (no-op callback),
        calls ``start()``, sleeps 200 ms, then calls ``stop()`` + ``close()``.
        It does NOT spawn the VAD worker thread or touch any recorder state
        (``self._state``, ``self._stream``, etc.).

        Returns:
            :class:`SelfTestResult` with ``ok=True`` on success or ``ok=False``
            plus an ``error`` description on any :class:`sd.PortAudioError` or
            unexpected exception.
        """
        return validate_device(self._device, self._device_name, self._channels)

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
            # Fetch (or create) a cached Resampler; reset() clears soxr filter state.
            self._resampler = _get_resampler(native_rate, self._vad_sample_rate)
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

    def _open_input_stream(self) -> sd.InputStream:
        """Open and start an InputStream, retrying once with fresh device resolution.

        On the first attempt, uses the current ``self._device`` index.  If
        :class:`sd.PortAudioError` is raised (e.g. another app grabbed the
        device in WASAPI exclusive mode, or a USB topology shift invalidated
        the cached index), the method:

        1. Clears ``self._device`` and re-runs :func:`_resolve_device_by_name`
           for a fresh WASAPI scan.
        2. If the fresh resolution returns the *same* failing index, falls back
           to ``device=None`` (system default) as a last resort.
        3. On second failure: logs ERROR with both errors and re-raises the
           second :class:`sd.PortAudioError`.

        Side effect: sets ``self._native_rate`` so the caller can use it for
        resampler setup.
        """
        first_error: sd.PortAudioError | None = None
        failing_device: int | None = self._device  # stash for retry comparison

        for attempt in range(2):
            if attempt == 0:
                resolved = _resolve_device_by_name(self._device, self._device_name)
                if resolved != self._device:
                    logger.info(
                        "StreamingRecorder: device index updated %s → %s (name=%r)",
                        self._device, resolved, self._device_name,
                    )
                self._device = resolved
            else:
                # Force-clear saved index and re-scan WASAPI from scratch.
                self._device = None
                fresh = _resolve_device_by_name(None, self._device_name)
                if fresh == failing_device:
                    # Same index failed; escalate to system default.
                    logger.warning(
                        "StreamingRecorder: fresh resolve returned same failing index %s; "
                        "falling back to system default",
                        failing_device,
                    )
                    fresh = None
                self._device = fresh

            # Query device info for native rate + host API name.
            device_info: Any = None
            native_rate = 48000
            host_api_name = "(default)"
            device_label = "(default)"
            if self._device is not None:
                try:
                    device_info = sd.query_devices(self._device)
                    native_rate = int(device_info["default_samplerate"])
                    device_label = device_info.get("name", str(self._device))
                    try:
                        host_api_name = sd.query_hostapis()[device_info["hostapi"]]["name"]
                    except Exception:
                        host_api_name = "(unknown)"
                except Exception:
                    logger.warning(
                        "StreamingRecorder: could not query device info for index %s",
                        self._device,
                    )

            try:
                stream = sd.InputStream(
                    samplerate=native_rate,
                    channels=self._channels,
                    dtype="float32",
                    device=self._device,
                    callback=self._on_audio,
                )
                stream.start()
                # Stash native_rate for resampler setup by the caller.
                self._native_rate = native_rate
                if attempt == 0:
                    logger.info(
                        "StreamingRecorder: opening session (device=%s (%r) "
                        "host_api=%r native_rate=%d channels=%d)",
                        self._device, device_label, host_api_name, native_rate, self._channels,
                    )
                else:
                    logger.warning(
                        "StreamingRecorder: opened session on RETRY "
                        "(device=%s (%r) host_api=%r native_rate=%d). "
                        "First attempt failed: %s",
                        self._device, device_label, host_api_name, native_rate, first_error,
                    )
                return stream
            except sd.PortAudioError as exc:
                if attempt == 0:
                    first_error = exc
                    failing_device = self._device
                    logger.warning(
                        "StreamingRecorder: InputStream.start() failed on first attempt "
                        "(device=%s (%r) host_api=%r native_rate=%d): %s. "
                        "Retrying with fresh device resolution...",
                        self._device, device_label, host_api_name, native_rate, exc,
                    )
                    continue
                logger.error(
                    "StreamingRecorder: InputStream.start() failed on retry too "
                    "(device=%s (%r) host_api=%r native_rate=%d). "
                    "First error: %s. Retry error: %s",
                    self._device, device_label, host_api_name, native_rate, first_error, exc,
                )
                raise
        raise RuntimeError("unreachable")  # pragma: no cover

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
            # Open InputStream with retry-once-with-fresh-resolve fallback.
            # _open_input_stream() also sets self._device (resolved) and
            # self._native_rate (device native sample rate).
            self._stream = self._open_input_stream()
            native_rate: int = self._native_rate  # type: ignore[attr-defined]

            # Fetch (or create) a cached Resampler; reset() clears soxr filter state.
            self._resampler = _get_resampler(native_rate, self._vad_sample_rate)

            # Reset VADGate for a clean session.
            self._vad_gate.reset()

            # Fresh queue for this session.
            self._raw_q = queue.Queue(maxsize=64)

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
        pending: deque[npt.NDArray[np.float32]] = deque()
        pending_samples = 0
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
                if resampled.size:
                    pending.append(resampled)
                    pending_samples += resampled.shape[0]

                while pending_samples >= _VAD_FRAME_SIZE:
                    frame: npt.NDArray[np.float32] = _pop_frame(pending, _VAD_FRAME_SIZE)
                    pending_samples -= _VAD_FRAME_SIZE

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
                    pending = deque()
                    pending_samples = 0
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
