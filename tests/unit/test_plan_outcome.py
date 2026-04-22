"""Tests for PlanOutcome dataclass — SSE event serialization round-trip."""

from __future__ import annotations

import dataclasses

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
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        outcome.status = "ok"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Malformed-input tests — document the exception contract of from_event_dict
# ---------------------------------------------------------------------------

_VALID_BASE: dict = {
    "transcript": "copy",
    "steps": [{"name": "copy", "kwargs": {}}],
    "status": "ok",
    "failed_step_index": None,
    "error_msg": None,
    "duration_ms": 50,
}


@pytest.mark.parametrize(
    "bad_dict, exc_type",
    [
        pytest.param(
            {"steps": [], "status": "ok", "duration_ms": 0},
            KeyError,
            id="missing_transcript",
        ),
        pytest.param(
            {"transcript": "hi", "steps": [], "duration_ms": 0},
            KeyError,
            id="missing_status",
        ),
        pytest.param(
            {"transcript": "hi", "steps": 42, "status": "ok", "duration_ms": 0},
            TypeError,
            id="steps_not_a_list",
        ),
        pytest.param(
            {"transcript": "hi", "steps": [{"kwargs": {}}], "status": "ok", "duration_ms": 0},
            KeyError,
            id="step_missing_name",
        ),
        pytest.param(
            {
                "transcript": "hi",
                "steps": [{"name": "copy", "kwargs": 99}],
                "status": "ok",
                "duration_ms": 0,
            },
            TypeError,
            id="step_kwargs_not_a_dict",
        ),
        pytest.param(
            {"transcript": "hi", "steps": [], "status": "ok", "duration_ms": "bad"},
            ValueError,
            id="duration_ms_non_numeric",
        ),
        pytest.param(
            {},
            KeyError,
            id="empty_dict",
        ),
    ],
)
def test_from_event_dict_malformed(bad_dict: dict, exc_type: type[Exception]) -> None:
    """from_event_dict raises the documented exception type for each malformed payload."""
    with pytest.raises(exc_type):
        PlanOutcome.from_event_dict(bad_dict)


@pytest.mark.parametrize(
    "bad_dict",
    [
        pytest.param({"steps": [], "status": "ok", "duration_ms": 0}, id="missing_transcript"),
        pytest.param({"transcript": "hi", "steps": [], "duration_ms": 0}, id="missing_status"),
        pytest.param(
            {"transcript": "hi", "steps": 42, "status": "ok", "duration_ms": 0},
            id="steps_not_a_list",
        ),
        pytest.param({}, id="empty_dict"),
    ],
)
def test_try_from_event_dict_returns_none_on_malformed(bad_dict: dict) -> None:
    """try_from_event_dict returns None rather than raising on bad input."""
    assert PlanOutcome.try_from_event_dict(bad_dict) is None


def test_try_from_event_dict_returns_outcome_on_valid_input() -> None:
    """try_from_event_dict returns a populated PlanOutcome for a well-formed dict."""
    result = PlanOutcome.try_from_event_dict(_VALID_BASE)
    assert result is not None
    assert result.transcript == "copy"
    assert result.status == "ok"
    assert len(result.steps) == 1
    assert result.steps[0].name == "copy"
