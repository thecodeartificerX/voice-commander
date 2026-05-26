"""Integration tests for build_streaming_daemon() factory.

These tests verify that the factory correctly wires all subsystems into a
StreamingDaemon instance.  Expensive I/O and GPU initialization are
monkeypatched out so the tests run without a microphone, CUDA, or LM Studio.

Covered scenarios
-----------------
1. Default config → all required components are present.
2. Config values propagate correctly to components (model_size, recorder device).
3. Factory is idempotent — two calls with identical config yield two
   independent daemons (distinct _utt_q, distinct _recorder, distinct _shutdown).

Notes on patching strategy
--------------------------
``build_streaming_daemon()`` uses a *local* ``import torch`` inside the
function body, so ``torch`` is never a module-level attribute of
``voice_commander.daemon``.  We therefore patch ``torch.set_num_threads``
directly on the ``torch`` module rather than trying to shadow a daemon-module
attribute.  Everything else (``load_silero_vad``, ``Transcriber``, …) is
imported at the top of ``daemon.py`` and is patchable via
``patch.multiple("voice_commander.daemon", ...)``.
"""

from __future__ import annotations

import queue
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from voice_commander.config import (
    AudioConfig,
    Config,
    DictationConfig,
    HotkeyConfig,
    TranscriptionConfig,
    WebConfig,
)
from voice_commander.daemon import StreamingDaemon, build_streaming_daemon

# ---------------------------------------------------------------------------
# Patch-target constants
# ---------------------------------------------------------------------------

_DAEMON_MOD = "voice_commander.daemon"


# ---------------------------------------------------------------------------
# Shared fixture: minimal Config with all expensive subsystems disabled
# ---------------------------------------------------------------------------


@pytest.fixture()
def base_cfg(tmp_path: Path) -> Config:
    """Minimal Config that avoids CUDA, audio devices, and network calls."""
    return Config(
        hotkey=HotkeyConfig(key="scroll_lock"),
        audio=AudioConfig(device=-1, channels=1, output_dir=str(tmp_path / "outputs")),
        transcription=TranscriptionConfig(
            model_size="tiny.en",
            device="cpu",
            compute_type="int8",
        ),
        # Web UI disabled — no port binding attempted.
        web=WebConfig(enabled=False),
    )


# ---------------------------------------------------------------------------
# Helper: patch all expensive external calls in daemon.py
# ---------------------------------------------------------------------------


def _base_patch_kwargs(
    *,
    transcriber_cls: Any = None,
    recorder_cls: Any = None,
) -> dict[str, Any]:
    """Build keyword arguments for patch.multiple targeting voice_commander.daemon.

    ``torch.set_num_threads`` is patched separately (see _full_patches) because
    torch is a local import inside build_streaming_daemon(), not a module attr.
    """
    mock_registry = MagicMock(name="registry")
    mock_registry.all.return_value = []
    mock_store = MagicMock(name="store")
    mock_store.load_all.return_value = {}
    kwargs: dict[str, Any] = dict(
        load_silero_vad=MagicMock(return_value=MagicMock(name="silero_model")),
        WindowsFeedbackSink=MagicMock(return_value=MagicMock(name="feedback")),
        Transcriber=transcriber_cls or MagicMock(return_value=MagicMock(name="transcriber")),
        StreamingRecorder=recorder_cls or MagicMock(return_value=MagicMock(name="recorder")),
        validate_config_or_die=MagicMock(),
        validate_or_die=MagicMock(),
        create_app=MagicMock(return_value=MagicMock(name="fastapi_app")),
        WebServer=MagicMock(return_value=MagicMock(name="web_server")),
        discover=MagicMock(return_value=mock_registry),
        ToolMetadataStore=MagicMock(return_value=mock_store),
    )
    return kwargs


@contextmanager
def _full_patches(**daemon_kwargs: Any) -> Generator[None, None, None]:
    """Patch torch.set_num_threads AND all daemon-module targets together."""
    with ExitStack() as stack:
        stack.enter_context(patch("torch.set_num_threads"))
        stack.enter_context(patch.multiple(_DAEMON_MOD, **daemon_kwargs))
        yield


# ---------------------------------------------------------------------------
# Test 1 — Default config: daemon is built; all required components are present.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_factory_wires_required_components(base_cfg: Config) -> None:
    """build_streaming_daemon wires all required components."""
    with _full_patches(**_base_patch_kwargs()):
        daemon = build_streaming_daemon(base_cfg)

    assert isinstance(daemon, StreamingDaemon)

    # All required components must be wired.
    assert daemon._transcriber is not None, "transcriber missing"
    assert daemon._dispatcher is not None, "dispatcher missing"
    assert daemon._feedback is not None, "feedback missing"
    assert daemon._recorder is not None, "recorder missing"
    assert daemon._registry is not None, "registry missing"

    # Pipeline queue must be a real queue.
    assert isinstance(daemon._utt_q, queue.Queue)


# ---------------------------------------------------------------------------
# Test 3 — Config values propagate correctly to constructed components
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_factory_propagates_transcriber_config(base_cfg: Config) -> None:
    """Transcriber is constructed with model_size / device / compute_type from config."""
    cfg = replace(
        base_cfg,
        transcription=TranscriptionConfig(
            model_size="base.en",
            device="cpu",
            compute_type="int8",
        ),
    )

    mock_transcriber_cls = MagicMock(return_value=MagicMock(name="transcriber"))

    with _full_patches(**_base_patch_kwargs(transcriber_cls=mock_transcriber_cls)):
        build_streaming_daemon(cfg)

    mock_transcriber_cls.assert_called_once_with(
        model_size="base.en",
        device="cpu",
        compute_type="int8",
    )


@pytest.mark.integration
def test_factory_propagates_recorder_device_minus_one(base_cfg: Config) -> None:
    """StreamingRecorder receives device=None when cfg.audio.device == -1."""
    mock_recorder_cls = MagicMock(return_value=MagicMock(name="recorder"))

    with _full_patches(**_base_patch_kwargs(recorder_cls=mock_recorder_cls)):
        build_streaming_daemon(base_cfg)

    call_kwargs = mock_recorder_cls.call_args.kwargs
    # The factory maps device=-1 → None (system default) for StreamingRecorder.
    assert call_kwargs["device"] is None, (
        f"Expected recorder device=None for cfg.audio.device=-1, got {call_kwargs['device']}"
    )


# ---------------------------------------------------------------------------
# Dictation wiring (ADR 0086)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_factory_wires_dictation(base_cfg: Config) -> None:
    """build_streaming_daemon constructs a DictationSession and wires the ws_url.

    Asserts:
    - ``daemon._dictation_session`` is not None (session always constructed).
    - ``daemon._dictation_ws_url`` matches ``cfg.dictation.ws_url``.
    """
    custom_ws_url = "ws://10.0.0.1:9999/ws/transcribe"
    cfg = replace(
        base_cfg,
        dictation=DictationConfig(
            ws_url=custom_ws_url,
            end_word="finish",
        ),
    )
    with _full_patches(**_base_patch_kwargs()):
        daemon = build_streaming_daemon(cfg)

    assert daemon._dictation_session is not None, (
        "_dictation_session must be set by the factory"
    )
    assert daemon._dictation_ws_url == custom_ws_url, (
        f"Expected _dictation_ws_url={custom_ws_url!r}, "
        f"got {daemon._dictation_ws_url!r}"
    )
    assert daemon._dictation_session._end_word == "finish", (
        "DictationSession end_word not propagated from cfg; "
        f"got {daemon._dictation_session._end_word!r}"
    )




# ---------------------------------------------------------------------------
# Test 4 — Idempotency: two calls with the same config return independent
#           daemons with no shared mutable state.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_factory_idempotent_independent_state(base_cfg: Config, tmp_path: Path) -> None:
    """Two build_streaming_daemon() calls return daemons with no shared mutable per-daemon state.

    Verified:
    - Distinct _utt_q objects (separate Queue instances).
    - Enqueuing to daemon1 does not affect daemon2.
    - Distinct _shutdown Events (setting daemon1's does not affect daemon2).
    - Distinct _recorder objects.

    Note on _registry: ``discover()`` intentionally returns the module-level
    ``_GLOBAL_REGISTRY`` singleton, so both daemons will reference the same
    registry object.  This is by design — the global registry is read-only at
    runtime after startup.  We therefore do NOT assert registry identity here;
    we assert that per-daemon mutable state (queues, events, threads, routers)
    is independent.
    """
    cfg1 = replace(base_cfg, audio=replace(base_cfg.audio, output_dir=str(tmp_path / "d1")))
    cfg2 = replace(base_cfg, audio=replace(base_cfg.audio, output_dir=str(tmp_path / "d2")))

    with _full_patches(**_base_patch_kwargs()):
        daemon1 = build_streaming_daemon(cfg1)

    with _full_patches(**_base_patch_kwargs()):
        daemon2 = build_streaming_daemon(cfg2)

    # Both must be valid StreamingDaemon instances, and distinct objects.
    assert isinstance(daemon1, StreamingDaemon)
    assert isinstance(daemon2, StreamingDaemon)
    assert daemon1 is not daemon2

    # --- Queue independence ---
    assert daemon1._utt_q is not daemon2._utt_q, "_utt_q must be distinct Queue instances"
    sentinel: object = object()
    daemon1._utt_q.put_nowait(sentinel)  # type: ignore[arg-type]
    assert daemon2._utt_q.empty(), "daemon2._utt_q polluted by daemon1 enqueue"
    daemon1._utt_q.get_nowait()  # clean up

    # --- Shutdown Event independence ---
    assert daemon1._shutdown is not daemon2._shutdown, "_shutdown Events must be distinct"
    daemon1._shutdown.set()
    assert not daemon2._shutdown.is_set(), (
        "daemon2._shutdown should not be set when daemon1._shutdown.set() is called"
    )

    # --- Recorder independence ---
    assert daemon1._recorder is not daemon2._recorder, "_recorder must be distinct per daemon"




# ---------------------------------------------------------------------------
# EventBus wiring
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_daemon_factory_creates_event_bus(base_cfg: Config) -> None:
    """Event bus is always created."""
    with _full_patches(**_base_patch_kwargs()):
        daemon = build_streaming_daemon(base_cfg)
    assert daemon._event_bus is not None


@pytest.mark.integration
def test_daemon_factory_event_bus_passed_to_dispatcher(base_cfg: Config) -> None:
    with _full_patches(**_base_patch_kwargs()):
        daemon = build_streaming_daemon(base_cfg)
    assert daemon._dispatcher._event_bus is daemon._event_bus


# ---------------------------------------------------------------------------
# Backend selector wiring (ADR 0102)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_build_streaming_daemon_wires_external_backend(base_cfg: Config) -> None:
    """build_streaming_daemon sets _dictation_backend from cfg.dictation.backend."""
    cfg = replace(base_cfg, dictation=DictationConfig(backend="external"))
    with _full_patches(**_base_patch_kwargs()):
        daemon = build_streaming_daemon(cfg)
    assert daemon._dictation_backend == "external"


@pytest.mark.integration
def test_build_streaming_daemon_defaults_backend_internal(base_cfg: Config) -> None:
    """build_streaming_daemon sets _dictation_backend to 'internal' by default."""
    with _full_patches(**_base_patch_kwargs()):
        daemon = build_streaming_daemon(base_cfg)
    assert daemon._dictation_backend == "internal"
