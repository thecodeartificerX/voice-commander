"""Tests for PlanOutcome dataclass — SSE event serialization round-trip."""

from __future__ import annotations

import pytest

from voice_commander.plan import PlanOutcome, ToolCall


def test_plan_outcome_ok_round_trip():
    outcome = PlanOutcome(
        transcript="minimize",
        steps=(ToolCall(name="minimize", kwargs={}),),
        status="ok",
        failed_step_index=None,
        error_msg=None,
        duration_ms=127,
    )
    d = outcome.to_event_dict()
    assert d == {
        "transcript": "minimize",
        "steps": [{"name": "minimize", "kwargs": {}}],
        "status": "ok",
        "failed_step_index": None,
        "error_msg": None,
        "duration_ms": 127,
    }
    restored = PlanOutcome.from_event_dict(d)
    assert restored == outcome


def test_plan_outcome_miss_round_trip():
    outcome = PlanOutcome(
        transcript="um hello",
        steps=(),
        status="miss",
        failed_step_index=None,
        error_msg=None,
        duration_ms=42,
    )
    restored = PlanOutcome.from_event_dict(outcome.to_event_dict())
    assert restored == outcome
    assert restored.steps == ()


def test_plan_outcome_error_round_trip():
    outcome = PlanOutcome(
        transcript="minimize",
        steps=(ToolCall(name="minimize", kwargs={}),),
        status="error",
        failed_step_index=0,
        error_msg="FocusWindowError: no visible window",
        duration_ms=310,
    )
    restored = PlanOutcome.from_event_dict(outcome.to_event_dict())
    assert restored == outcome
    assert restored.failed_step_index == 0


def test_plan_outcome_is_frozen():
    outcome = PlanOutcome(
        transcript="x",
        steps=(),
        status="miss",
        failed_step_index=None,
        error_msg=None,
        duration_ms=0,
    )
    with pytest.raises(Exception):
        outcome.status = "ok"  # type: ignore[misc]
