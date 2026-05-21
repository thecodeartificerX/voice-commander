"""End-to-end: VerbRouter("<base> twice|N times") -> Plan -> Dispatcher.run_plan.

Proves the repeat modifier (ADR 0098) drives the real Dispatcher to invoke the
target tool N times, with the synthetic wait separators executed but suppressed
from the HUD (no ``tool_fired`` events, excluded from the visible step count).
"""

from __future__ import annotations

from typing import Any

import pytest

from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.repeat import REPEAT_SETTLE_MS
from voice_commander.verb_router import VerbRouter, build_default_rules


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


def _registry() -> tuple[ToolRegistry, list[str]]:
    """Registry with a counting `scroll`, `wait`, and a user command `go down`."""
    calls: list[str] = []
    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="scroll",
            phrases=(),
            func=lambda direction="down": calls.append(f"scroll:{direction}"),
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
    reg.register(
        ToolEntry(
            name="go down",
            phrases=(),
            func=lambda: calls.append("go_down"),
            module="test",
            docstring=None,
            settle_ms=0,
            origin="command",
        )
    )
    return reg, calls


def _dispatch(router: VerbRouter, reg: ToolRegistry, utterance: str, monkeypatch):
    plan = router.route(utterance)
    assert plan is not None, f"expected {utterance!r} to route"
    fb = CapturingFeedbackSink()
    bus = _RecordingBus()
    monkeypatch.setattr("voice_commander.dispatcher.time.sleep", lambda s: None)
    outcome = Dispatcher(feedback=fb, event_bus=bus).run_plan(utterance, plan, reg)
    return outcome, fb, bus


def test_scroll_down_twice_fires_scroll_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    reg, calls = _registry()
    router = VerbRouter(build_default_rules(), registry=reg)

    outcome, fb, bus = _dispatch(router, reg, "scroll down twice", monkeypatch)

    assert outcome.status == "ok"
    # Real scroll fired twice; one internal wait separator between them.
    assert calls == ["scroll:down", f"wait:{REPEAT_SETTLE_MS}", "scroll:down"]
    # HUD count excludes the internal wait → 2 visible steps.
    plan_start = [args for name, args in fb.calls if name == "on_plan_start"]
    assert plan_start == [("scroll down twice", 2)]
    # Only the two real scrolls emit tool_fired; the wait is suppressed.
    fired = [e[1]["name"] for e in bus.events if e[0] == "tool_fired"]
    assert fired == ["scroll", "scroll"]


def test_scroll_down_four_times_fires_scroll_four_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg, calls = _registry()
    router = VerbRouter(build_default_rules(), registry=reg)

    outcome, _fb, bus = _dispatch(router, reg, "scroll down four times", monkeypatch)

    assert outcome.status == "ok"
    assert calls.count("scroll:down") == 4
    assert calls.count(f"wait:{REPEAT_SETTLE_MS}") == 3  # n-1 separators
    fired = [e[1]["name"] for e in bus.events if e[0] == "tool_fired"]
    assert fired == ["scroll"] * 4


def test_registered_command_go_down_thrice(monkeypatch: pytest.MonkeyPatch) -> None:
    reg, calls = _registry()
    router = VerbRouter(build_default_rules(), registry=reg)

    outcome, _fb, _bus = _dispatch(router, reg, "go down thrice", monkeypatch)

    assert outcome.status == "ok"
    assert calls.count("go_down") == 3


def test_repeat_miss_does_not_route(monkeypatch: pytest.MonkeyPatch) -> None:
    reg, _calls = _registry()
    router = VerbRouter(build_default_rules(), registry=reg)
    # Unknown base with a modifier → no plan at all (daemon would miss-chime).
    assert router.route("frobnicate twice") is None
