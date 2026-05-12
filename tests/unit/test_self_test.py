"""Unit tests for StreamingRecorder.self_test() and related startup wiring.

All sounddevice access is monkeypatched so no real audio hardware is required.
"""
from __future__ import annotations

import sys
import threading
import types
from unittest.mock import MagicMock, patch

import pytest

import sounddevice as _sd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_sd_full(monkeypatch, *, native_rate: float = 48000.0, hostapi_name: str = "Windows WASAPI"):
    """Patch sd.query_devices, sd.query_hostapis, and sd.InputStream."""

    wasapi_hostapi = {"name": hostapi_name}

    def _query_devices(device=None):
        return {"default_samplerate": native_rate, "hostapi": 0}

    monkeypatch.setattr("voice_commander.streaming_recorder.sd.query_devices", _query_devices)
    monkeypatch.setattr(
        "voice_commander.streaming_recorder.sd.query_hostapis",
        lambda: [wasapi_hostapi],
    )

    instances: list[MagicMock] = []

    class FakeInputStream:
        def __init__(self, **kwargs):
            self.started = False
            self._closed = False
            instances.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

        def close(self):
            self._closed = True

    monkeypatch.setattr("voice_commander.streaming_recorder.sd.InputStream", FakeInputStream)
    # Patch time.sleep so tests don't actually wait 200 ms.
    monkeypatch.setattr("voice_commander.streaming_recorder.time.sleep", lambda _: None)

    return instances


def _make_recorder(monkeypatch, *, device_name: str = "TestMic"):
    from voice_commander.streaming_recorder import StreamingRecorder
    from voice_commander.vad_gate import VADGate

    class _FakeVADGate:
        def reset(self) -> None: ...
        def process(self, frame): return None

    return StreamingRecorder(
        device=None,
        channels=1,
        vad_gate=_FakeVADGate(),
        utterance_sink=lambda x: None,
        device_name=device_name,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_self_test_success_with_mock_device(monkeypatch):
    """self_test() returns ok=True with populated device_index and host_api."""
    instances = _fake_sd_full(monkeypatch, native_rate=48000.0, hostapi_name="Windows WASAPI")
    recorder = _make_recorder(monkeypatch, device_name="")

    # Patch resolver to return index 3 (non-None device).
    monkeypatch.setattr(
        "voice_commander.streaming_recorder._resolve_device_by_name",
        lambda saved, name: 3,
    )

    result = recorder.self_test()

    assert result.ok is True
    assert result.device_index == 3
    assert result.host_api == "Windows WASAPI"
    assert result.native_rate == 48000
    assert result.error is None
    # One InputStream was created and start/stop/close were called.
    assert len(instances) == 1
    stream = instances[0]
    assert stream._closed is True


def test_self_test_failure_propagates_porterror(monkeypatch):
    """self_test() returns ok=False when InputStream.start() raises PortAudioError."""
    import sounddevice as sd

    _fake_sd_full(monkeypatch, native_rate=44100.0)

    class _BoomInputStream:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise sd.PortAudioError("boom")

        def stop(self): ...
        def close(self): ...

    monkeypatch.setattr("voice_commander.streaming_recorder.sd.InputStream", _BoomInputStream)
    monkeypatch.setattr(
        "voice_commander.streaming_recorder._resolve_device_by_name",
        lambda saved, name: 5,
    )

    recorder = _make_recorder(monkeypatch)
    result = recorder.self_test()

    assert result.ok is False
    assert result.error is not None
    assert "boom" in result.error


def test_self_test_does_not_modify_recorder_state(monkeypatch):
    """self_test() must not mutate _state, _stream, or spawn a VAD thread."""
    from voice_commander.streaming_recorder import _SessionState

    _fake_sd_full(monkeypatch)
    monkeypatch.setattr(
        "voice_commander.streaming_recorder._resolve_device_by_name",
        lambda saved, name: None,
    )

    recorder = _make_recorder(monkeypatch)

    threads_before = threading.active_count()
    result = recorder.self_test()
    threads_after = threading.active_count()

    assert result.ok is True
    # State must still be IDLE.
    assert recorder._state is _SessionState.IDLE
    # Stream handle must still be None.
    assert recorder._stream is None
    # No extra threads should have been spawned.
    assert threads_after == threads_before


def test_self_test_resolves_device_by_name(monkeypatch):
    """self_test() calls _resolve_device_by_name with the recorder's saved index + name."""
    _fake_sd_full(monkeypatch)

    resolve_calls: list[tuple] = []

    def _fake_resolve(saved_index, device_name):
        resolve_calls.append((saved_index, device_name))
        return None  # system default

    monkeypatch.setattr(
        "voice_commander.streaming_recorder._resolve_device_by_name",
        _fake_resolve,
    )

    recorder = _make_recorder(monkeypatch, device_name="AT2020USB")
    recorder.self_test()

    assert len(resolve_calls) == 1
    saved_idx, name = resolve_calls[0]
    assert name == "AT2020USB"


# ---------------------------------------------------------------------------
# validate_device free-function tests
# ---------------------------------------------------------------------------


def test_validate_device_signature_matches_method(monkeypatch):
    """validate_device(None, '') returns a SelfTestResult (not a method error)."""
    from voice_commander.streaming_recorder import SelfTestResult, validate_device

    _fake_sd_full(monkeypatch, native_rate=48000.0)
    monkeypatch.setattr(
        "voice_commander.streaming_recorder._resolve_device_by_name",
        lambda saved, name: None,
    )

    result = validate_device(None, "")

    assert isinstance(result, SelfTestResult)
    assert result.ok is True


def test_validate_device_called_by_self_test(monkeypatch):
    """StreamingRecorder.self_test() delegates to validate_device."""
    from voice_commander.streaming_recorder import SelfTestResult

    sentinel = SelfTestResult(
        ok=True, device_index=99, host_api="stub", native_rate=16000, error=None
    )
    calls: list[tuple] = []

    def _fake_validate(saved_index, device_name, channels=1):
        calls.append((saved_index, device_name, channels))
        return sentinel

    monkeypatch.setattr(
        "voice_commander.streaming_recorder.validate_device",
        _fake_validate,
    )

    recorder = _make_recorder(monkeypatch, device_name="MyMic")
    result = recorder.self_test()

    assert result is sentinel
    assert len(calls) == 1
    _, name, ch = calls[0]
    assert name == "MyMic"
    assert ch == 1


# ---------------------------------------------------------------------------
# Banner + exit-73 tests
# ---------------------------------------------------------------------------


def test_self_test_failure_triggers_exit_73(monkeypatch, tmp_path):
    """build_streaming_daemon exits with code 73 when self_test() returns ok=False."""
    from voice_commander.config import Config
    from voice_commander.daemon import build_streaming_daemon
    from voice_commander.streaming_recorder import SelfTestResult

    monkeypatch.chdir(tmp_path)
    cfg = Config()

    fake_torch = types.SimpleNamespace(set_num_threads=lambda n: None)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    class _FakeRegistry:
        def all(self): return []
        def by_name(self, name): return None
        def register(self, entry): pass
        def all_enabled(self): return []
        def by_origin(self, origin): return []

    failing_result = SelfTestResult(
        ok=False, device_index=None, host_api="(default)", native_rate=48000,
        error="PaErrorCode -9999\ndevice unavailable"
    )

    fake_recorder = MagicMock()
    fake_recorder.self_test.return_value = failing_result

    def _mock_recorder_cls(*args, **kwargs):
        return fake_recorder

    with (
        patch("voice_commander.daemon.load_silero_vad", return_value=object()),
        patch("voice_commander.daemon.Transcriber"),
        patch("voice_commander.daemon.StreamingRecorder", side_effect=_mock_recorder_cls),
        patch("voice_commander.daemon.WindowsFeedbackSink"),
        patch("voice_commander.daemon.create_app"),
        patch("voice_commander.daemon.discover", return_value=_FakeRegistry()),
        patch("voice_commander.daemon.validate_config_or_die"),
        patch("voice_commander.daemon.validate_or_die"),
        patch("voice_commander.daemon.ToolMetadataStore"),
        patch("voice_commander.daemon.VADGate"),
        patch("voice_commander.commands.registrar.reload_all", return_value=([], [])),
        patch("voice_commander.commands.GraphStore"),
        patch("voice_commander.commands.seed_if_missing"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            build_streaming_daemon(cfg)

    assert exc_info.value.code == 73


def test_provenance_banner_two_lines(monkeypatch, tmp_path):
    """_provenance_banner returns exactly 2 strings matching the expected format."""
    import re

    from voice_commander.config import Config
    from voice_commander.daemon import _provenance_banner
    from voice_commander.streaming_recorder import SelfTestResult

    monkeypatch.chdir(tmp_path)

    # Write a dummy config.toml so the hash path succeeds.
    (tmp_path / "config.toml").write_bytes(b"[audio]\n")

    cfg = Config()
    result = SelfTestResult(
        ok=True, device_index=7, host_api="Windows WASAPI", native_rate=48000, error=None
    )

    lines = _provenance_banner(cfg, result)

    assert len(lines) == 2

    # Line 1: === voice_commander | pid=... | git=... | config=... ===
    assert re.match(
        r"=== voice_commander \| pid=\d+ \| git=\w+ \| config=\w+ ===",
        lines[0],
    ), f"Line 1 did not match: {lines[0]!r}"

    # Line 2: === audio: name='...' -> ...[...] @ ...Hz | OK ===
    assert re.match(
        r"=== audio: name='.*' -> .+ @ \d+Hz \| .+ ===",
        lines[1],
    ), f"Line 2 did not match: {lines[1]!r}"

    # Status should be OK for a successful result.
    assert "OK" in lines[1]
    # Rate should appear.
    assert "48000Hz" in lines[1]
