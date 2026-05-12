from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_build_streaming_daemon_constructs_tracer(monkeypatch, tmp_path):
    """build_streaming_daemon should attach a Tracer when observability.enabled."""
    from voice_commander.config import Config
    from voice_commander.daemon import build_streaming_daemon

    monkeypatch.chdir(tmp_path)
    cfg = Config()  # defaults; observability.enabled=True

    # Stub out heavy deps that the factory normally imports.
    fake_torch = types.SimpleNamespace(set_num_threads=lambda n: None)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    class _FakeRegistry:
        def all(self):
            return []

        def by_name(self, name):
            return None

        def register(self, entry):
            pass

        def all_enabled(self):
            return []

        def by_origin(self, origin):
            return []

    fake_registry = _FakeRegistry()

    with (
        patch("voice_commander.daemon.load_silero_vad", return_value=object()),
        patch("voice_commander.daemon.Transcriber"),
        patch("voice_commander.daemon.StreamingRecorder"),
        patch("voice_commander.daemon.WindowsFeedbackSink"),
        patch("voice_commander.daemon.create_app"),
        patch("voice_commander.daemon.discover", return_value=fake_registry),
        patch("voice_commander.daemon.validate_config_or_die"),
        patch("voice_commander.daemon.validate_or_die"),
        patch("voice_commander.daemon.ToolMetadataStore"),
        patch("voice_commander.daemon.VADGate"),
        patch("voice_commander.commands.registrar.reload_all", return_value=([], [])),
        patch("voice_commander.commands.GraphStore"),
        patch("voice_commander.commands.seed_if_missing"),
    ):
        daemon = build_streaming_daemon(cfg)

    assert daemon._tracer is not None
    assert daemon._tracer.enabled is True


# ---------------------------------------------------------------------------
# Config hot-reload unit tests
# ---------------------------------------------------------------------------

def _make_daemon(tmp_path: Path, device_name: str = "Microphone A") -> "StreamingDaemon":
    """Construct a minimal StreamingDaemon with a mock recorder for unit tests."""
    from voice_commander.config import Config
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.event_bus import EventBus
    from voice_commander.feedback import CapturingFeedbackSink

    recorder = MagicMock()
    recorder._device_name = device_name

    cfg = Config()
    # Patch audio.device_name onto the frozen dataclass via object.__setattr__
    import dataclasses
    audio_cfg = dataclasses.replace(cfg.audio, device_name=device_name)
    cfg = dataclasses.replace(cfg, audio=audio_cfg)

    bus = EventBus()

    daemon = StreamingDaemon(
        feedback=CapturingFeedbackSink(),
        recorder=recorder,
        transcriber=MagicMock(),
        dispatcher=MagicMock(),
        verb_router=MagicMock(),
        registry=None,
        output_dir=str(tmp_path / "out"),
        event_bus=bus,
    )
    daemon._cfg = cfg
    daemon._recorder = recorder
    return daemon


def test_on_config_changed_updates_recorder_device_name(tmp_path: Path) -> None:
    """_on_config_changed should update recorder._device_name when device_name changes."""
    daemon = _make_daemon(tmp_path, device_name="Microphone A")

    # Write a config.toml with a different device_name.
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[audio]\ndevice_name = "Microphone B"\n', encoding="utf-8")

    daemon._on_config_changed(cfg_file)

    assert daemon._recorder._device_name == "Microphone B"


def test_on_config_changed_publishes_reload_event(tmp_path: Path) -> None:
    """_on_config_changed should publish a 'config_reloaded' event on success."""
    daemon = _make_daemon(tmp_path, device_name="Microphone A")

    # Subscribe before triggering the change.
    q = daemon._event_bus.subscribe()

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[audio]\ndevice_name = "Microphone B"\n', encoding="utf-8")

    daemon._on_config_changed(cfg_file)

    # Drain all queued events.
    import queue as _queue
    events = []
    while True:
        try:
            events.append(q.get_nowait())
        except _queue.Empty:
            break

    types_ = [e.type for e in events]
    assert "config_reloaded" in types_


def test_on_config_changed_swallows_parse_error(tmp_path: Path) -> None:
    """_on_config_changed must not raise when config.toml is invalid TOML."""
    daemon = _make_daemon(tmp_path, device_name="Microphone A")

    bad_file = tmp_path / "config.toml"
    bad_file.write_text("this is not [ valid toml ]]]\n", encoding="utf-8")

    # Must not raise.
    daemon._on_config_changed(bad_file)

    # recorder._device_name should be unchanged.
    assert daemon._recorder._device_name == "Microphone A"
