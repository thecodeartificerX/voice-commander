"""Shared pytest fixtures and per-test cleanup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_resampler_cache():
    """Drop the process-wide Resampler cache between tests.

    `streaming_recorder._RESAMPLER_CACHE` (added in the audio-memory pass) is
    keyed by `(src_rate, dst_rate)` and persists across the process lifetime.
    Without per-test cleanup, a test that constructs a real `Resampler` poisons
    every subsequent test that patches `Resampler` with a mock — the cache hit
    returns the stale real instance.
    """
    try:
        from voice_commander import streaming_recorder as _sr
    except Exception:
        yield
        return
    _sr._RESAMPLER_CACHE.clear()
    try:
        yield
    finally:
        _sr._RESAMPLER_CACHE.clear()
