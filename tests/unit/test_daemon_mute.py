"""Unit tests for the ADR 0025 mute hotkey behavior.

Covers the 6-case session × mute state matrix, the pipeline mute guard
(utterance arriving after mute toggle is dropped silently), the muted
shutdown path, and the EventBus `muted` / `unmuted` events that drive
the sprite overlay.

The recorder, transcriber, and dispatcher are all mocked,
so no real audio / GPU work happens. Utterances are injected directly
through ``daemon._process_utterance``.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np

# Stub heavy deps before voice_commander.daemon is imported.
for _mod in ("silero_vad", "torch", "sounddevice", "soxr"):
    sys.modules.setdefault(_mod, MagicMock())

from voice_commander.daemon import StreamingDaemon  # noqa: E402
from voice_commander.event_bus import EventBus  # noqa: E402
from voice_commander.feedback import CapturingFeedbackSink  # noqa: E402
from voice_commander.plan import Plan, ToolCall  # noqa: E402
from voice_commander.transcriber import TranscriptionResult  # noqa: E402
from voice_commander.verb_router import VerbRouter  # noqa: E402


def _make_daemon(event_bus: EventBus | None = None):
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_open = False
    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="hello world",
        language="en",
        duration_ms=500,
        confidence=0.95,
        no_speech_prob=0.01,
    )
    dispatcher = MagicMock()
    verb_router = MagicMock(spec=VerbRouter)
    verb_router.route.return_value = Plan(
        steps=(ToolCall(name="press", kwargs={"combo": "ctrl+c"}),),
        raw_response={"router": "verb"},
    )
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=MagicMock(),
        event_bus=event_bus,
    )
    daemon._transcriber_ready.set()
    return daemon, feedback, recorder, dispatcher


def _event_types(bus: EventBus) -> list[str]:
    return [e.type for e in bus.replay_after(0)]


# ---------------------------------------------------------------------------
# State-matrix: 6 transitions
# ---------------------------------------------------------------------------


def test_mute_toggle_inactive_session_is_noop() -> None:
    bus = EventBus()
    daemon, feedback, recorder, _ = _make_daemon(event_bus=bus)
    assert daemon._session_active is False
    daemon.on_mute_toggle()
    assert daemon._muted is False
    assert recorder.close_session.call_count == 0
    assert recorder.open_session.call_count == 0
    assert "muted" not in _event_types(bus)
    assert feedback.calls == []


def test_active_unmuted_to_muted_closes_stream() -> None:
    bus = EventBus()
    daemon, feedback, recorder, _ = _make_daemon(event_bus=bus)
    daemon.on_scroll_lock()  # active+unmuted
    recorder.reset_mock()
    feedback.calls.clear()

    daemon.on_mute_toggle()

    assert daemon._session_active is True
    assert daemon._muted is True
    assert recorder.close_session.call_count == 1
    assert recorder.open_session.call_count == 0
    assert "muted" in _event_types(bus)
    # Sprite + audio chime: muting acts like recording_stop visually.
    assert feedback.calls[-1][0] == "on_recording_stop"


def test_active_muted_to_unmuted_reopens_stream() -> None:
    bus = EventBus()
    daemon, feedback, _recorder, _ = _make_daemon(event_bus=bus)
    daemon.on_scroll_lock()  # active+unmuted
    daemon.on_mute_toggle()  # active+muted
    recorder = _recorder
    recorder.reset_mock()
    last_id = bus._next_id - 1  # snapshot before the act phase

    daemon.on_mute_toggle()

    assert daemon._session_active is True
    assert daemon._muted is False
    assert recorder.open_session.call_count == 1
    assert recorder.close_session.call_count == 0
    # Post-SSOT contract: every transition into "consuming audio" emits
    # `unmuted` — so the open path emits one and the unmute emits another.
    # Filter to events from the unmute alone.
    types_after = [e.type for e in bus.replay_after(last_id)]
    assert "unmuted" in types_after


def test_scroll_lock_close_from_muted_does_not_double_close() -> None:
    bus = EventBus()
    daemon, _feedback, recorder, _ = _make_daemon(event_bus=bus)
    daemon.on_scroll_lock()  # open
    daemon.on_mute_toggle()  # mute (closes once)
    recorder.reset_mock()
    last_id = bus._next_id - 1  # snapshot before the act phase

    daemon.on_scroll_lock()  # close while muted

    assert daemon._session_active is False
    assert daemon._muted is False
    # Stream stays closed — no second close_session() call.
    assert recorder.close_session.call_count == 0
    # Post-SSOT contract: close path emits `muted` (sprite enters the
    # disengaged grey-tinted pose) before `session_stopped`, regardless of
    # whether the session was muted or unmuted when closed.
    types_after = [e.type for e in bus.replay_after(last_id)]
    assert types_after == ["muted", "session_stopped"]


def test_scroll_lock_close_from_unmuted_closes_once() -> None:
    bus = EventBus()
    daemon, _feedback, recorder, _ = _make_daemon(event_bus=bus)
    daemon.on_scroll_lock()  # open
    recorder.reset_mock()

    daemon.on_scroll_lock()  # close

    assert daemon._session_active is False
    assert daemon._muted is False
    assert recorder.close_session.call_count == 1


def test_scroll_lock_open_clears_muted_flag() -> None:
    daemon, _feedback, recorder, _ = _make_daemon()
    # Pretend the previous session left _muted=True somehow (defensive).
    daemon._session_active = False
    daemon._muted = True

    daemon.on_scroll_lock()

    assert daemon._session_active is True
    assert daemon._muted is False
    assert recorder.open_session.call_count == 1


# ---------------------------------------------------------------------------
# Pipeline mute guard
# ---------------------------------------------------------------------------


def test_pipeline_drops_utterance_when_muted_mid_flight() -> None:
    bus = EventBus()
    daemon, feedback, _recorder, dispatcher = _make_daemon(event_bus=bus)
    daemon.on_scroll_lock()
    daemon.on_mute_toggle()
    feedback.calls.clear()
    bus.replay_after(0)  # ignored; we'll re-read after _process_utterance

    daemon._process_utterance(np.zeros(16000, dtype=np.float32))

    # Verb router never reached, dispatcher never fired.
    assert dispatcher.run_plan.call_count == 0
    # No transcript event emitted (silent drop).
    types_after = _event_types(bus)
    assert "transcript" not in types_after
    # No miss chime — the user knows they're muted.
    miss_events = [e for e in feedback.calls if e[0] == "on_miss"]
    assert miss_events == []


def test_pipeline_runs_normally_when_not_muted() -> None:
    bus = EventBus()
    daemon, _feedback, _recorder, dispatcher = _make_daemon(event_bus=bus)
    daemon.on_scroll_lock()

    daemon._process_utterance(np.zeros(16000, dtype=np.float32))

    assert dispatcher.run_plan.call_count == 1
    assert "transcript" in _event_types(bus)


# ---------------------------------------------------------------------------
# Mute guards drain stale utterances
# ---------------------------------------------------------------------------


def test_mute_drains_pending_utterances() -> None:
    daemon, _feedback, _recorder, _ = _make_daemon()
    daemon.on_scroll_lock()
    # Stuff three utterances into the queue (as if VAD produced them just
    # before mute fired).
    for _ in range(3):
        daemon._utt_q.put_nowait(np.zeros(8000, dtype=np.float32))
    assert daemon._utt_q.qsize() == 3

    daemon.on_mute_toggle()

    assert daemon._utt_q.qsize() == 0


# ---------------------------------------------------------------------------
# Generation-counter race regression (ADR 0025 §4)
# ---------------------------------------------------------------------------


def test_mute_bumps_audio_gen() -> None:
    daemon, _feedback, _recorder, _ = _make_daemon()
    daemon.on_scroll_lock()
    gen_before = daemon._audio_gen

    daemon.on_mute_toggle()

    assert daemon._audio_gen == gen_before + 1


def test_unmute_bumps_audio_gen() -> None:
    daemon, _feedback, _recorder, _ = _make_daemon()
    daemon.on_scroll_lock()
    daemon.on_mute_toggle()
    gen_muted = daemon._audio_gen

    daemon.on_mute_toggle()  # unmute

    assert daemon._audio_gen == gen_muted + 1


def test_scroll_lock_close_bumps_audio_gen() -> None:
    daemon, _feedback, _recorder, _ = _make_daemon()
    daemon.on_scroll_lock()  # open
    gen_open = daemon._audio_gen

    daemon.on_scroll_lock()  # close

    assert daemon._audio_gen == gen_open + 1


def test_pipeline_drops_utterance_when_gen_stale_pre_transcribe() -> None:
    """Pre-transcribe gen check: if mute has invalidated the utterance
    BEFORE the pipeline calls transcribe(), the transcribe call must not
    happen at all (cost saving + side-effect prevention)."""
    bus = EventBus()
    daemon, _feedback, _recorder, dispatcher = _make_daemon(event_bus=bus)
    daemon.on_scroll_lock()
    captured_gen = daemon._audio_gen

    # Simulate: mute fires AFTER VAD enqueued the utterance but BEFORE the
    # pipeline pops it.
    daemon._audio_gen += 1

    daemon._process_utterance(np.zeros(16000, dtype=np.float32), gen=captured_gen)

    daemon._transcriber.transcribe.assert_not_called()
    assert dispatcher.run_plan.call_count == 0
    assert "transcript" not in _event_types(bus)


def test_pipeline_drops_utterance_when_gen_bumped_during_transcribe() -> None:
    """Post-transcribe gen check: this is the actual race the user reported.
    transcribe() finishes BEFORE _muted is set to True (close_session blocks
    on the VAD worker join). _audio_gen is bumped synchronously on the
    hotkey thread BEFORE close_session, so the post-transcribe gen check
    fires even though _muted is still False at the moment we read it."""
    bus = EventBus()
    daemon, _feedback, _recorder, dispatcher = _make_daemon(event_bus=bus)
    daemon.on_scroll_lock()
    captured_gen = daemon._audio_gen

    # Patch transcribe() to bump _audio_gen mid-call — simulating the
    # mute hotkey firing on the listener thread DURING transcription.
    # _muted stays False (mimicking the real race where _muted=True is
    # written only after close_session() returns, which is after transcribe).
    original_transcribe = daemon._transcriber.transcribe

    def _race(audio):
        daemon._audio_gen += 1  # mute bumped gen on listener thread
        return original_transcribe.return_value

    daemon._transcriber.transcribe = _race

    daemon._process_utterance(np.zeros(16000, dtype=np.float32), gen=captured_gen)

    # Stale gen detected post-transcribe — utterance dropped silently.
    assert dispatcher.run_plan.call_count == 0
    assert "transcript" not in _event_types(bus)


def test_pipeline_processes_utterance_with_matching_gen() -> None:
    """Sanity: an utterance whose gen matches current _audio_gen runs through
    the pipeline normally."""
    bus = EventBus()
    daemon, _feedback, _recorder, dispatcher = _make_daemon(event_bus=bus)
    daemon.on_scroll_lock()

    daemon._process_utterance(
        np.zeros(16000, dtype=np.float32),
        gen=daemon._audio_gen,
    )

    assert dispatcher.run_plan.call_count == 1
    assert "transcript" in _event_types(bus)


def test_on_utterance_tags_with_current_gen() -> None:
    """_on_utterance must enqueue (audio, gen) tuples so the pipeline can
    detect stale generations."""
    daemon, _feedback, _recorder, _ = _make_daemon()
    daemon.on_scroll_lock()

    daemon._on_utterance(np.zeros(8000, dtype=np.float32))

    item = daemon._utt_q.get_nowait()
    assert isinstance(item, tuple), f"expected (audio, gen) tuple, got {type(item)}"
    audio, gen = item
    assert isinstance(audio, np.ndarray)
    assert gen == daemon._audio_gen


def test_pipeline_loop_unpacks_tuple_and_bare_ndarray() -> None:
    """_pipeline_loop must accept BOTH (audio, gen) tuples (production path)
    AND bare ndarrays (test injection path) for backward compatibility."""
    daemon, _feedback, _recorder, dispatcher = _make_daemon()
    daemon.on_scroll_lock()

    import threading

    processed = threading.Event()
    original_process = daemon._process_utterance
    seen: list[tuple[bool, int | None]] = []

    def _spy(utt, *, gen=None):
        seen.append((isinstance(utt, np.ndarray), gen))
        original_process(utt, gen=gen)
        if len(seen) == 2:
            processed.set()

    daemon._process_utterance = _spy

    thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    thread.start()
    try:
        daemon._utt_q.put_nowait(np.zeros(8000, dtype=np.float32))  # bare
        daemon._utt_q.put_nowait((np.zeros(8000, dtype=np.float32), 7))  # tuple
        assert processed.wait(timeout=5.0)
    finally:
        daemon._utt_q.put(None)
        thread.join(timeout=3.0)

    assert seen[0] == (True, None), "bare ndarray must arrive with gen=None"
    assert seen[1] == (True, 7), "tuple must arrive with its gen"


# ---------------------------------------------------------------------------
# Shutdown handling
# ---------------------------------------------------------------------------


def test_shutdown_from_muted_does_not_close_stream_again() -> None:
    daemon, _feedback, recorder, _ = _make_daemon()
    daemon.on_scroll_lock()
    daemon.on_mute_toggle()  # closes stream
    recorder.reset_mock()

    daemon.shutdown()

    assert recorder.close_session.call_count == 0
    assert daemon._session_active is False
    assert daemon._muted is False


def test_shutdown_from_active_unmuted_closes_stream() -> None:
    daemon, _feedback, recorder, _ = _make_daemon()
    daemon.on_scroll_lock()
    recorder.reset_mock()

    daemon.shutdown()

    assert recorder.close_session.call_count == 1


# ---------------------------------------------------------------------------
# Recovery on reopen failure
# ---------------------------------------------------------------------------


def test_unmute_keeps_muted_flag_when_reopen_raises() -> None:
    daemon, feedback, recorder, _ = _make_daemon()
    daemon.on_scroll_lock()
    daemon.on_mute_toggle()
    recorder.open_session.side_effect = RuntimeError("device gone")

    daemon.on_mute_toggle()

    # Failed unmute keeps the muted flag set so the user can retry.
    assert daemon._muted is True
    error_events = [e for e in feedback.calls if e[0] == "on_error"]
    assert len(error_events) >= 1


# ---------------------------------------------------------------------------
# Run() wires the secondary binding
# ---------------------------------------------------------------------------


def test_run_binds_mute_key_when_provided() -> None:
    """`run()` should pass mute_key into HotkeyController bindings."""
    from voice_commander.daemon import HotkeyController

    daemon, _feedback, _recorder, _ = _make_daemon()
    captured: dict[str, object] = {}

    class _StubHotkey:
        def __init__(self, bindings: dict[str, object]) -> None:
            captured["bindings"] = bindings

        def start(self) -> None:
            captured["started"] = True
            daemon._shutdown.set()  # exit run() loop immediately

        def stop(self) -> None:
            pass

    import voice_commander.daemon as dm

    original = dm.HotkeyController
    try:
        dm.HotkeyController = _StubHotkey  # type: ignore[assignment,misc]
        daemon.run("scroll_lock", mute_key="ctrl_r")
    finally:
        dm.HotkeyController = original

    bindings = captured["bindings"]
    assert "scroll_lock" in bindings
    assert "ctrl_r" in bindings
    assert bindings["scroll_lock"] == daemon.on_scroll_lock
    assert bindings["ctrl_r"] == daemon.on_mute_toggle
    # Reference HotkeyController to placate F401 — we already replace it above.
    assert HotkeyController is original


def test_run_skips_mute_key_when_empty() -> None:
    daemon, _feedback, _recorder, _ = _make_daemon()
    captured: dict[str, object] = {}

    class _StubHotkey:
        def __init__(self, bindings: dict[str, object]) -> None:
            captured["bindings"] = bindings

        def start(self) -> None:
            daemon._shutdown.set()

        def stop(self) -> None:
            pass

    import voice_commander.daemon as dm

    original = dm.HotkeyController
    try:
        dm.HotkeyController = _StubHotkey  # type: ignore[assignment,misc]
        daemon.run("scroll_lock", mute_key="")
    finally:
        dm.HotkeyController = original

    bindings = captured["bindings"]
    assert list(bindings.keys()) == ["scroll_lock"]


def test_run_warns_when_mute_key_collides_with_hotkey(caplog) -> None:
    daemon, _feedback, _recorder, _ = _make_daemon()
    captured: dict[str, object] = {}

    class _StubHotkey:
        def __init__(self, bindings: dict[str, object]) -> None:
            captured["bindings"] = bindings

        def start(self) -> None:
            daemon._shutdown.set()

        def stop(self) -> None:
            pass

    import logging

    import voice_commander.daemon as dm

    original = dm.HotkeyController
    try:
        dm.HotkeyController = _StubHotkey  # type: ignore[assignment,misc]
        with caplog.at_level(logging.WARNING, logger="voice_commander.daemon"):
            daemon.run("scroll_lock", mute_key="scroll_lock")
    finally:
        dm.HotkeyController = original

    bindings = captured["bindings"]
    assert list(bindings.keys()) == ["scroll_lock"]
    assert any("mute_key" in record.message for record in caplog.records)
