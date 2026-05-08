"""Unit tests for the primitives-only VerbRouter rule set."""

import pytest
from voice_commander.plan import Plan, ToolCall
from voice_commander.verb_router import VerbRouter, build_default_rules


def _router() -> VerbRouter:
    return VerbRouter(build_default_rules())


# ---------------------------------------------------------------------------
# click
# ---------------------------------------------------------------------------

def test_click_routes_to_click():
    plan = _router().route("click")
    assert plan is not None
    assert plan.steps == (ToolCall(name="click", kwargs={}),)


def test_click_tolerates_trailing_period():
    plan = _router().route("Click.")
    assert plan is not None
    assert plan.steps == (ToolCall(name="click", kwargs={}),)


# ---------------------------------------------------------------------------
# scroll
# ---------------------------------------------------------------------------

def test_scroll_default_down():
    plan = _router().route("scroll")
    assert plan is not None
    assert plan.steps == (ToolCall(name="scroll", kwargs={"direction": "down"}),)


def test_scroll_up_subcommand():
    plan = _router().route("scroll up")
    assert plan is not None
    assert plan.steps == (ToolCall(name="scroll", kwargs={"direction": "up"}),)


def test_scroll_down_subcommand():
    plan = _router().route("scroll down")
    assert plan is not None
    assert plan.steps == (ToolCall(name="scroll", kwargs={"direction": "down"}),)


# ---------------------------------------------------------------------------
# focus / open / type / press
# ---------------------------------------------------------------------------

def test_focus_with_tail():
    plan = _router().route("focus chrome")
    assert plan is not None
    assert plan.steps == (ToolCall(name="focus", kwargs={"target": "chrome"}),)


def test_open_with_tail():
    plan = _router().route("open notepad")
    assert plan is not None
    assert plan.steps == (ToolCall(name="open", kwargs={"target": "notepad"}),)


def test_type_with_tail():
    plan = _router().route("type hello world")
    assert plan is not None
    assert plan.steps == (ToolCall(name="type", kwargs={"text": "hello world"}),)


def test_press_with_tail():
    plan = _router().route("press control t")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "control t"}),)


def test_focus_without_tail_misses():
    assert _router().route("focus") is None


# ---------------------------------------------------------------------------
# wait — uses tail_coerce
# ---------------------------------------------------------------------------

def test_wait_numeric_tail_coerced_to_int():
    plan = _router().route("wait 500")
    assert plan is not None
    assert plan.steps == (ToolCall(name="wait", kwargs={"ms": 500}),)


def test_wait_unit_suffix_ms_stripped():
    plan = _router().route("wait 500 ms")
    assert plan is not None
    assert plan.steps == (ToolCall(name="wait", kwargs={"ms": 500}),)


def test_wait_unit_suffix_milliseconds_stripped():
    plan = _router().route("wait 500 milliseconds")
    assert plan is not None
    assert plan.steps == (ToolCall(name="wait", kwargs={"ms": 500}),)


def test_wait_without_tail_misses():
    assert _router().route("wait") is None


# ---------------------------------------------------------------------------
# fall-through
# ---------------------------------------------------------------------------

def test_unknown_verb_returns_none():
    assert _router().route("xyz") is None


def test_empty_returns_none():
    assert _router().route("") is None
    assert _router().route("   ") is None


def test_legacy_copy_no_longer_routes():
    """Semantic verbs are deleted — user must author them as graphs."""
    assert _router().route("copy") is None


def test_legacy_close_no_longer_routes():
    assert _router().route("close") is None


def test_legacy_minimize_no_longer_routes():
    assert _router().route("minimize") is None


def test_legacy_new_tab_no_longer_routes():
    assert _router().route("new tab") is None
