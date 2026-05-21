"""Unit tests for repeat-count parsing, expansion, and router wiring (ADR 0098)."""

import pytest

from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.repeat import (
    MAX_REPEAT,
    REPEAT_SETTLE_MS,
    expand_repeat,
    parse_repeat_suffix,
)
from voice_commander.verb_router import VerbRouter, build_default_rules

# ---------------------------------------------------------------------------
# parse_repeat_suffix — recognised forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("go down twice", ("go down", 2)),
        ("go down thrice", ("go down", 3)),
        ("scroll up twice", ("scroll up", 2)),
        ("scroll down four times", ("scroll down", 4)),
        ("scroll down two times", ("scroll down", 2)),
        ("scroll down 4 times", ("scroll down", 4)),
        ("press enter two times", ("press enter", 2)),
        ("go down one time", ("go down", 1)),
        ("go down once", ("go down", 1)),
        ("go down ten times", ("go down", 10)),
        ("go down twenty times", ("go down", 20)),
    ],
)
def test_parse_recognised_forms(text, expected):
    assert parse_repeat_suffix(text) == expected


def test_parse_is_case_insensitive_on_modifier_but_preserves_base_case():
    # The modifier matches case-insensitively; the base keeps its casing so the
    # `type` primitive's text argument is preserved verbatim.
    assert parse_repeat_suffix("type Hello World twice") == ("type Hello World", 2)
    assert parse_repeat_suffix("Scroll Down THRICE") == ("Scroll Down", 3)
    assert parse_repeat_suffix("type Foo Three Times") == ("type Foo", 3)


def test_parse_digit_count():
    assert parse_repeat_suffix("scroll down 3 times") == ("scroll down", 3)


# ---------------------------------------------------------------------------
# parse_repeat_suffix — non-matches (must be non-destructive → None)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "scroll down",  # no modifier
        "twice",  # modifier but empty base
        "thrice",
        "once",
        "times",  # bare "times", no number, no base
        "end times",  # "times" with a non-number word before it
        "save game time",  # "time" preceded by a non-number
        "workspace 2",  # trailing digit but no "times" → not a repeat
        "go down zero times",  # count 0 → out of [1, MAX]
    ],
)
def test_parse_non_matches_return_none(text):
    assert parse_repeat_suffix(text) is None


def test_parse_rejects_count_above_max():
    assert parse_repeat_suffix(f"go down {MAX_REPEAT} times") is not None
    assert parse_repeat_suffix(f"go down {MAX_REPEAT + 1} times") is None


# ---------------------------------------------------------------------------
# expand_repeat
# ---------------------------------------------------------------------------


def _base_plan(*names: str) -> Plan:
    return Plan(
        steps=tuple(ToolCall(name=n, kwargs={}) for n in names),
        raw_response={"router": "verb"},
    )


def test_expand_none_base_returns_none():
    assert expand_repeat(None, 2) is None


def test_expand_count_one_returns_base_unchanged():
    base = _base_plan("click")
    assert expand_repeat(base, 1) is base


def test_expand_interleaves_internal_wait_separators():
    plan = expand_repeat(_base_plan("click"), 3)
    assert plan is not None
    assert [s.name for s in plan.steps] == ["click", "wait", "click", "wait", "click"]
    waits = [s for s in plan.steps if s.name == "wait"]
    assert all(s.internal for s in waits)
    assert all(s.kwargs == {"ms": REPEAT_SETTLE_MS} for s in waits)


def test_expand_repeats_multi_step_base_blockwise():
    # A two-step base repeated twice → block, wait, block.
    plan = expand_repeat(_base_plan("copy", "paste"), 2)
    assert plan is not None
    assert [s.name for s in plan.steps] == ["copy", "paste", "wait", "copy", "paste"]


def test_expand_records_repeat_metadata():
    base = _base_plan("click")
    plan = expand_repeat(base, 2)
    assert plan is not None
    assert plan.raw_response["router"] == "repeat"
    assert plan.raw_response["count"] == 2
    assert plan.raw_response["base"] is base.raw_response


def test_expand_inherits_strict_flag():
    base = Plan(steps=(ToolCall(name="click", kwargs={}),), raw_response={}, strict=False)
    plan = expand_repeat(base, 2)
    assert plan is not None
    assert plan.strict is False


@pytest.mark.parametrize("synthetic", ["__picker.open", "__dictation.start"])
def test_expand_rejects_synthetic_intercept_steps(synthetic):
    base = Plan(steps=(ToolCall(name=synthetic, kwargs={}),), raw_response={})
    assert expand_repeat(base, 2) is None


def test_expand_rejects_empty_step_plan():
    assert expand_repeat(Plan(steps=(), raw_response={}), 2) is None


# ---------------------------------------------------------------------------
# VerbRouter.route integration — primitives
# ---------------------------------------------------------------------------


def _router() -> VerbRouter:
    return VerbRouter(build_default_rules())


def test_route_scroll_down_twice():
    plan = _router().route("scroll down twice")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["scroll", "wait", "scroll"]
    assert plan.steps[0].kwargs == {"direction": "down"}
    assert plan.steps[2].kwargs == {"direction": "down"}
    assert plan.steps[1].internal is True
    assert plan.raw_response["count"] == 2


def test_route_scroll_up_thrice():
    plan = _router().route("scroll up thrice")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["scroll", "wait", "scroll", "wait", "scroll"]
    assert all(s.kwargs == {"direction": "up"} for s in plan.steps if s.name == "scroll")


def test_route_scroll_down_four_times():
    plan = _router().route("scroll down four times")
    assert plan is not None
    assert [s.name for s in plan.steps].count("scroll") == 4


def test_route_click_twice_double_clicks():
    plan = _router().route("click twice")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["click", "wait", "click"]


def test_route_type_preserves_text_with_count():
    plan = _router().route("type Hello twice")
    assert plan is not None
    typed = [s for s in plan.steps if s.name == "type"]
    assert len(typed) == 2
    assert all(s.kwargs == {"text": "Hello"} for s in typed)


def test_route_no_modifier_unaffected():
    # Regression guard: a plain command must route exactly as before.
    plan = _router().route("scroll down")
    assert plan is not None
    assert plan.steps == (ToolCall(name="scroll", kwargs={"direction": "down"}),)


def test_route_count_one_runs_once():
    plan = _router().route("scroll down once")
    assert plan is not None
    assert plan.steps == (ToolCall(name="scroll", kwargs={"direction": "down"}),)


def test_route_repeat_on_unknown_base_misses():
    assert _router().route("frobnicate twice") is None


def test_route_bare_focus_twice_misses_with_picker():
    # A bare picker target cannot be repeated → miss.
    from voice_commander.picker.registry import BarePickerRegistry

    reg = BarePickerRegistry()
    reg.register("focus", lambda: [])
    router = VerbRouter(build_default_rules(), picker_registry=reg)
    assert router.route("focus twice") is None


def test_route_dictate_twice_misses():
    assert _router().route("dictate twice") is None


# ---------------------------------------------------------------------------
# VerbRouter.route integration — registered commands
# ---------------------------------------------------------------------------


def _registry_with(name: str, *, origin: str = "command"):
    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name=name,
            phrases=(),
            func=lambda: None,
            module="test",
            docstring=None,
            enabled=True,
            origin=origin,
        )
    )
    return reg


def test_route_registered_command_twice():
    reg = _registry_with("go down")
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("go down twice")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["go down", "wait", "go down"]


def test_route_underscore_command_thrice():
    reg = _registry_with("go_down")
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("go down thrice")
    assert plan is not None
    assert [s.name for s in plan.steps].count("go_down") == 3


def test_literal_command_named_with_count_word_wins_over_repeat():
    # A command literally named "go down twice" must beat the repeat
    # interpretation, because the full-text registered match runs first.
    reg = _registry_with("go down twice")
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("go down twice")
    assert plan is not None
    assert plan.steps == (ToolCall(name="go down twice", kwargs={}),)
