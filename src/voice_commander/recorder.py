from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)


class Recorder:
    def __init__(
        self,
        output_dir: Path,
        sample_rate: int = 16000,
        channels: int = 1,
        device: int | None = None,
    ) -> None:
        self._out_dir = Path(output_dir)
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._sr = sample_rate
        self._ch = channels
        self._device = device if device is not None and device >= 0 else None
        self._buffer: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: Any | None = None

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self.is_recording:
            return
        with self._lock:
            self._buffer.clear()
        self._stream = self._open_stream()
        self._stream.start()
        logger.info("Recorder started (sr=%s, ch=%s, device=%s)", self._sr, self._ch, self._device)

    def stop(self) -> Path:
        if not self.is_recording:
            raise RuntimeError("Recorder.stop() called while not recording")
        stream = self._stream
        self._stream = None
        stream.stop()
        stream.close()
        with self._lock:
            frames = (
                np.concatenate(self._buffer, axis=0) if self._buffer
                else np.zeros((0, self._ch), dtype=np.float32)
            )
            self._buffer.clear()
        path = self._out_dir / "recorded.wav"
        sf.write(path, frames, self._sr, subtype="PCM_16")
        logger.info("Recorder wrote %s (%d frames)", path, len(frames))
        return path

    def _open_stream(self) -> Any:
        return sd.InputStream(
            samplerate=self._sr,
            channels=self._ch,
            dtype="float32",
            device=self._device,
            callback=self._on_audio,
        )

    def _on_audio(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if status:
            logger.warning("sounddevice status: %s", status)
        with self._lock:
            self._buffer.append(indata.copy())
