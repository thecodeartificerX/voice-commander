"""Unit tests for ADR 0102 — dictation backend selector (internal vs external).

The external backend is a passthrough sub-state: VC plays the same chime +
DICTATING animation, mutes its own routing, and records/transcribes/pastes
nothing.  Right Ctrl enters/exits; an external tool (Wispr Flow) does the real
dictation.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from voice_commander.daemon import StreamingDaemon
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.plan import Plan, ToolCall
from voice_commander.verb_router import VerbRouter


def _make_daemon(
    *,
    event_bus: EventBus | None = None,
    backend: str = "external",
) -> tuple[StreamingDaemon, CapturingFeedbackSink, MagicMock]:
    """Return (daemon, feedback, recorder) with all hardware mocked.

    Mirrors tests/unit/test_daemon_session_helpers.py::_make_daemon.
    """
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
        output_dir="outputs",
        event_bus=event_bus,
        dictation_session=None,
    )
    daemon._transcriber_ready.set()
    daemon._dictation_backend = backend
    return daemon, feedback, recorder


def _drain(q) -> list[str]:
    out = []
    while not q.empty():
        out.append(q.get_nowait().type)
    return out


def test_external_enter_from_idle_opens_owned_session_and_sets_flag() -> None:
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, recorder = _make_daemon(event_bus=bus)
    assert daemon._session_active is False

    daemon.on_dictation_toggle()

    assert daemon._passthrough_active is True
    assert daemon._session_active is True
    assert daemon._session_opened_by_dictation is True
    recorder.open_session.assert_called_once()
    events = _drain(q)
    assert "session_started" in events
    assert "dictation.start" in events


def test_external_exit_from_idle_closes_owned_session_and_clears_flag() -> None:
    bus = EventBus()
    daemon, _, recorder = _make_daemon(event_bus=bus)
    daemon.on_dictation_toggle()  # enter (owned session)
    q = bus.subscribe()

    daemon.on_dictation_toggle()  # exit

    assert daemon._passthrough_active is False
    assert daemon._session_active is False
    assert daemon._session_opened_by_dictation is False
    recorder.close_session.assert_called_once()
    events = _drain(q)
    assert "dictation.end" in events
    assert "session_stopped" in events


def test_external_enter_with_session_open_does_not_open_session() -> None:
    bus = EventBus()
    daemon, _, recorder = _make_daemon(event_bus=bus)
    daemon._session_active = True  # simulate Scroll Lock session already open
    daemon._session_opened_by_dictation = False
    q = bus.subscribe()

    daemon.on_dictation_toggle()  # enter passthrough as a sub-state

    assert daemon._passthrough_active is True
    assert daemon._session_active is True
    recorder.open_session.assert_not_called()
    events = _drain(q)
    assert "dictation.start" in events
    assert "session_started" not in events


def test_external_exit_with_scroll_lock_session_keeps_session_open() -> None:
    bus = EventBus()
    daemon, _, recorder = _make_daemon(event_bus=bus)
    daemon._session_active = True
    daemon._session_opened_by_dictation = False
    daemon.on_dictation_toggle()  # enter
    q = bus.subscribe()

    daemon.on_dictation_toggle()  # exit

    assert daemon._passthrough_active is False
    assert daemon._session_active is True
    recorder.close_session.assert_not_called()
    events = _drain(q)
    assert "dictation.end" in events
    assert "session_stopped" not in events


def test_external_enter_aborts_when_recorder_open_fails() -> None:
    daemon, _, recorder = _make_daemon()
    recorder.open_session.side_effect = RuntimeError("device busy")

    daemon.on_dictation_toggle()  # enter attempt from idle

    assert daemon._passthrough_active is False
    assert daemon._session_active is False
