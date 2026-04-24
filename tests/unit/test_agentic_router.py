"""Unit tests for AgenticRouter — the step-observe-decide agentic fallback loop.

LM Studio (httpx) and perception tools are fully mocked. No pywin32, no CUDA,
no running LM Studio instance required.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from voice_commander.agentic_router import AgenticRouter
from voice_commander.config import LLMConfig
from voice_commander.dispatcher import Dispatcher
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.plan import Plan, PlanOutcome, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_TOOL_NAMES = [
    "focus",
    "press",
    "type",
    "done",
    "ask_user",
    "get_focused_window",
    "list_windows",
    "get_clipboard",
    "close_window",
    "no_match",
]


def _make_registry() -> ToolRegistry:
    """Build a minimal ToolRegistry with all the tool names the agentic loop cares about."""
    reg = ToolRegistry()
    for name in _TOOL_NAMES:
        entry = ToolEntry(
            name=name,
            phrases=(),
            func=lambda **kw: None,
            module="test",
            docstring=None,
            enabled=True,
            llm_only=True,
            params_schema={
                "type": "function",
                "function": {"name": name, "parameters": {}},
            },
        )
        reg.register(entry)
    return reg


def _make_dispatcher() -> Dispatcher:
    """Return a Dispatcher backed by a CapturingFeedbackSink."""
    return Dispatcher(feedback=CapturingFeedbackSink())


def _make_llm_response(tool_name: str, tool_args: dict) -> dict:
    """Build a minimal OpenAI-style chat completion response dict."""
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args),
                            },
                        }
                    ]
                }
            }
        ]
    }


def _mock_response(data: dict) -> MagicMock:
    """Create a mock httpx.Response that returns *data* from .json()."""
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _make_router(
    registry: ToolRegistry | None = None,
    dispatcher: Dispatcher | None = None,
    event_bus: EventBus | None = None,
    max_steps: int = 6,
) -> AgenticRouter:
    """Build an AgenticRouter with mocked HTTP client and sensible defaults."""
    router = AgenticRouter(
        config=LLMConfig(),
        registry=registry or _make_registry(),
        dispatcher=dispatcher or _make_dispatcher(),
        event_bus=event_bus,
        max_steps=max_steps,
    )
    return router


_FAKE_ENV = "Focused window: {}\nVisible windows (0): []"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_terminates_on_done_success():
    """LLM emitting done(success=true) returns PlanOutcome with status='ok'."""
    router = _make_router()
    router._client = MagicMock()
    router._client.post.return_value = _mock_response(
        _make_llm_response("done", {"success": True})
    )

    with patch.object(router, "_gather_context", return_value=_FAKE_ENV):
        outcome = router.run("open notepad")

    assert isinstance(outcome, PlanOutcome)
    assert outcome.status == "ok"
    assert outcome.transcript == "open notepad"


def test_run_terminates_on_done_failure():
    """LLM emitting done(success=false) returns PlanOutcome with status='miss'."""
    router = _make_router()
    router._client = MagicMock()
    router._client.post.return_value = _mock_response(
        _make_llm_response("done", {"success": False})
    )

    with patch.object(router, "_gather_context", return_value=_FAKE_ENV):
        outcome = router.run("impossible task")

    assert outcome.status == "miss"


def test_run_step_cap_returns_miss():
    """When the LLM never emits done/ask_user within max_steps, return status='miss'
    with an error_msg containing 'step cap'."""
    router = _make_router(max_steps=2)
    router._client = MagicMock()
    # Always return a perception tool so the loop keeps going without terminating.
    router._client.post.return_value = _mock_response(
        _make_llm_response("get_focused_window", {})
    )

    with patch.object(router, "_gather_context", return_value=_FAKE_ENV):
        outcome = router.run("do something forever")

    assert outcome.status == "miss"
    assert outcome.error_msg is not None
    assert "step cap" in outcome.error_msg.lower()
    # post() called exactly max_steps times
    assert router._client.post.call_count == 2


def test_run_terminates_on_ask_user():
    """LLM emitting ask_user() returns status='ok' and publishes an 'ask_user' event."""
    bus = EventBus()
    router = _make_router(event_bus=bus)
    router._client = MagicMock()
    router._client.post.return_value = _mock_response(
        _make_llm_response(
            "ask_user",
            {"question": "Which terminal?", "options": "1. A, 2. B"},
        )
    )

    with patch.object(router, "_gather_context", return_value=_FAKE_ENV):
        outcome = router.run("focus terminal")

    assert outcome.status == "ok"
    events = bus.replay_after(0)
    assert any(e.type == "ask_user" for e in events), (
        f"Expected 'ask_user' event in bus; got: {[e.type for e in events]}"
    )
    ask_event = next(e for e in events if e.type == "ask_user")
    assert ask_event.data["question"] == "Which terminal?"


def test_run_perception_tool_executed_inline():
    """Perception tools (e.g. list_windows) are called directly via registry, not Dispatcher."""
    registry = _make_registry()

    perception_calls: list[str] = []

    def fake_list_windows(**kw):
        perception_calls.append("list_windows")
        return []

    # Replace the registry entry's func with our spy.
    registry.by_name("list_windows").func = fake_list_windows  # type: ignore[union-attr]

    mock_dispatcher = MagicMock(spec=Dispatcher)
    router = _make_router(registry=registry, dispatcher=mock_dispatcher)
    router._client = MagicMock()

    # Step 1 → list_windows(), Step 2 → done(success=true)
    router._client.post.side_effect = [
        _mock_response(_make_llm_response("list_windows", {})),
        _mock_response(_make_llm_response("done", {"success": True})),
    ]

    with patch.object(router, "_gather_context", return_value=_FAKE_ENV):
        outcome = router.run("show windows then quit")

    assert outcome.status == "ok"
    assert perception_calls == ["list_windows"], (
        "Expected list_windows to be called exactly once inline"
    )
    # Dispatcher must NOT have been called for the perception step.
    mock_dispatcher.run_plan.assert_not_called()


def test_run_action_tool_dispatched():
    """Action tools (e.g. focus) are dispatched via Dispatcher.run_plan, not called inline."""
    mock_dispatcher = MagicMock(spec=Dispatcher)
    # Dispatcher.run_plan must return a PlanOutcome so the router can inspect .status
    mock_dispatcher.run_plan.return_value = PlanOutcome(
        transcript="focus chrome",
        steps=(ToolCall(name="focus", kwargs={"target": "chrome"}),),
        status="ok",
        failed_step_index=None,
        error_msg=None,
        duration_ms=10,
    )

    router = _make_router(dispatcher=mock_dispatcher)
    router._client = MagicMock()

    # Step 1 → focus(target="chrome"), Step 2 → done(success=true)
    router._client.post.side_effect = [
        _mock_response(_make_llm_response("focus", {"target": "chrome"})),
        _mock_response(_make_llm_response("done", {"success": True})),
    ]

    with patch.object(router, "_gather_context", return_value=_FAKE_ENV):
        outcome = router.run("focus chrome")

    assert outcome.status == "ok"
    assert mock_dispatcher.run_plan.call_count == 1, (
        "Dispatcher.run_plan must be called exactly once for the focus step"
    )
    dispatched_plan: Plan = mock_dispatcher.run_plan.call_args[0][1]
    assert len(dispatched_plan.steps) == 1
    assert dispatched_plan.steps[0].name == "focus"
    assert dispatched_plan.steps[0].kwargs == {"target": "chrome"}


def test_run_llm_error_returns_error_outcome():
    """httpx.ConnectError during any step returns PlanOutcome with status='error'."""
    router = _make_router()
    router._client = MagicMock()
    router._client.post.side_effect = httpx.ConnectError("connection refused")

    with patch.object(router, "_gather_context", return_value=_FAKE_ENV):
        outcome = router.run("open spotify")

    assert outcome.status == "error"
    assert outcome.error_msg is not None
    assert "LLM error" in outcome.error_msg


def test_build_tools_excludes_close_window_and_no_match():
    """_build_tools_array() must exclude close_window and no_match (agentic restrictions)."""
    router = _make_router()
    tools = router._build_tools_array()
    names = {t["function"]["name"] for t in tools}

    assert "close_window" not in names, (
        "close_window must be excluded from the agentic tools array"
    )
    assert "no_match" not in names, (
        "no_match must be excluded from the agentic tools array"
    )
    # Unrestricted tools should still be present.
    assert "focus" in names
    assert "done" in names


def test_metrics_increment():
    """After one run, metrics reflect total_runs=1 and total_steps >= 1."""
    router = _make_router()
    router._client = MagicMock()
    router._client.post.return_value = _mock_response(
        _make_llm_response("done", {"success": True})
    )

    with patch.object(router, "_gather_context", return_value=_FAKE_ENV):
        router.run("test metrics")

    m = router.metrics
    assert m["total_runs"] == 1, f"Expected total_runs=1, got {m['total_runs']}"
    assert m["total_steps"] >= 1, f"Expected total_steps>=1, got {m['total_steps']}"
    assert "avg_steps_per_run" in m


def test_gather_context_includes_clipboard_for_paste():
    """_gather_context fetches clipboard when the transcript contains 'paste'.

    _gather_context does inline `from .tools.perception import ...` so the
    correct patch target is the function on the source module, not on
    agentic_router (which never imports them at module level).
    """
    router = _make_router()

    with (
        patch(
            "voice_commander.tools.perception.get_focused_window",
            return_value={"hwnd": 0, "title": "Notepad", "process": "notepad.exe"},
        ),
        patch(
            "voice_commander.tools.perception.list_windows",
            return_value=[],
        ),
        patch(
            "voice_commander.tools.perception.get_clipboard",
            return_value={"text": "hello from clipboard"},
        ),
    ):
        context = router._gather_context("paste this text")

    assert "Clipboard" in context, (
        f"Expected 'Clipboard' key in context for paste transcript; got:\n{context}"
    )


def test_gather_context_skips_clipboard_normally():
    """_gather_context does NOT fetch clipboard for non-clipboard transcripts."""
    router = _make_router()

    clipboard_fetched: list[bool] = []

    def spy_get_clipboard():
        clipboard_fetched.append(True)
        return {"text": "should not be called"}

    with (
        patch(
            "voice_commander.tools.perception.get_focused_window",
            return_value={"hwnd": 0, "title": "Chrome", "process": "chrome.exe"},
        ),
        patch(
            "voice_commander.tools.perception.list_windows",
            return_value=[],
        ),
        patch(
            "voice_commander.tools.perception.get_clipboard",
            side_effect=spy_get_clipboard,
        ),
    ):
        context = router._gather_context("open chrome")

    assert not clipboard_fetched, (
        "get_clipboard must NOT be called when transcript has no clipboard keywords"
    )
    assert "Clipboard" not in context
