"""Unit tests for ChainParser."""

from __future__ import annotations

from voice_commander.chain import ChainParser, INTER_STEP_MS
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
    assert plan.raw_response == {"router": "chain", "tokens": ["click", "click"]}


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


# ---------------------------------------------------------------------------
# Multi-word + tie-break helpers
# ---------------------------------------------------------------------------


def _registry_with_command(name: str, phrases: tuple[str, ...] = ()) -> ToolRegistry:
    """Build a registry with a single fake command entry."""
    reg = ToolRegistry()
    entry = ToolEntry(
        name=name,
        phrases=phrases,
        func=lambda: None,
        module="test",
        docstring=None,
        settle_ms=0,
        origin="command",
        enabled=True,
    )
    reg.register(entry)
    return reg


# ---------------------------------------------------------------------------
# Multi-word + tie-break tests
# ---------------------------------------------------------------------------


def test_multiword_command_resolved_inside_chain():
    reg = _registry_with_command("close_window")
    plan = _parser(reg).parse("click close window click")
    assert plan is not None
    names = [s.name for s in plan.steps]
    # click, wait, close_window, wait, click
    assert names == ["click", "wait", "close_window", "wait", "click"]


def test_registered_command_beats_primitive_at_equal_token_count():
    # Author registers a command literally called "click". The chain parser
    # must dispatch the registered command, not the primitive click.
    reg = _registry_with_command("click")
    plan = _parser(reg).parse("click click")
    assert plan is not None
    # Both steps should target the registered command (priority tie-break),
    # not the primitive's default_target.
    assert plan.steps[0].name == "click"
    assert plan.steps[0].kwargs == {}  # registered command takes no kwargs
    assert plan.steps[2].name == "click"
    assert plan.steps[2].kwargs == {}


def test_longest_match_wins_over_shorter():
    # If both "close" and "close_window" are registered, "close window" in
    # the utterance must bind to close_window.
    reg = ToolRegistry()
    reg.register(ToolEntry(name="close", phrases=(), func=lambda: None,
                           module="test", docstring=None, settle_ms=0,
                           origin="command", enabled=True))
    reg.register(ToolEntry(name="close_window", phrases=(), func=lambda: None,
                           module="test", docstring=None, settle_ms=0,
                           origin="command", enabled=True))
    plan = _parser(reg).parse("click close window")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["click", "wait", "close_window"]


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


def test_forbidden_verb_mid_chain_rejected():
    plan = _parser().parse("click open click")
    assert plan is None


def test_forbidden_verb_at_start_rejected():
    plan = _parser().parse("type click click")
    assert plan is None


def test_unknown_token_mid_chain_rejected():
    plan = _parser().parse("click nonsense click")
    assert plan is None


def test_empty_tail_rejected():
    assert _parser().parse("") is None
    assert _parser().parse("   ") is None


def test_single_token_rejected():
    # A one-step chain is meaningless; user must mean >=2 commands.
    assert _parser().parse("click") is None


def test_chain_inside_chain_rejected():
    # The head "chain" must never appear in the tail.
    assert _parser().parse("click chain click") is None


def test_tabs_token_rejected():
    # tabs is a picker-only primitive — forbidden in chain.
    assert _parser().parse("click tabs click") is None
