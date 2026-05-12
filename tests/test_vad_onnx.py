"""Smoke tests for the in-house SileroVADOnnx wrapper (voice_commander.vad_onnx).

Validates that:
- The wrapper instantiates without errors.
- 512 samples of silence produce a speech probability < 0.5.
- 512 samples of white noise return a float in [0, 1].
- reset_states() resets internal GRU state without raising.
"""

from __future__ import annotations

import numpy as np
import pytest

from voice_commander.vad_onnx import SileroVADOnnx, load_silero_vad


@pytest.fixture(scope="module")
def vad_model() -> SileroVADOnnx:
    """Load the bundled ONNX VAD model once for the entire module."""
    return SileroVADOnnx()


def test_silence_returns_low_probability(vad_model: SileroVADOnnx) -> None:
    """512 zero-valued samples should produce a speech probability below 0.5."""
    vad_model.reset_states()
    silence = np.zeros(512, dtype=np.float32)
    prob = vad_model(silence)
    assert isinstance(prob, float), f"Expected float, got {type(prob)}"
    assert 0.0 <= prob <= 1.0, f"Probability out of range: {prob}"
    assert prob < 0.5, f"Silence should not be classified as speech (prob={prob:.4f})"


def test_white_noise_returns_valid_probability(vad_model: SileroVADOnnx) -> None:
    """512 samples of white noise should return a float in [0, 1]."""
    vad_model.reset_states()
    rng = np.random.default_rng(seed=42)
    noise = rng.uniform(-0.1, 0.1, size=512).astype(np.float32)
    prob = vad_model(noise)
    assert isinstance(prob, float), f"Expected float, got {type(prob)}"
    assert 0.0 <= prob <= 1.0, f"Probability out of range: {prob}"


def test_reset_states_clears_context(vad_model: SileroVADOnnx) -> None:
    """reset_states() should not raise and should zero the state tensor."""
    # Feed a few frames to populate state.
    silence = np.zeros(512, dtype=np.float32)
    for _ in range(5):
        vad_model(silence)

    vad_model.reset_states()

    # After reset, state tensor should be all zeros.
    assert np.all(vad_model._state == 0.0), "State tensor should be zeroed after reset_states()"
    assert np.all(vad_model._context == 0.0), "Context tensor should be zeroed after reset_states()"


def test_load_silero_vad_factory() -> None:
    """load_silero_vad() factory returns a SileroVADOnnx and is callable."""
    model = load_silero_vad(onnx=True)
    assert isinstance(model, SileroVADOnnx)
    silence = np.zeros(512, dtype=np.float32)
    prob = model(silence)
    assert 0.0 <= prob <= 1.0


def test_wrong_chunk_size_raises(vad_model: SileroVADOnnx) -> None:
    """Passing a chunk that is not 512 samples should raise ValueError."""
    vad_model.reset_states()
    bad_chunk = np.zeros(256, dtype=np.float32)  # wrong size
    with pytest.raises(ValueError, match="512"):
        vad_model(bad_chunk)


def test_consecutive_calls_advance_state(vad_model: SileroVADOnnx) -> None:
    """Two consecutive __call__ invocations on different audio should update _state.

    The GRU recurrent state must differ between the first and second call,
    confirming that the model is actually advancing its internal state rather
    than returning the same tensor each time.
    """
    vad_model.reset_states()
    rng = np.random.default_rng(seed=7)

    chunk_a = rng.uniform(-0.1, 0.1, size=512).astype(np.float32)
    vad_model(chunk_a)
    state_after_first = vad_model._state.copy()

    chunk_b = rng.uniform(-0.2, 0.2, size=512).astype(np.float32)
    vad_model(chunk_b)
    state_after_second = vad_model._state.copy()

    assert not np.array_equal(state_after_first, state_after_second), (
        "Internal _state did not change between two consecutive calls with different audio"
    )


def test_int16_input_coerced_to_float32_matches_equivalent_float(
    vad_model: SileroVADOnnx,
) -> None:
    """int16 audio coerced via np.asarray(dtype=float32) must give the same result as
    passing the equivalent float32 values directly.

    The silero reference (OnnxWrapper) performs no int16→[-1,1] normalization —
    it casts raw int16 values directly to float (via torch.Tensor or np.asarray).
    Our wrapper must match that behaviour: int16 values are widened to their
    equivalent float32 magnitude, not divided by 32768.
    """
    vad_model.reset_states()
    rng = np.random.default_rng(seed=99)

    # Build a small-magnitude int16 chunk so values stay in a realistic range.
    int16_chunk = rng.integers(-100, 100, size=512, dtype=np.int16)

    # Equivalent float32 chunk produced by the same widening cast.
    float32_chunk = int16_chunk.astype(np.float32)

    vad_model.reset_states()
    prob_from_int16 = vad_model(int16_chunk)  # wrapper must coerce internally

    vad_model.reset_states()
    prob_from_float32 = vad_model(float32_chunk)

    assert prob_from_int16 == prob_from_float32, (
        f"int16 coercion produced a different result ({prob_from_int16}) "
        f"than direct float32 input ({prob_from_float32})"
    )
