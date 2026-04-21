from __future__ import annotations

import numpy as np
import soxr


class Resampler:
    """Streaming audio resampler: converts device-native rate to 16 kHz for silero-vad."""

    def __init__(self, src_rate: int, dst_rate: int = 16000) -> None:
        self._src_rate = src_rate
        self._dst_rate = dst_rate
        self._stream = self._make_stream()

    def _make_stream(self) -> soxr.ResampleStream:
        return soxr.ResampleStream(
            in_rate=self._src_rate,
            out_rate=self._dst_rate,
            num_channels=1,
            dtype=np.float32,
            quality=soxr.HQ,
        )

    def process(self, chunk: np.ndarray) -> np.ndarray:
        """Resample a 1D float32 chunk; returns a 1D float32 array at dst_rate."""
        if chunk.ndim != 1:
            raise ValueError(f"Expected 1-D audio chunk, got shape {chunk.shape}")
        if chunk.dtype != np.float32:
            raise ValueError(f"Expected float32 audio, got {chunk.dtype}")
        return self._stream.resample_chunk(chunk)  # type: ignore[no-any-return]

    def flush(self) -> np.ndarray:
        """Flush the internal soxr filter tail and return any remaining samples.

        Call at the end of a session to drain samples held inside the resampler's
        filter delay.  After flushing, the stream is recreated so the resampler is
        ready for the next session.
        """
        tail: np.ndarray = self._stream.resample_chunk(
            np.array([], dtype=np.float32), last=True
        )
        self._stream = self._make_stream()
        return tail

    def reset(self) -> None:
        """Recreate the internal ResampleStream for a fresh session."""
        self._stream = self._make_stream()
