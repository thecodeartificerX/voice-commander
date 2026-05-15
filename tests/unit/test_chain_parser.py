"""Unit tests for ChainParser."""

from __future__ import annotations

from typing import Any

import pytest

from voice_commander.chain import ChainParser, INTER_STEP_MS
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.verb_router import build_default_rules


def _empty_registry() -> ToolRegistry:
    """Registry with no enabled command/workflow entries — primitives only."""
    return ToolRegistry()


def _parser(reg: ToolRegistry | None = None) -> ChainParser:
    return ChainParser(
        registry=reg if reg is not None else _empty_registry(),
        verb_rules=build_default_rules(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_two_nullary_primitives_plan_has_internal_wait():
    plan = _parser().parse("click click")
    assert plan is not None
    assert plan.strict is True
    names = [s.name for s in plan.steps]
    assert names == ["click", "wait", "click"]
    # The middle wait is internal; the others are not.
    assert plan.steps[0].internal is False
    assert plan.steps[1].internal is True
    assert plan.steps[1].kwargs == {"ms": INTER_STEP_MS}
    assert plan.steps[2].internal is False


def test_three_primitives_two_internal_waits():
    plan = _parser().parse("click scroll click")
    assert plan is not None
    names = [s.name for s in plan.steps]
    assert names == ["click", "wait", "scroll", "wait", "click"]
    assert [s.internal for s in plan.steps] == [False, True, False, True, False]


def test_scroll_uses_default_target_kwargs():
    plan = _parser().parse("scroll click")
    assert plan is not None
    assert plan.steps[0].name == "scroll"
    assert plan.steps[0].kwargs == {"direction": "down"}
