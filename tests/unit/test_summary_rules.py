"""Tests for nine-verb rule table + chain detectors."""

from __future__ import annotations

from voice_commander.plan import PlanOutcome, ToolCall
from voice_sprite.summary_rules import CHAIN_DETECTORS, RULES, detect_search_chain


def _outcome(steps: tuple[ToolCall, ...], status: str = "ok") -> PlanOutcome:
    return PlanOutcome(
        transcript="t",
        steps=steps,
        status=status,  # type: ignore[arg-type]
        failed_step_index=None,
        error_msg=None,
        duration_ms=0,
    )


def test_every_catalog_verb_has_rule():
    required = {
        "focus",
        "close",
        "open",
        "type",
        "press",
        "wait",
        "click",
        "scroll",
        "no_match",
    }
    assert required <= set(RULES)


def test_rules_emit_nonempty_strings():
    samples = {
        "focus": {"target": "chrome"},
        "close": {},
        "open": {"target": "spotify"},
        "type": {"text": "hello world"},
        "press": {"combo": "ctrl+t"},
        "wait": {"ms": 250},
        "click": {},
        "scroll": {"direction": "down"},
        "no_match": {"reason": "n/a"},
    }
    outcome = _outcome(())
    for name, kwargs in samples.items():
        s = RULES[name](kwargs, outcome)
        assert isinstance(s, str) and len(s) > 0, f"rule {name} produced empty string"


def test_type_rule_truncates_long_text():
    outcome = _outcome(())
    s = RULES["type"]({"text": "a" * 80}, outcome)
    assert len(s) <= 40  # "typed \"<30-char-snip>\""


def test_detect_search_chain_canonical():
    steps = (
        ToolCall("focus", {"target": "chrome"}),
        ToolCall("press", {"combo": "ctrl+t"}),
        ToolCall("press", {"combo": "ctrl+l"}),
        ToolCall("type", {"text": "how to lose weight"}),
        ToolCall("press", {"combo": "enter"}),
    )
    assert detect_search_chain(steps) == 'searched chrome for "how to lose weight"'


def test_detect_search_chain_ignores_near_miss():
    steps = (
        ToolCall("focus", {"target": "chrome"}),
        ToolCall("press", {"combo": "ctrl+t"}),
        ToolCall("type", {"text": "foo"}),  # missing ctrl+l
    )
    assert detect_search_chain(steps) is None


def test_press_ctrl_c_reads_as_copied():
    out = _outcome((ToolCall("press", {"combo": "ctrl+c"}),))
    assert RULES["press"]({"combo": "ctrl+c"}, out) == "copied"


def test_press_ctrl_t_reads_as_opened_new_tab():
    out = _outcome((ToolCall("press", {"combo": "ctrl+t"}),))
    assert RULES["press"]({"combo": "ctrl+t"}, out) == "opened new tab"


def test_press_ctrl_w_reads_as_closed_tab():
    out = _outcome((ToolCall("press", {"combo": "ctrl+w"}),))
    assert RULES["press"]({"combo": "ctrl+w"}, out) == "closed tab"


def test_press_unknown_combo_falls_back():
    out = _outcome((ToolCall("press", {"combo": "ctrl+q"}),))
    assert RULES["press"]({"combo": "ctrl+q"}, out) == "pressed ctrl+q"


def test_chain_detectors_is_a_list():
    assert isinstance(CHAIN_DETECTORS, list)
    assert detect_search_chain in CHAIN_DETECTORS
