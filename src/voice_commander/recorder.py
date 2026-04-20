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
    """Capture microphone audio to a WAV file.

    The sample rate is not configured directly — it is determined per-stream
    by querying the chosen device's ``default_samplerate`` via
    ``sounddevice.query_devices()`` each time ``start()`` is called.  This
    ensures compatibility with WASAPI exclusive / strict-mode devices that
    only accept their native rate (e.g. 48 kHz).  Phase-2's faster-whisper
    resamples on ingest, so storing a 48 kHz WAV is fine.

    Args:
        output_dir: Directory in which ``recorded.wav`` is written.
        channels: Number of capture channels (default 1 / mono).
        device: PortAudio device index, or ``None`` for the system default.
    """

    def __init__(
        self,
        output_dir: Path,
        channels: int = 1,
        device: int | None = None,
    ) -> None:
        self._out_dir = Path(output_dir)
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._ch = channels
        self._device = device if device is not None and device >= 0 else None
        self._buffer: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: Any | None = None
        self._actual_sample_rate: int | None = None

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def actual_sample_rate(self) -> int | None:
        """The sample rate used for the most recent (or current) recording.

        Returns ``None`` if ``start()`` has never been called.  The value is
        set from the device's ``default_samplerate`` each time ``start()`` is
        called, so it reflects the native rate of whatever device was active.
        """
        return self._actual_sample_rate

    def start(self) -> None:
        if self.is_recording:
            return
        # Query the device's native sample rate so we don't pass an unsupported
        # rate to WASAPI (which only accepts the device's default rate).
        device_info = sd.query_devices(self._device)
        self._actual_sample_rate = int(device_info["default_samplerate"])
        with self._lock:
            self._buffer.clear()
        self._stream = self._open_stream()
        self._stream.start()
        logger.info(
            "Recorder started (sr=%s, ch=%s, device=%s)",
            self._actual_sample_rate,
            self._ch,
            self._device,
        )

    def stop(self) -> Path:
        if not self.is_recording:
            raise RuntimeError("Recorder.stop() called while not recording")
        stream = self._stream
        self._stream = None
        assert stream is not None  # guarded by is_recording check above
        stream.stop()
        stream.close()
        with self._lock:
            frames = (
                np.concatenate(self._buffer, axis=0)
                if self._buffer
                else np.zeros((0, self._ch), dtype=np.float32)
            )
            self._buffer.clear()
        path = self._out_dir / "recorded.wav"
        sf.write(path, frames, self._actual_sample_rate, subtype="PCM_16")
        logger.info("Recorder wrote %s (%d frames)", path, len(frames))
        return path

    def _open_stream(self) -> Any:
        return sd.InputStream(
            samplerate=self._actual_sample_rate,
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
