"""DictationWindow — the growing audio window for streaming-window dictation.

Owns one dictation's growing 16 kHz mono float32 audio buffer. Frames arrive
from the StreamingRecorder frame tap on the VAD worker thread (:meth:`append`);
the asyncio-loop thread reads window WAVs and trims the buffer (:meth:`commit`,
:meth:`flush`). A single lock guards the buffer across those two threads.

Cadence is driven by *accumulated sample count*, not wall-clock: every
``window_step_ms`` of newly appended audio, :meth:`append` returns the WHOLE
current window encoded as a WAV. The window grows until :meth:`commit` trims it
at a committed segment boundary; :attr:`committed_offset_s` tracks how much
audio has been dropped so callers can translate window-relative timestamps to
absolute-stream timestamps (ADR 0095, open item OI-2).

When uncommitted buffered audio exceeds ``window_cap_ms`` the window is bounded
by force-committing: :meth:`uncommitted_seconds` lets the caller detect the cap;
the session force-commits the oldest segment (ADR 0095, open item OI-1).

See ADR 0095 (`docs/decisions/0095-streaming-window-dictation.md`).
"""

from __future__ import annotations

import logging
import threading

import numpy as np
import numpy.typing as npt

from .store import encode_wav

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000


class DictationWindow:
    """Growing 16 kHz audio buffer for one dictation; emits whole-window WAVs."""

    def __init__(self, window_step_ms: int = 1000, window_cap_ms: int = 25000) -> None:
        self._step_samples = max(1, int(_SAMPLE_RATE * window_step_ms / 1000))
        self._cap_samples = max(self._step_samples, int(_SAMPLE_RATE * window_cap_ms / 1000))
        self._lock = threading.Lock()
        # Buffer holds only UNCOMMITTED audio — committed audio is trimmed away.
        self._buffer: list[npt.NDArray[np.float32]] = []
        self._buffer_samples = 0
        # Samples appended since the last emission — drives the step cadence.
        self._since_emit = 0
        # Total audio-seconds dropped by commit() trims. Lets callers map a
        # window-relative timestamp to an absolute-stream timestamp.
        self._committed_offset_s = 0.0

    @property
    def committed_offset_s(self) -> float:
        """Total audio-seconds dropped from the head by :meth:`commit` trims."""
        with self._lock:
            return self._committed_offset_s

    def append(self, frame: npt.NDArray[np.float32]) -> bytes | None:
        """Append one 16 kHz float32 frame; return a window WAV at step cadence.

        Called by the VAD worker thread via the StreamingRecorder frame tap.
        Returns ``bytes`` (the WHOLE current window as a WAV) once
        ``window_step_ms`` of audio has accumulated since the last emission,
        else ``None``. Cheap and thread-safe.
        """
        with self._lock:
            self._buffer.append(np.asarray(frame, dtype=np.float32))
            self._buffer_samples += frame.shape[0]
            self._since_emit += frame.shape[0]
            if self._since_emit < self._step_samples:
                return None
            self._since_emit = 0
            return self._encode_locked()

    def commit(self, committed_end_s: float) -> None:
        """Trim buffer audio up to *committed_end_s* (an absolute-stream time).

        *committed_end_s* is absolute (offset by all prior trims) — the session
        translates window-relative segment end-times via :attr:`committed_offset_s`
        before calling this. A timestamp at or before the current committed
        offset is a no-op (idempotent — guards stale LocalAgreement output).
        """
        with self._lock:
            drop_s = committed_end_s - self._committed_offset_s
            if drop_s <= 0.0:
                return
            drop_samples = min(round(drop_s * _SAMPLE_RATE), self._buffer_samples)
            self._drop_head_locked(drop_samples)
            self._committed_offset_s += drop_samples / _SAMPLE_RATE

    def uncommitted_seconds(self) -> float:
        """Seconds of uncommitted audio currently buffered."""
        with self._lock:
            return self._buffer_samples / _SAMPLE_RATE

    def cap_exceeded(self) -> bool:
        """``True`` when buffered uncommitted audio exceeds ``window_cap_ms``."""
        with self._lock:
            return self._buffer_samples > self._cap_samples

    def flush(self) -> bytes | None:
        """Return the entire remaining window as a WAV, or ``None`` if empty.

        Called once by :meth:`DictationSession.finish` to send the final window.
        """
        with self._lock:
            if self._buffer_samples == 0:
                return None
            return self._encode_locked()

    # --- internal (caller holds self._lock) ---

    def _encode_locked(self) -> bytes:
        audio = np.concatenate(self._buffer) if self._buffer else np.zeros(0, dtype=np.float32)
        return encode_wav(audio)

    def _drop_head_locked(self, n: int) -> None:
        """Drop the first *n* samples from the buffer (caller holds the lock)."""
        if n <= 0:
            return
        remaining = n
        new_buffer: list[npt.NDArray[np.float32]] = []
        for chunk in self._buffer:
            if remaining <= 0:
                new_buffer.append(chunk)
            elif chunk.shape[0] <= remaining:
                remaining -= chunk.shape[0]
            else:
                new_buffer.append(chunk[remaining:])
                remaining = 0
        self._buffer = new_buffer
        self._buffer_samples = sum(c.shape[0] for c in new_buffer)
