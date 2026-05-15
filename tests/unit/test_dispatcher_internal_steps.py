"""Dispatcher suppresses FeedbackSink / EventBus surfacing for internal steps."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import FeedbackSink
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry


class _RecordingFeedback:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def on_recording_start(self) -> None:
        self.calls.append(("on_recording_start", ()))

    def on_recording_stop(self) -> None:
        self.calls.append(("on_recording_stop", ()))

    def on_transcript(self, text: str, confidence: float) -> None:
        self.calls.append(("on_transcript", (text, confidence)))

    def on_match(self, tool: str, phrase: str, score: float) -> None:
        self.calls.append(("on_match", (tool, phrase, score)))

    def on_session_start(self) -> None:
        self.calls.append(("on_session_start", ()))

    def on_session_end(self) -> None:
        self.calls.append(("on_session_end", ()))

    def on_plan_start(self, transcript: str, step_count: int) -> None:
        self.calls.append(("on_plan_start", (transcript, step_count)))

    def on_plan_complete(self, transcript: str, steps_executed: int) -> None:
        self.calls.append(("on_plan_complete", (transcript, steps_executed)))

    def on_miss(self, transcript: str, candidates: Sequence[tuple[str, str, float]]) -> None:
        self.calls.append(("on_miss", (transcript,)))

    def on_error(self, where: str, exc: BaseException) -> None:
        self.calls.append(("on_error", (where, type(exc).__name__)))


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


def _registry_with(*entries: tuple[str, Any]) -> ToolRegistry:
    reg = ToolRegistry()
    for name, fn in entries:
        reg.register(ToolEntry(
            name=name,
            phrases=(),
            func=fn,
            module="m",
            docstring=None,
            settle_ms=0,
            internal=False,
        ))
    return reg


def test_internal_step_does_not_publish_tool_fired():
    calls: list[str] = []

    def _click(**_kw: Any) -> None:
        calls.append("click")

    def _wait(*, ms: int) -> None:
        pass

    reg = _registry_with(("click", _click), ("wait", _wait))
    fb = _RecordingFeedback()
    bus = _RecordingBus()
    dsp = Dispatcher(feedback=fb, event_bus=bus)

    plan = Plan(
        steps=(
            ToolCall(name="click", kwargs={}),
            ToolCall(name="wait", kwargs={"ms": 255}, internal=True),
            ToolCall(name="click", kwargs={}),
        ),
        raw_response={"router": "chain"},
    )
    outcome = dsp.run_plan("chain click click", plan, reg)

    fired = [e for e in bus.events if e[0] == "tool_fired"]
    assert len(fired) == 2, f"expected 2 tool_fired events, got {bus.events}"
    assert all(e[1]["name"] == "click" for e in fired)
    assert outcome.status == "ok"


def test_internal_step_excluded_from_on_plan_start_count():
    def _click(**_kw: Any) -> None: ...
    def _wait(*, ms: int) -> None: ...

    reg = _registry_with(("click", _click), ("wait", _wait))
    fb = _RecordingFeedback()
    dsp = Dispatcher(feedback=fb, event_bus=None)

    plan = Plan(
        steps=(
            ToolCall(name="click", kwargs={}),
            ToolCall(name="wait", kwargs={"ms": 255}, internal=True),
            ToolCall(name="click", kwargs={}),
        ),
        raw_response={"router": "chain"},
    )
    dsp.run_plan("chain click click", plan, reg)

    starts = [c for c in fb.calls if c[0] == "on_plan_start"]
    assert starts == [("on_plan_start", ("chain click click", 2))]


def test_internal_step_still_executes(monkeypatch: pytest.MonkeyPatch):
    called: list[int] = []

    def _wait(*, ms: int) -> None:
        called.append(ms)

    reg = _registry_with(("wait", _wait))
    dsp = Dispatcher(feedback=_RecordingFeedback(), event_bus=None)

    plan = Plan(
        steps=(ToolCall(name="wait", kwargs={"ms": 255}, internal=True),),
        raw_response={"router": "chain"},
    )
    dsp.run_plan("internal-only", plan, reg)
    assert called == [255]
