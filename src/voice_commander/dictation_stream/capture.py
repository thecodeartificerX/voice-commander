"""Microphone capture for standalone streaming dictation.

Opens a ``sounddevice`` input stream at the device-native rate and pushes raw
float32 blocks onto a queue. Mirrors the daemon's capture approach — native
rate in, resample downstream — because WASAPI shared mode locks the device to
its native rate (usually 48 kHz); a direct 16 kHz stream is unreliable.
"""

from __future__ import annotations

import logging
import queue
from typing import Any

import numpy.typing as npt
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class MicCapture:
    """Open the default input device; stream raw blocks onto ``raw_q``."""

    def __init__(self, raw_q: "queue.Queue[npt.NDArray[np.float32] | None]") -> None:
        self._raw_q = raw_q
        self._stream: Any = None
        self.native_rate: int = 0

    def start(self) -> None:
        """Open and start the input stream. Raises on device failure."""
        device_info = sd.query_devices(kind="input")
        self.native_rate = int(device_info["default_samplerate"])
        self._stream = sd.InputStream(
            samplerate=self.native_rate,
            channels=1,
            dtype="float32",
            callback=self._on_audio,
        )
        self._stream.start()
        logger.info("MicCapture: input stream open at %d Hz", self.native_rate)

    def stop(self) -> None:
        """Close the stream and push the ``None`` end-of-audio sentinel."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._raw_q.put(None)

    def _on_audio(
        self,
        indata: npt.NDArray[np.float32],
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """PortAudio callback — copy the block onto the queue, never block."""
        try:
            self._raw_q.put_nowait(indata.copy())
        except queue.Full:
            logger.warning("MicCapture: raw_q full — dropping audio block")
