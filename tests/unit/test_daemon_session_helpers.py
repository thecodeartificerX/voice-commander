"""Unit tests for ADR 0090 — _session_opened_by_dictation flag, shared session
helpers, and on_dictation_toggle idle branch.

All hardware subsystems are replaced with MagicMocks. No real audio, no GPU.
Mirrors tests/unit/test_streaming_daemon.py style exactly.

NOTE: soxr, sounddevice, silero_vad, and torch are all real installed
dependencies — do NOT stub them here.  test_resampler.py and
test_streaming_recorder.py need the real soxr/sounddevice modules bound in
sys.modules; installing MagicMock stubs at module level would poison those
test files for the entire pytest session.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_commander.daemon import StreamingDaemon
from voice_commander.dictation.session import DictationSession
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.plan import Plan, ToolCall
from voice_commander.transcriber import TranscriptionResult
from voice_commander.verb_router import VerbRouter


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _make_daemon(
    *,
    output_dir: str = "outputs",
    event_bus: EventBus | None = None,
    dictation_session: DictationSession | None = None,
) -> tuple[StreamingDaemon, CapturingFeedbackSink, MagicMock]:
    """Return (daemon, feedback, recorder) with all hardware mocked."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_open = False
    transcriber = MagicMock()
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
        output_dir=output_dir,
        event_bus=event_bus,
        dictation_session=dictation_session,
    )
    daemon._transcriber_ready.set()
    return daemon, feedback, recorder


# ---------------------------------------------------------------------------
# Test: _session_opened_by_dictation field initialises to False
# ---------------------------------------------------------------------------


def test_session_opened_by_dictation_starts_false(tmp_path) -> None:
    """Daemon must initialise _session_opened_by_dictation=False."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    assert daemon._session_opened_by_dictation is False


# ---------------------------------------------------------------------------
# Tests: _open_voice_session behaviour
# ---------------------------------------------------------------------------


def test_open_voice_session_returns_true_on_success(tmp_path) -> None:
    """_open_voice_session() returns True and sets _session_active on success."""
    daemon, feedback, recorder = _make_daemon(output_dir=str(tmp_path))

    result = daemon._open_voice_session()

    assert result is True
    assert daemon._session_active is True
    recorder.open_session.assert_called_once()


def test_open_voice_session_publishes_events_in_order(tmp_path) -> None:
    """_open_voice_session must publish session_started then unmuted (exact order)."""
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path), event_bus=bus)

    daemon._open_voice_session()

    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    assert events == ["session_started", "unmuted"], (
        f"Expected [session_started, unmuted]; got {events}"
    )


def test_open_voice_session_does_not_set_flag(tmp_path) -> None:
    """_open_voice_session must NOT set _session_opened_by_dictation."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._open_voice_session()
    assert daemon._session_opened_by_dictation is False


def test_open_voice_session_returns_false_on_recorder_error(tmp_path) -> None:
    """_open_voice_session() returns False (not raise) when recorder.open_session raises."""
    daemon, feedback, recorder = _make_daemon(output_dir=str(tmp_path))
    recorder.open_session.side_effect = RuntimeError("device unavailable")

    result = daemon._open_voice_session()

    assert result is False
    assert daemon._session_active is False
    assert any(c[0] == "on_error" for c in feedback.calls)


def test_open_voice_session_returns_false_when_recorder_is_none(tmp_path) -> None:
    """_open_voice_session returns False and logs a warning when recorder is None."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._recorder = None

    result = daemon._open_voice_session()

    assert result is False


def test_open_voice_session_bumps_audio_gen(tmp_path) -> None:
    """_open_voice_session must increment _audio_gen."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    before = daemon._audio_gen
    daemon._open_voice_session()
    assert daemon._audio_gen == before + 1


# ---------------------------------------------------------------------------
# Tests: _close_voice_session behaviour
# ---------------------------------------------------------------------------


def test_close_voice_session_resets_session_active(tmp_path) -> None:
    """_close_voice_session sets _session_active=False."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._audio_gen = 1

    daemon._close_voice_session()

    assert daemon._session_active is False


def test_close_voice_session_always_clears_session_opened_by_dictation(tmp_path) -> None:
    """_close_voice_session must reset _session_opened_by_dictation=False regardless of prior value."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._session_opened_by_dictation = True

    daemon._close_voice_session()

    assert daemon._session_opened_by_dictation is False


def test_close_voice_session_publishes_events_in_order(tmp_path) -> None:
    """_close_voice_session must publish muted then session_stopped (exact order)."""
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path), event_bus=bus)
    daemon._session_active = True

    daemon._close_voice_session()

    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    assert events == ["muted", "session_stopped"], (
        f"Expected [muted, session_stopped]; got {events}"
    )


def test_close_voice_session_calls_on_recording_stop(tmp_path) -> None:
    """_close_voice_session must call feedback.on_recording_stop."""
    daemon, feedback, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True

    daemon._close_voice_session()

    assert any(c[0] == "on_recording_stop" for c in feedback.calls)


def test_close_voice_session_bumps_audio_gen(tmp_path) -> None:
    """_close_voice_session must increment _audio_gen."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    before = daemon._audio_gen

    daemon._close_voice_session()

    assert daemon._audio_gen == before + 1


def test_close_voice_session_cancels_active_dictation(tmp_path) -> None:
    """_close_voice_session cancels active DictationSession."""
    bus = EventBus()
    ds = DictationSession(bus=bus, end_word="done")
    daemon, _, _ = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus, dictation_session=ds
    )
    daemon._session_active = True
    ds.start()
    assert ds.active is True

    daemon._close_voice_session()

    assert ds.active is False


def test_close_voice_session_is_noop_when_recorder_is_none(tmp_path) -> None:
    """_close_voice_session returns without crashing when recorder is None."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._recorder = None
    daemon._session_active = True
    daemon._session_opened_by_dictation = True

    # Must not raise
    daemon._close_voice_session()

    # Flag is always cleared even with None recorder
    assert daemon._session_opened_by_dictation is False


# ---------------------------------------------------------------------------
# Tests: on_scroll_lock refactor regression guard
# ---------------------------------------------------------------------------


def test_on_scroll_lock_open_does_not_set_session_opened_by_dictation(tmp_path) -> None:
    """on_scroll_lock open path must NEVER set _session_opened_by_dictation."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))

    daemon.on_scroll_lock()  # open path

    assert daemon._session_opened_by_dictation is False


def test_on_scroll_lock_close_resets_session_opened_by_dictation(tmp_path) -> None:
    """on_scroll_lock close path resets _session_opened_by_dictation to False."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._session_opened_by_dictation = True  # simulate a prior Ctrl-open

    daemon.on_scroll_lock()  # close path

    assert daemon._session_opened_by_dictation is False


def test_on_scroll_lock_open_publishes_session_started_then_unmuted(tmp_path) -> None:
    """Regression: on_scroll_lock open path event order must be session_started → unmuted."""
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path), event_bus=bus)

    daemon.on_scroll_lock()

    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    assert events == ["session_started", "unmuted"], (
        f"Regression: on_scroll_lock open event order changed; got {events}"
    )


def test_on_scroll_lock_close_publishes_muted_then_session_stopped(tmp_path) -> None:
    """Regression: on_scroll_lock close path event order must be muted → session_stopped."""
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path), event_bus=bus)
    daemon._session_active = True

    daemon.on_scroll_lock()

    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    assert events == ["muted", "session_stopped"], (
        f"Regression: on_scroll_lock close event order changed; got {events}"
    )


# ---------------------------------------------------------------------------
# Tests: _end_owned_session_if_needed
# ---------------------------------------------------------------------------


def test_end_owned_session_if_needed_calls_close_when_flag_true(tmp_path) -> None:
    """_end_owned_session_if_needed calls _close_voice_session when flag=True."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._session_opened_by_dictation = True

    daemon._end_owned_session_if_needed()

    recorder.close_session.assert_called_once()
    assert daemon._session_opened_by_dictation is False


def test_end_owned_session_if_needed_is_noop_when_flag_false(tmp_path) -> None:
    """_end_owned_session_if_needed is a no-op when _session_opened_by_dictation=False."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._session_opened_by_dictation = False

    daemon._end_owned_session_if_needed()

    recorder.close_session.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: on_dictation_toggle idle branch (no active session)
# ---------------------------------------------------------------------------


def test_on_dictation_toggle_no_session_opens_and_starts_dictation(tmp_path) -> None:
    """on_dictation_toggle with no session: opens session, sets flag, starts dictation."""
    bus = EventBus()
    ds = DictationSession(bus=bus, end_word="done")
    daemon, feedback, recorder = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus, dictation_session=ds
    )
    assert daemon._session_active is False

    daemon.on_dictation_toggle()

    recorder.open_session.assert_called_once()
    assert daemon._session_active is True
    assert daemon._session_opened_by_dictation is True
    assert ds.active is True


def test_on_dictation_toggle_no_session_plays_no_miss_chime(tmp_path) -> None:
    """on_dictation_toggle with no session must NOT play a miss chime.

    Red-phase prediction: this test FAILS against the current production code,
    because the current code calls self._feedback.on_miss(...) in the idle branch.
    It goes green in Task 3 when the idle branch is rewritten.
    """
    bus = EventBus()
    ds = DictationSession(bus=bus, end_word="done")
    daemon, feedback, recorder = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus, dictation_session=ds
    )

    daemon.on_dictation_toggle()

    assert not any(c[0] == "on_miss" for c in feedback.calls), (
        "on_dictation_toggle with no session must not play a miss chime; "
        f"got: {feedback.calls}"
    )


def test_on_dictation_toggle_no_session_recorder_failure_does_not_set_flag(tmp_path) -> None:
    """on_dictation_toggle: when _open_voice_session fails, flag stays False, dictation not started."""
    bus = EventBus()
    ds = DictationSession(bus=bus, end_word="done")
    daemon, feedback, recorder = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus, dictation_session=ds
    )
    recorder.open_session.side_effect = RuntimeError("device unavailable")

    daemon.on_dictation_toggle()

    assert daemon._session_opened_by_dictation is False
    assert ds.active is False
