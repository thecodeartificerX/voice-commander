"""Torch-free Silero VAD wrapper using onnxruntime.

Replaces the ``silero-vad`` PyPI package to eliminate the transitive
torch/torchaudio import (~300–400 MB RSS savings).

The inference logic is derived from ``silero_vad.utils_vad.OnnxWrapper``
(MIT License, Copyright (c) 2021 Silero Team,
https://github.com/snakers4/silero-vad).  Only the ONNX/numpy path is
retained; all torch-specific code has been removed.

Public API mirrors what the daemon and :class:`~voice_commander.vad_gate.VADGate`
need from the original silero model object:

- ``model(audio_chunk)`` — returns a scalar float speech probability
- ``model.reset_states()`` — zeroes the recurrent state tensor

A module-level :func:`load_silero_vad` factory is provided so the daemon's
import line changes minimally (``from voice_commander.vad_onnx import load_silero_vad``).
"""

from __future__ import annotations

import logging
from importlib.resources import files
from pathlib import Path

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

# Default bundled model path (resolved from package resources at import time).
_BUNDLED_MODEL: Path = Path(str(files("voice_commander").joinpath("assets/silero_vad.onnx")))

_SAMPLE_RATE: int = 16_000
_WINDOW_SAMPLES: int = 512  # 32 ms per frame at 16 kHz
_CONTEXT_SIZE: int = 64  # context window prepended to each chunk


class SileroVADOnnx:
    """Thin ONNX-runtime wrapper around the Silero VAD model.

    Provides the same call interface used by the daemon and
    :class:`~voice_commander.vad_gate.VADGate`:

    * ``__call__(audio_chunk)`` — accepts a shape ``(512,)`` float32 ndarray
      at 16 kHz and returns a scalar speech probability in ``[0, 1]``.
    * ``reset_states()`` — zeroes the recurrent GRU state tensor.

    Args:
        model_path: Filesystem path to ``silero_vad.onnx``.  Defaults to the
            bundled copy inside the package ``assets/`` directory.
        force_cpu: When *True*, explicitly request the ONNX CPU execution
            provider even if a GPU provider is available.  Defaults to
            *False* (let onnxruntime choose the best available provider).
    """

    def __init__(
        self,
        model_path: Path | str | None = None,
        *,
        force_cpu: bool = False,
    ) -> None:
        import onnxruntime as ort  # local import — keeps module importable without ort

        resolved_path = Path(model_path) if model_path is not None else _BUNDLED_MODEL
        logger.debug("Loading Silero VAD ONNX model from %s", resolved_path)

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1

        providers: list[str] | None = None
        if force_cpu and "CPUExecutionProvider" in ort.get_available_providers():
            providers = ["CPUExecutionProvider"]

        self._session = ort.InferenceSession(
            str(resolved_path),
            sess_options=opts,
            providers=providers or ort.get_available_providers(),
        )

        self.reset_states()
        logger.debug("Silero VAD ONNX model loaded (providers=%s)", self._session.get_providers())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset_states(self, batch_size: int = 1) -> None:
        """Zero the recurrent GRU state and context tensors.

        Args:
            batch_size: Batch dimension (always 1 in the daemon).
        """
        self._state: npt.NDArray[np.float32] = np.zeros(
            (2, batch_size, 128), dtype=np.float32
        )
        self._context: npt.NDArray[np.float32] = np.zeros(
            (batch_size, _CONTEXT_SIZE), dtype=np.float32
        )
        self._last_batch_size: int = 0

    def __call__(self, audio_chunk: npt.NDArray[np.float32]) -> float:
        """Run VAD inference on a single 512-sample 16 kHz frame.

        Args:
            audio_chunk: Shape ``(512,)`` or ``(1, 512)`` float32 array.

        Returns:
            Speech probability in ``[0, 1]`` as a Python float.

        Raises:
            ValueError: If the chunk length is not 512 samples.
        """
        x = np.asarray(audio_chunk, dtype=np.float32)

        # Normalise to (batch, samples).
        if x.ndim == 1:
            x = x[np.newaxis, :]  # (1, 512)
        if x.ndim != 2:
            raise ValueError(f"Expected 1-D or 2-D audio chunk, got shape {x.shape}")

        batch_size, num_samples = x.shape

        if num_samples != _WINDOW_SAMPLES:
            raise ValueError(
                f"Expected {_WINDOW_SAMPLES} samples per chunk, got {num_samples}"
            )

        # Re-initialise state when batch size changes (mirrors OnnxWrapper logic).
        if self._last_batch_size != batch_size:
            self.reset_states(batch_size)

        # Prepend context window (mirrors OnnxWrapper: x = cat([context, x], dim=1)).
        x_with_ctx = np.concatenate([self._context, x], axis=1)  # (1, 64+512)

        ort_inputs = {
            "input": x_with_ctx,
            "state": self._state,
            "sr": np.array(_SAMPLE_RATE, dtype=np.int64),
        }
        ort_out, new_state = self._session.run(None, ort_inputs)

        # Update recurrent context and state for next frame.
        self._context = x_with_ctx[..., -_CONTEXT_SIZE:]
        self._state = new_state
        self._last_batch_size = batch_size

        # ort_out shape: (1, 1) or (1,) — extract scalar.
        return float(ort_out.squeeze())


# ---------------------------------------------------------------------------
# VADIterator — streaming state-machine around SileroVADOnnx
# ---------------------------------------------------------------------------


class VADIterator:
    """Streaming VAD iterator that wraps a :class:`SileroVADOnnx` model.

    Mirrors the ``silero_vad.VADIterator`` interface used by
    :class:`~voice_commander.vad_gate.VADGate`.  Accepts 512-sample float32
    frames and emits ``{'start': sample}`` / ``{'end': sample}`` events.

    Derived from ``silero_vad.utils_vad.VADIterator``
    (MIT License, Copyright (c) 2021 Silero Team).

    Args:
        model: Loaded VAD model (e.g. :class:`SileroVADOnnx`).
        threshold: Speech probability threshold (0–1).
        sampling_rate: Must be 16000.
        min_silence_duration_ms: Silence duration before speech-end is declared (ms).
        speech_pad_ms: Padding added around each speech segment (ms).
    """

    def __init__(
        self,
        model: SileroVADOnnx,
        threshold: float = 0.5,
        sampling_rate: int = _SAMPLE_RATE,
        min_silence_duration_ms: int = 100,
        speech_pad_ms: int = 30,
    ) -> None:
        if sampling_rate not in (8000, 16000):
            raise ValueError("VADIterator only supports sampling rates 8000 or 16000")

        self.model = model
        self.threshold = threshold
        self.sampling_rate = sampling_rate
        self.min_silence_samples: float = sampling_rate * min_silence_duration_ms / 1000
        self.speech_pad_samples: float = sampling_rate * speech_pad_ms / 1000
        self.reset_states()

    def reset_states(self) -> None:
        """Reset model state and iterator state variables."""
        self.model.reset_states()
        self.triggered: bool = False
        self.temp_end: int = 0
        self.current_sample: int = 0

    def __call__(
        self,
        x: npt.NDArray[np.float32],
        return_seconds: bool = False,
        time_resolution: int = 1,
    ) -> dict[str, int | float] | None:
        """Process a single audio frame and return a speech-event dict or ``None``.

        Args:
            x: Shape ``(512,)`` float32 array at 16 kHz.
            return_seconds: If *True*, timestamps are returned in seconds.
            time_resolution: Decimal places when ``return_seconds=True``.

        Returns:
            ``{'start': t}`` when speech begins, ``{'end': t}`` when speech ends,
            ``None`` otherwise.
        """
        x_arr = np.asarray(x, dtype=np.float32)
        window_size_samples = x_arr.shape[-1] if x_arr.ndim >= 1 else len(x_arr)
        self.current_sample += window_size_samples

        speech_prob: float = self.model(x_arr)

        if speech_prob >= self.threshold and self.temp_end:
            self.temp_end = 0

        if speech_prob >= self.threshold and not self.triggered:
            self.triggered = True
            speech_start = max(
                0, self.current_sample - self.speech_pad_samples - window_size_samples
            )
            ts = (
                round(speech_start / self.sampling_rate, time_resolution)
                if return_seconds
                else int(speech_start)
            )
            return {"start": ts}

        if speech_prob < (self.threshold - 0.15) and self.triggered:
            if not self.temp_end:
                self.temp_end = self.current_sample
            if self.current_sample - self.temp_end < self.min_silence_samples:
                return None
            speech_end = self.temp_end + self.speech_pad_samples - window_size_samples
            self.temp_end = 0
            self.triggered = False
            ts = (
                round(speech_end / self.sampling_rate, time_resolution)
                if return_seconds
                else int(speech_end)
            )
            return {"end": ts}

        return None


# ---------------------------------------------------------------------------
# Module-level factory — matches silero-vad's public API
# ---------------------------------------------------------------------------


def load_silero_vad(  # noqa: D401
    onnx: bool = True,
    *,
    model_path: Path | str | None = None,
    force_cpu: bool = False,
) -> SileroVADOnnx:
    """Return a loaded :class:`SileroVADOnnx` instance.

    Drop-in replacement for ``silero_vad.load_silero_vad(onnx=True)``.
    Torch is eliminated — inference runs entirely through onnxruntime.

    Args:
        onnx: Accepted for API compatibility; always *True* in this wrapper.
        model_path: Path to ``silero_vad.onnx``.  Defaults to the bundled copy.
        force_cpu: Force CPU execution provider.

    Returns:
        Initialised :class:`SileroVADOnnx` ready for inference.
    """
    if not onnx:
        logger.warning(
            "load_silero_vad(onnx=False) requested but this wrapper always uses ONNX; "
            "ignoring onnx=False."
        )
    return SileroVADOnnx(model_path=model_path, force_cpu=force_cpu)
