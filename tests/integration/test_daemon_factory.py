"""Integration tests for build_streaming_daemon() factory.

These tests verify that the factory correctly wires all subsystems into a
StreamingDaemon instance.  Expensive I/O and GPU initialization are
monkeypatched out so the tests run without a microphone, CUDA, or LM Studio.

Covered scenarios
-----------------
1. Default config → daemon._resolver is wired; all other components are present.
2. Custom LLMConfig values propagate to LLMRouter (e.g. timeout_ms).
3. Config values propagate correctly to components (model_size,
   recorder device, llm.timeout_ms).
4. Factory is idempotent — two calls with identical config yield two
   independent daemons (distinct _utt_q, distinct _registry, distinct
   _recorder, distinct _shutdown).

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
    HotkeyConfig,
    LLMConfig,
    TranscriptionConfig,
    WebConfig,
)
from voice_commander.daemon import StreamingDaemon, build_streaming_daemon
from voice_commander.llm_router import LLMRouter

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
        hotkey=HotkeyConfig(key="scroll_lock", mute_key=""),
        audio=AudioConfig(device=-1, channels=1, output_dir=str(tmp_path / "outputs")),
        transcription=TranscriptionConfig(
            model_size="tiny.en",
            device="cpu",
            compute_type="int8",
        ),
        # Web UI disabled — no port binding attempted.
        web=WebConfig(enabled=False),
        # LLM router: warmup disabled to avoid network call.
        llm=LLMConfig(timeout_ms=300, warmup_on_startup=False),
    )


# ---------------------------------------------------------------------------
# Helper: patch all expensive external calls in daemon.py
# ---------------------------------------------------------------------------


def _base_patch_kwargs(
    *,
    transcriber_cls: Any = None,
    recorder_cls: Any = None,
    llm_router_cls: Any = None,
) -> dict[str, Any]:
    """Build keyword arguments for patch.multiple targeting voice_commander.daemon.

    ``torch.set_num_threads`` is patched separately (see _full_patches) because
    torch is a local import inside build_streaming_daemon(), not a module attr.
    """
    kwargs: dict[str, Any] = dict(
        load_silero_vad=MagicMock(return_value=MagicMock(name="silero_model")),
        WindowsFeedbackSink=MagicMock(return_value=MagicMock(name="feedback")),
        Transcriber=transcriber_cls or MagicMock(return_value=MagicMock(name="transcriber")),
        StreamingRecorder=recorder_cls or MagicMock(return_value=MagicMock(name="recorder")),
        validate_config_or_die=MagicMock(),
        validate_or_die=MagicMock(),
        create_app=MagicMock(return_value=MagicMock(name="fastapi_app")),
        WebServer=MagicMock(return_value=MagicMock(name="web_server")),
    )
    if llm_router_cls is not None:
        kwargs["LLMRouter"] = llm_router_cls
    return kwargs


@contextmanager
def _full_patches(**daemon_kwargs: Any) -> Generator[None, None, None]:
    """Patch torch.set_num_threads AND all daemon-module targets together."""
    with ExitStack() as stack:
        stack.enter_context(patch("torch.set_num_threads"))
        stack.enter_context(patch.multiple(_DAEMON_MOD, **daemon_kwargs))
        yield


# ---------------------------------------------------------------------------
# Test 1 — Default config: daemon is built; _resolver is wired; all other
#           components are present.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_factory_resolver_always_wired(base_cfg: Config) -> None:
    """build_streaming_daemon always wires _resolver regardless of config."""
    with _full_patches(**_base_patch_kwargs()):
        daemon = build_streaming_daemon(base_cfg)

    assert isinstance(daemon, StreamingDaemon)

    # Resolver must always be present.
    assert daemon._resolver is not None, "Expected _resolver to be wired"

    # All other required components must be wired.
    assert daemon._transcriber is not None, "transcriber missing"
    assert daemon._dispatcher is not None, "dispatcher missing"
    assert daemon._feedback is not None, "feedback missing"
    assert daemon._recorder is not None, "recorder missing"
    assert daemon._registry is not None, "registry missing"

    # Pipeline queue must be a real queue.
    assert isinstance(daemon._utt_q, queue.Queue)


# ---------------------------------------------------------------------------
# Test 2 — LLMRouter is always created and wired into _resolver.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_factory_llm_router_always_created(base_cfg: Config) -> None:
    """build_streaming_daemon always creates an LLMRouter and passes it to Resolver."""
    mock_router_instance = MagicMock(name="llm_router_instance", spec=LLMRouter)
    mock_router_cls = MagicMock(return_value=mock_router_instance)

    with _full_patches(**_base_patch_kwargs(llm_router_cls=mock_router_cls)):
        daemon = build_streaming_daemon(base_cfg)

    # LLMRouter constructor must have been called.
    mock_router_cls.assert_called_once()

    # _resolver must be wired (it holds the LLMRouter internally).
    assert daemon._resolver is not None, "Expected _resolver to be wired"
    assert daemon._resolver._llm_router is mock_router_instance, (
        "Expected _resolver._llm_router to be the mocked LLMRouter instance"
    )


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


@pytest.mark.integration
def test_factory_propagates_llm_timeout(base_cfg: Config) -> None:
    """LLMRouter is constructed with the LLMConfig that carries the correct timeout_ms."""
    cfg = replace(
        base_cfg,
        llm=LLMConfig(timeout_ms=800, warmup_on_startup=False),
    )

    mock_router_cls = MagicMock(return_value=MagicMock(spec=LLMRouter))

    with _full_patches(**_base_patch_kwargs(llm_router_cls=mock_router_cls)):
        build_streaming_daemon(cfg)

    mock_router_cls.assert_called_once()
    # First positional argument to LLMRouter(config, registry) is the LLMConfig.
    router_cfg_arg = mock_router_cls.call_args.args[0]
    assert router_cfg_arg.timeout_ms == 800, (
        f"Expected LLMConfig.timeout_ms=800, got {router_cfg_arg.timeout_ms}"
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
    - Distinct _resolver instances (each daemon has its own Resolver).

    Note on _registry: ``discover()`` intentionally returns the module-level
    ``_GLOBAL_REGISTRY`` singleton, so both daemons will reference the same
    registry object.  This is by design — the global registry is read-only at
    runtime after startup.  We therefore do NOT assert registry identity here;
    we assert that per-daemon mutable state (queues, events, threads, resolvers)
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

    # --- Resolver independence ---
    # Each daemon gets its own Resolver, even though they share the global registry.
    assert daemon1._resolver is not daemon2._resolver, "_resolver must be distinct per daemon"
