"""End-to-end: VerbRouter("chain ...") -> Plan -> Dispatcher.run_plan."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from voice_commander.chain import ChainParser, INTER_STEP_MS
from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.verb_router import VerbRouter, build_default_rules


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


def _registry_with_click_and_wait() -> tuple[ToolRegistry, list[str]]:
    calls: list[str] = []
    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="click",
            phrases=(),
            func=lambda: calls.append("click"),
            module="test",
            docstring=None,
            settle_ms=0,
        )
    )
    reg.register(
        ToolEntry(
            name="wait",
            phrases=(),
            func=lambda ms: calls.append(f"wait:{ms}"),
            module="test",
            docstring=None,
            settle_ms=0,
        )
    )
    return reg, calls


def test_chain_click_click_routes_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    reg, calls = _registry_with_click_and_wait()
    rules = build_default_rules()
    router = VerbRouter(rules, registry=reg, chain_parser=ChainParser(reg, rules))

    plan = router.route("chain click click")
    assert plan is not None

    fb = CapturingFeedbackSink()
    bus = _RecordingBus()
    sleeps: list[float] = []
    monkeypatch.setattr("voice_commander.dispatcher.time.sleep", lambda s: sleeps.append(s))

    dsp = Dispatcher(feedback=fb, event_bus=bus)
    outcome = dsp.run_plan("chain click click", plan, reg)

    assert outcome.status == "ok"
    # Execution order: click, wait(255), click
    assert calls == ["click", f"wait:{INTER_STEP_MS}", "click"]
    # HUD count excludes the internal wait step — visible_steps == 2
    plan_start_calls = [args for name, args in fb.calls if name == "on_plan_start"]
    assert len(plan_start_calls) == 1
    assert plan_start_calls[0] == ("chain click click", 2)
    # Only two tool_fired events (internal wait is suppressed)
    fired = [e for e in bus.events if e[0] == "tool_fired"]
    assert [e[1]["name"] for e in fired] == ["click", "click"]


def test_chain_open_click_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    reg, _ = _registry_with_click_and_wait()
    rules = build_default_rules()
    router = VerbRouter(rules, registry=reg, chain_parser=ChainParser(reg, rules))
    # "open" is in _FORBIDDEN so ChainParser.parse returns None -> route returns None
    assert router.route("chain open click") is None
