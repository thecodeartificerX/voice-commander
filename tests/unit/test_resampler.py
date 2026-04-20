from __future__ import annotations

import numpy as np

from voice_commander.resampler import Resampler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sine(freq: float, sample_rate: int, duration_s: float = 1.0) -> np.ndarray:
    """Return a 1D float32 sine wave."""
    t = np.arange(int(sample_rate * duration_s), dtype=np.float32) / sample_rate
    return (np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_48k_to_16k_output_length():
    """1 s of audio at 48 kHz should produce exactly 16 000 samples at 16 kHz.

    soxr's ResampleStream is a streaming resampler; it holds a small internal
    delay.  The final call uses ``last=True`` to flush the delay buffer, so the
    total across all chunks equals the expected sample count.
    """
    src = _sine(440.0, 48000, duration_s=1.0)
    assert src.shape == (48000,)

    resampler = Resampler(48000, 16000)
    # Process in two halves; flush on the final chunk.
    half = len(src) // 2
    out_a = resampler._stream.resample_chunk(src[:half])
    out_b = resampler._stream.resample_chunk(src[half:], last=True)
    total = len(out_a) + len(out_b)

    assert total == 16000, f"Expected exactly 16000 samples total after flush, got {total}"


def test_preserves_frequency_content():
    """A 440 Hz tone survives resampling — FFT peak must be near 440 Hz."""
    src = _sine(440.0, 48000, duration_s=1.0)
    resampler = Resampler(48000, 16000)
    out = resampler.process(src)

    # FFT over the resampled signal
    spectrum = np.abs(np.fft.rfft(out))
    freqs = np.fft.rfftfreq(len(out), d=1.0 / 16000)

    peak_freq = freqs[np.argmax(spectrum)]
    assert abs(peak_freq - 440.0) < 5.0, f"FFT peak at {peak_freq:.1f} Hz, expected ~440 Hz"


def test_reset_clears_state():
    """After reset(), the resampler behaves identically to a fresh instance."""
    src_a = _sine(440.0, 48000, duration_s=0.1)
    src_b = _sine(880.0, 48000, duration_s=0.1)

    # Process chunk A on a reused resampler (then reset).
    resampler = Resampler(48000, 16000)
    resampler.process(src_a)
    resampler.reset()
    out_reused = resampler.process(src_b)

    # Fresh resampler — no prior state.
    fresh = Resampler(48000, 16000)
    out_fresh = fresh.process(src_b)

    # Lengths must match.
    assert len(out_reused) == len(out_fresh)
    # Values must be very close (same state → same output).
    np.testing.assert_allclose(out_reused, out_fresh, atol=1e-5)


def test_identity_when_rates_match():
    """Resampler(16000, 16000) should pass samples through unchanged."""
    src = _sine(440.0, 16000, duration_s=0.5)
    resampler = Resampler(16000, 16000)
    out = resampler.process(src)

    # Length should match (within soxr internal buffering tolerance)
    assert abs(len(out) - len(src)) <= 2

    # Trim to the shorter length and compare values
    n = min(len(out), len(src))
    np.testing.assert_allclose(out[:n], src[:n], atol=1e-5)
