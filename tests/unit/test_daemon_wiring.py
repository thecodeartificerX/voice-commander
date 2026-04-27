from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch


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

        def all_llm_visible(self):
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
        patch("voice_commander.daemon.LLMRouter") as MockLLMRouter,
        patch("voice_commander.daemon.StreamingRecorder"),
        patch("voice_commander.daemon.WindowsFeedbackSink"),
        patch("voice_commander.daemon.create_app"),
        patch("voice_commander.daemon.discover", return_value=fake_registry),
        patch("voice_commander.daemon.validate_config_or_die"),
        patch("voice_commander.daemon.validate_or_die"),
        patch("voice_commander.daemon.log_llm_sources"),
        patch("voice_commander.daemon.ToolMetadataStore"),
        patch("voice_commander.daemon.VADGate"),
        patch("voice_commander.commands.registrar.reload_all", return_value=([], [])),
        patch("voice_commander.commands.GraphStore"),
        patch("voice_commander.commands.seed_if_missing"),
        patch("voice_commander.tools.primitives._set_mute_callback"),
    ):
        # LLMRouter mock needs set_tracer and warmup
        mock_router = MagicMock()
        mock_router.warmup.return_value = True
        mock_router.set_tracer = MagicMock()
        MockLLMRouter.return_value = mock_router

        daemon = build_streaming_daemon(cfg)

    assert daemon._tracer is not None
    assert daemon._tracer.enabled is True
