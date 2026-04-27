"""Tests for Summarizer — rule-table only (no LLM call)."""

from __future__ import annotations

from voice_commander.plan import PlanOutcome, ToolCall
from voice_sprite.summarizer import Summarizer
from voice_sprite.summary_rules import CHAIN_DETECTORS, RULES


def _outcome(steps, status="ok", error_msg=None, failed_index=None):
    return PlanOutcome(
        transcript="t",
        steps=tuple(steps),
        status=status,
        failed_step_index=failed_index,
        error_msg=error_msg,
        duration_ms=10,
    )


def _summarizer() -> Summarizer:
    return Summarizer(rules=RULES, chain_detectors=CHAIN_DETECTORS)


def test_miss_returns_no_match():
    s = _summarizer()
    assert s.summarize(_outcome([], status="miss")) == "no match"


def test_single_step_ok_uses_rule():
    s = _summarizer()
    out = _outcome([ToolCall("minimize", {})])
    assert s.summarize(out) == "minimized window"


def test_chain_detector_wins_over_last_step_rule():
    steps = [
        ToolCall("focus", {"target": "chrome"}),
        ToolCall("press", {"combo": "ctrl+t"}),
        ToolCall("press", {"combo": "ctrl+l"}),
        ToolCall("type", {"text": "cats"}),
        ToolCall("press", {"combo": "enter"}),
    ]
    s = _summarizer()
    assert s.summarize(_outcome(steps)) == 'searched chrome for "cats"'


def test_multi_step_all_in_rules_uses_last_step():
    steps = [
        ToolCall("focus", {"target": "notepad"}),
        ToolCall("press", {"combo": "ctrl+v"}),
    ]
    s = _summarizer()
    assert s.summarize(_outcome(steps)) == "pressed ctrl+v"


def test_unknown_verb_uses_default_rule():
    s = _summarizer()
    out = _outcome([ToolCall("reticulate_splines", {})])
    assert s.summarize(out) == "reticulate splines"


def test_user_command_in_rules():
    s = _summarizer()
    assert s.summarize(_outcome([ToolCall("new_tab", {})])) == "opened new tab"
    assert s.summarize(_outcome([ToolCall("copy", {})])) == "copied"


def test_error_returns_failed_step_name():
    s = _summarizer()
    out = _outcome(
        [ToolCall("minimize", {})],
        status="error",
        failed_index=0,
        error_msg="FocusWindowError",
    )
    assert s.summarize(out) == "minimize failed"


def test_error_with_unknown_failed_step():
    s = _summarizer()
    out = _outcome([ToolCall("foo", {})], status="error", failed_index=0, error_msg="x")
    assert s.summarize(out) == "foo failed"


def test_error_with_no_failed_index_returns_generic():
    s = _summarizer()
    out = _outcome([ToolCall("minimize", {})], status="error")
    assert s.summarize(out) == "command failed"
