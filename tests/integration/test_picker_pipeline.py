"""Daemon pipeline integration tests for the bare-primitive picker."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from voice_commander.event_bus import EventBus
from voice_commander.feedback import FeedbackSink
from voice_commander.picker.mru import MruEntry, MruTracker
from voice_commander.picker.registry import BarePickerRegistry
from voice_commander.picker.session import PickerSession
from voice_commander.picker.types import PickerItem
from voice_commander.plan import Plan, ToolCall
from voice_commander.tools.focus_picker import FocusPickerSettings, build_focus_picker


class _CaptureFeedback(FeedbackSink):
    def __init__(self) -> None:
        self.missed: list[str] = []
        self.transcripts: list[str] = []
        self.plan_starts: list[tuple[str, int]] = []
        self.plan_completes: list[tuple[str, int]] = []
        self.errors: list[tuple[str, BaseException]] = []

    def on_recording_start(self) -> None: ...
    def on_recording_stop(self) -> None: ...
    def on_transcript(self, text: str, confidence: float) -> None:
        self.transcripts.append(text)
    def on_plan_start(self, transcript: str, n_steps: int) -> None:
        self.plan_starts.append((transcript, n_steps))
    def on_plan_complete(self, transcript: str, executed: int) -> None:
        self.plan_completes.append((transcript, executed))
    def on_miss(self, transcript: str, top3: tuple) -> None:
        self.missed.append(transcript)
    def on_error(self, source: str, exc: BaseException) -> None:
        self.errors.append((source, exc))


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray):
        return self.queue.pop(0)


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


def _make_daemon(*, mru_entries: list[MruEntry], transcripts: list[_Transcription]):
    """Build a stripped-down daemon for pipeline-only testing."""
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.registry import ToolRegistry, get_global_registry, reset_global_registry
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.verb_router import VerbRouter, build_default_rules

    reset_global_registry()
    reset_global_picker_registry()

    # Stub registry with focus tool that records calls.
    captured: list[dict] = []

    def _focus(target: str = "", _hwnd: int = 0) -> int:
        captured.append({"target": target, "_hwnd": _hwnd})
        return _hwnd or 1

    from voice_commander.registry import ToolEntry
    registry = get_global_registry()
    registry.register(
        ToolEntry(
            name="focus",
            phrases=(),
            func=_focus,
            module="test",
            docstring=None,
        )
    )

    tracker = MruTracker(capacity=16)
    for e in mru_entries:
        tracker.record(e)

    picker_reg = BarePickerRegistry()
    provider = build_focus_picker(
        tracker=tracker,
        settings=FocusPickerSettings(cap=5, exclude_foreground=False, exclude_self=False),
        foreground_hwnd=lambda: 0,
    )
    picker_reg.register("focus", provider)

    bus = EventBus()
    feedback = _CaptureFeedback()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=picker_reg)
    picker_session = PickerSession(bus=bus, cancel_words=("cancel",), timeout_sec=5.0)

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
    )
    daemon._transcriber_ready.set()
    daemon._picker_session = picker_session  # set in build_streaming_daemon for prod
    daemon._picker_registry = picker_reg

    return daemon, captured, feedback, bus, picker_session


def test_bare_focus_opens_picker_then_number_fires_focus(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        MruEntry(hwnd=11, pid=1100, proc_name="chrome.exe", title="VC"),
        MruEntry(hwnd=22, pid=2200, proc_name="Code.exe", title="daemon.py"),
        MruEntry(hwnd=33, pid=3300, proc_name="slack.exe", title="eng"),
    ]
    # entries recorded in order 11, 22, 33 → top is 33, 22, 11.
    daemon, captured, feedback, bus, session = _make_daemon(
        mru_entries=entries,
        transcripts=[_Transcription("focus."), _Transcription("two.")],
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "focus." — opens picker
    assert session.active is True
    assert captured == []  # no focus call yet

    daemon._process_utterance(audio)  # "two." — selects items[1]
    assert session.active is False
    # Items were [33, 22, 11]; "two" → items[1] → hwnd 22.
    assert captured == [{"target": "", "_hwnd": 22}]


def test_bare_focus_cancel_word_closes_no_dispatch() -> None:
    entries = [MruEntry(hwnd=11, pid=1, proc_name="chrome.exe", title="t")]
    daemon, captured, feedback, bus, session = _make_daemon(
        mru_entries=entries,
        transcripts=[_Transcription("focus."), _Transcription("cancel.")],
    )
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    assert session.active is False
    assert captured == []


def test_bare_focus_out_of_range_keeps_picker_open() -> None:
    entries = [MruEntry(hwnd=11, pid=1, proc_name="chrome.exe", title="t")]
    daemon, captured, feedback, bus, session = _make_daemon(
        mru_entries=entries,
        transcripts=[_Transcription("focus."), _Transcription("seven."), _Transcription("one.")],
    )
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "focus."
    assert session.active is True
    daemon._process_utterance(audio)  # "seven." — out of range
    assert session.active is True
    assert captured == []
    daemon._process_utterance(audio)  # "one." — valid
    assert session.active is False
    assert captured == [{"target": "", "_hwnd": 11}]
