"""Tests for Summarizer — hybrid rule-first, LLM fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

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


def _summarizer(llm=None, enabled=True) -> Summarizer:
    return Summarizer(
        rules=RULES,
        chain_detectors=CHAIN_DETECTORS,
        llm_client=llm,
        llm_fallback_enabled=enabled,
    )


def test_miss_returns_no_match_without_llm():
    llm = MagicMock()
    s = _summarizer(llm=llm)
    assert s.summarize(_outcome([], status="miss")) == "no match"
    llm.summarize.assert_not_called()


def test_single_step_ok_uses_rule():
    llm = MagicMock()
    s = _summarizer(llm=llm)
    out = _outcome([ToolCall("minimize", {})])
    assert s.summarize(out) == "minimized window"
    llm.summarize.assert_not_called()


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
    # No chain detector matches — last-step-wins -> "pressed ctrl+v"
    assert s.summarize(_outcome(steps)) == "pressed ctrl+v"


def test_unknown_verb_triggers_llm():
    llm = MagicMock()
    llm.summarize.return_value = "did a thing"
    s = _summarizer(llm=llm)
    out = _outcome([ToolCall("reticulate_splines", {})])
    assert s.summarize(out) == "did a thing"
    llm.summarize.assert_called_once()


def test_error_triggers_llm():
    llm = MagicMock()
    llm.summarize.return_value = "minimize failed"
    s = _summarizer(llm=llm)
    out = _outcome(
        [ToolCall("minimize", {})],
        status="error",
        failed_index=0,
        error_msg="FocusWindowError",
    )
    assert s.summarize(out) == "minimize failed"


def test_error_with_llm_none_falls_back_to_raw():
    llm = MagicMock()
    llm.summarize.return_value = None
    s = _summarizer(llm=llm)
    out = _outcome(
        [ToolCall("minimize", {})],
        status="error",
        failed_index=0,
        error_msg="x",
    )
    assert s.summarize(out) == "minimize failed"


def test_error_with_unknown_failed_step_uses_generic():
    llm = MagicMock()
    llm.summarize.return_value = None
    s = _summarizer(llm=llm)
    out = _outcome([ToolCall("foo", {})], status="error", failed_index=0, error_msg="x")
    assert s.summarize(out) == "foo failed"


def test_llm_disabled_skips_llm_on_unknown_verb():
    llm = MagicMock()
    s = _summarizer(llm=llm, enabled=False)
    out = _outcome([ToolCall("reticulate_splines", {})])
    result = s.summarize(out)
    # No LLM call; must produce *something* deterministic.
    llm.summarize.assert_not_called()
    assert result == "reticulate_splines"


def test_no_llm_client_still_works():
    s = _summarizer(llm=None)
    assert s.summarize(_outcome([ToolCall("minimize", {})])) == "minimized window"
