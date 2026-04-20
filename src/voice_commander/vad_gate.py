from __future__ import annotations

import collections
import logging
import threading
from enum import Enum, auto
from typing import Any

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

_SAMPLE_RATE: int = 16000
_WINDOW_SAMPLES: int = 512  # 32 ms per frame at 16 kHz


class _State(Enum):
    IDLE = auto()
    ACTIVE = auto()


class VADGate:
    """Wrap silero-vad's VADIterator with pre-roll buffering and utterance accumulation.

    Fed 512-sample 16 kHz float32 frames via :meth:`process`.  Returns a
    completed utterance ndarray (pre-roll + speech) when speech ends, or
    ``None`` while accumulating.

    Args:
        model: Loaded silero VAD model (``torch.nn.Module``; typed as ``Any``
            because silero doesn't export typed model classes).
        threshold: VAD speech-probability threshold (0–1).
        min_speech_ms: Minimum speech duration in milliseconds. Utterances
            whose speech portion (excluding pre-roll) is shorter than this
            are discarded as false wakes. Enforced in :meth:`process` because
            silero's ``VADIterator`` does not accept this parameter directly
            (only the higher-level ``get_speech_timestamps`` helper does).
        min_silence_ms: Minimum silence after speech (ms) before speech-end
            is declared.
        speech_pad_ms: Padding added around each speech segment (ms).
        pre_roll_ms: How many milliseconds of audio to keep in the ring buffer
            and prepend to the utterance on speech-start.
        max_utterance_ms: Hard cap on utterance length (ms).  If the active
            buffer exceeds this, the utterance is force-ended and returned.
    """

    def __init__(
        self,
        model: Any,
        threshold: float = 0.5,
        min_speech_ms: int = 250,
        min_silence_ms: int = 100,
        speech_pad_ms: int = 30,
        pre_roll_ms: int = 320,
        max_utterance_ms: int = 30_000,
    ) -> None:
        from silero_vad import (
            VADIterator,  # local import — silero may not be present at import time
        )

        self._vad: Any = VADIterator(
            model,
            sampling_rate=_SAMPLE_RATE,
            threshold=threshold,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
        )

        # Pre-roll ring buffer: keeps the last `pre_roll_ms` worth of frames.
        # 32 ms per frame → frames = ceil(pre_roll_ms / 32).
        pre_roll_frames: int = max(1, (pre_roll_ms + 31) // 32)
        self._ring: collections.deque[npt.NDArray[np.float32]] = collections.deque(
            maxlen=pre_roll_frames
        )

        self._max_utterance_samples: int = int(_SAMPLE_RATE * max_utterance_ms / 1000)
        self._min_speech_samples: int = int(_SAMPLE_RATE * min_speech_ms / 1000)

        self._state: _State = _State.IDLE
        self._active: list[npt.NDArray[np.float32]] = []
        self._active_samples: int = 0
        self._speech_samples: int = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, frame_16k: npt.NDArray[np.float32]) -> npt.NDArray[np.float32] | None:
        """Feed a 512-sample 16 kHz float32 frame and return a completed utterance or ``None``.

        Args:
            frame_16k: Shape ``(512,)`` float32 array at 16 kHz.

        Returns:
            Concatenated ndarray of pre-roll + speech when a speech-end event
            is detected (or the max-utterance guard fires), else ``None``.
        """
        with self._lock:
            result: dict[str, Any] | None = self._vad(frame_16k)

            if self._state is _State.IDLE:
                self._ring.append(frame_16k)

                if result is not None and "start" in result:
                    logger.debug("VAD speech-start detected (t=%s)", result["start"])
                    # Snapshot ring buffer as pre-roll (oldest → newest order is preserved
                    # because deque iteration is FIFO).
                    pre_roll_frames: list[npt.NDArray[np.float32]] = list(self._ring)
                    self._active = pre_roll_frames
                    self._active_samples = sum(f.shape[0] for f in pre_roll_frames)
                    # Clear ring so back-to-back utterances get a fresh pre-roll.
                    self._ring.clear()
                    self._state = _State.ACTIVE

            elif self._state is _State.ACTIVE:
                self._active.append(frame_16k)
                self._active_samples += frame_16k.shape[0]
                self._speech_samples += frame_16k.shape[0]

                force_end = self._active_samples >= self._max_utterance_samples
                speech_end = result is not None and "end" in result

                if speech_end or force_end:
                    if force_end:
                        logger.debug(
                            "VAD max-utterance guard fired (%d samples)",
                            self._active_samples,
                        )
                    else:
                        logger.debug("VAD speech-end detected (t=%s)", result["end"])  # type: ignore[index]

                    too_short = (
                        not force_end
                        and self._speech_samples < self._min_speech_samples
                    )

                    if too_short:
                        logger.debug(
                            "VAD utterance discarded: speech %d samples < min %d",
                            self._speech_samples,
                            self._min_speech_samples,
                        )
                        self._state = _State.IDLE
                        self._active = []
                        self._active_samples = 0
                        self._speech_samples = 0
                        self._ring.clear()
                        return None

                    utterance: npt.NDArray[np.float32] = np.concatenate(self._active, axis=0)
                    self._state = _State.IDLE
                    self._active = []
                    self._active_samples = 0
                    self._speech_samples = 0
                    self._ring.clear()
                    return utterance

            return None

    def reset(self) -> None:
        """Reset VAD internal states, ring buffer, and active utterance buffer.

        Call at session open to ensure a clean slate.
        """
        with self._lock:
            self._vad.reset_states()
            self._ring.clear()
            self._active = []
            self._active_samples = 0
            self._speech_samples = 0
            self._state = _State.IDLE
            logger.debug("VADGate reset")
