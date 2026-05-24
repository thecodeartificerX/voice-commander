"""Unit tests for voice_commander.dictation.timing (ADR 0101).

The module is pure (no I/O), so tests are straightforward math assertions.
"""

from __future__ import annotations

import pytest

from voice_commander.dictation.timing import build_timing_record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVER_TIMINGS_FULL = {
    "transcribe_ms": 800.0,
    "clean_ms": 120.0,
    "format_ms": 30.0,
    "server_total_ms": 950.0,
}


# ---------------------------------------------------------------------------
# Test 1: all fields present — network_ms, total_ms, chars, ts
# ---------------------------------------------------------------------------


def test_full_timings_network_ms_correct() -> None:
    """With server_timings and roundtrip_ms both present, network_ms = rt - server_total."""
    record = build_timing_record(
        text="hello world",
        server_timings=_SERVER_TIMINGS_FULL,
        roundtrip_ms=1050.0,
        postprocess_ms=5.0,
        paste_ms=10.0,
    )

    # network_ms = 1050 - 950 = 100
    assert record["network_ms"] == pytest.approx(100.0)


def test_full_timings_total_ms_correct() -> None:
    """total_ms = roundtrip_ms + postprocess_ms + paste_ms."""
    record = build_timing_record(
        text="hello world",
        server_timings=_SERVER_TIMINGS_FULL,
        roundtrip_ms=1050.0,
        postprocess_ms=5.0,
        paste_ms=10.0,
    )

    # total_ms = 1050 + 5 + 10 = 1065
    assert record["total_ms"] == pytest.approx(1065.0)


def test_full_timings_chars_correct() -> None:
    """chars = len(text)."""
    text = "hello world"
    record = build_timing_record(
        text=text,
        server_timings=_SERVER_TIMINGS_FULL,
        roundtrip_ms=1050.0,
        postprocess_ms=5.0,
        paste_ms=10.0,
    )
    assert record["chars"] == len(text)


def test_full_timings_ts_present() -> None:
    """ts is a float (unix timestamp)."""
    import time

    before = time.time()
    record = build_timing_record(
        text="hi",
        server_timings=_SERVER_TIMINGS_FULL,
        roundtrip_ms=1000.0,
        postprocess_ms=1.0,
        paste_ms=1.0,
    )
    after = time.time()

    assert isinstance(record["ts"], float)
    assert before <= record["ts"] <= after


def test_full_timings_server_dict_stored() -> None:
    """The server dict is stored verbatim under the 'server' key."""
    record = build_timing_record(
        text="ok",
        server_timings=_SERVER_TIMINGS_FULL,
        roundtrip_ms=1000.0,
        postprocess_ms=2.0,
        paste_ms=3.0,
    )
    assert record["server"] == _SERVER_TIMINGS_FULL


# ---------------------------------------------------------------------------
# Test 2: empty server_timings → network_ms is None
# ---------------------------------------------------------------------------


def test_empty_server_timings_network_ms_none() -> None:
    """When server_timings={}, network_ms must be None (no server_total_ms)."""
    record = build_timing_record(
        text="some text",
        server_timings={},
        roundtrip_ms=500.0,
        postprocess_ms=3.0,
        paste_ms=7.0,
    )
    assert record["network_ms"] is None


def test_empty_server_timings_total_ms_uses_roundtrip() -> None:
    """With empty server_timings, total_ms still uses roundtrip_ms."""
    record = build_timing_record(
        text="some text",
        server_timings={},
        roundtrip_ms=500.0,
        postprocess_ms=3.0,
        paste_ms=7.0,
    )
    # total_ms = 500 + 3 + 7 = 510
    assert record["total_ms"] == pytest.approx(510.0)


# ---------------------------------------------------------------------------
# Test 3: roundtrip_ms=None → network_ms is None, total_ms excludes it
# ---------------------------------------------------------------------------


def test_roundtrip_ms_none_network_ms_none() -> None:
    """When roundtrip_ms is None (no done frame), network_ms must be None."""
    record = build_timing_record(
        text="hi",
        server_timings=_SERVER_TIMINGS_FULL,
        roundtrip_ms=None,
        postprocess_ms=4.0,
        paste_ms=6.0,
    )
    assert record["network_ms"] is None


def test_roundtrip_ms_none_total_ms_excludes_roundtrip() -> None:
    """When roundtrip_ms is None, total_ms = postprocess_ms + paste_ms (roundtrip treated as 0)."""
    record = build_timing_record(
        text="hi",
        server_timings=_SERVER_TIMINGS_FULL,
        roundtrip_ms=None,
        postprocess_ms=4.0,
        paste_ms=6.0,
    )
    # total_ms = 0 + 4 + 6 = 10
    assert record["total_ms"] == pytest.approx(10.0)


def test_roundtrip_ms_none_stored_as_none() -> None:
    """roundtrip_ms=None is preserved in the record."""
    record = build_timing_record(
        text="hi",
        server_timings={},
        roundtrip_ms=None,
        postprocess_ms=1.0,
        paste_ms=1.0,
    )
    assert record["roundtrip_ms"] is None


# ---------------------------------------------------------------------------
# Test 4: schema completeness — all locked keys are present
# ---------------------------------------------------------------------------


def test_record_has_all_locked_keys() -> None:
    """The record must contain exactly the locked schema keys (ADR 0101)."""
    required_keys = {
        "ts",
        "chars",
        "server",
        "roundtrip_ms",
        "network_ms",
        "postprocess_ms",
        "paste_ms",
        "total_ms",
    }
    record = build_timing_record(
        text="any",
        server_timings={},
        roundtrip_ms=100.0,
        postprocess_ms=1.0,
        paste_ms=2.0,
    )
    assert required_keys.issubset(record.keys()), (
        f"Missing keys: {required_keys - record.keys()}"
    )


# ---------------------------------------------------------------------------
# Test 5: server_timings partially populated (no server_total_ms) → network_ms None
# ---------------------------------------------------------------------------


def test_server_timings_without_total_network_ms_none() -> None:
    """If server_timings has transcribe_ms etc. but no server_total_ms, network_ms is None."""
    partial = {"transcribe_ms": 700.0, "clean_ms": 100.0}
    record = build_timing_record(
        text="test",
        server_timings=partial,
        roundtrip_ms=900.0,
        postprocess_ms=2.0,
        paste_ms=3.0,
    )
    assert record["network_ms"] is None


# ---------------------------------------------------------------------------
# Test 6: zero-length text
# ---------------------------------------------------------------------------


def test_zero_length_text_chars_zero() -> None:
    """Empty transcript produces chars=0."""
    record = build_timing_record(
        text="",
        server_timings={},
        roundtrip_ms=200.0,
        postprocess_ms=1.0,
        paste_ms=1.0,
    )
    assert record["chars"] == 0
