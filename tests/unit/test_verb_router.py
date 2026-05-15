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


def test_focus_without_tail_misses_when_no_picker_registered():
    """Bare verbs still miss unless a BarePickerRegistry is wired in (Task 12)."""
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
    """Without a registry, no fallback path matches user-authored commands."""
    assert _router().route("copy") is None


# ---------------------------------------------------------------------------
# Registry-backed command routing
# ---------------------------------------------------------------------------


def _registry_with(name: str, *, origin: str = "command", enabled: bool = True):
    from voice_commander.registry import ToolEntry, ToolRegistry

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name=name,
            phrases=(),
            func=lambda: None,
            module="test",
            docstring=None,
            enabled=enabled,
            origin=origin,
        )
    )
    return reg


def test_authored_command_routes_by_name():
    reg = _registry_with("copy")
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("copy")
    assert plan is not None
    assert plan.steps == (ToolCall(name="copy", kwargs={}),)


def test_authored_workflow_routes_by_name():
    reg = _registry_with("morning_routine", origin="workflow")
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("morning routine")
    assert plan is not None
    assert plan.steps == (ToolCall(name="morning_routine", kwargs={}),)


def test_disabled_command_does_not_route():
    reg = _registry_with("copy", enabled=False)
    router = VerbRouter(build_default_rules(), registry=reg)
    assert router.route("copy") is None


def test_primitive_origin_does_not_match_command_fallback():
    """Primitives must continue going through the verb-rule path, not the
    command-name fallback, so kwargs-from-tail behaviour is preserved."""
    reg = _registry_with("press", origin="primitive")
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("press ctrl+c")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+c"}),)


def test_unknown_word_still_returns_none_with_registry():
    reg = _registry_with("copy")
    router = VerbRouter(build_default_rules(), registry=reg)
    assert router.route("xyzzy") is None


def _registry_with_many(*names_and_origins: tuple[str, str]):
    from voice_commander.registry import ToolEntry, ToolRegistry

    reg = ToolRegistry()
    for name, origin in names_and_origins:
        reg.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=lambda: None,
                module="test",
                docstring=None,
                enabled=True,
                origin=origin,  # type: ignore[arg-type]
            )
        )
    return reg


def test_multiword_command_routes_exactly():
    reg = _registry_with_many(("close window", "command"))
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("close window")
    assert plan is not None
    assert plan.steps == (ToolCall(name="close window", kwargs={}),)


def test_longest_match_wins_over_bare_verb():
    """Both 'close' and 'close window' registered. Saying 'close window'
    must fire the more specific variant, not the bare default."""
    reg = _registry_with_many(("close", "command"), ("close window", "command"))
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("close window")
    assert plan is not None
    assert plan.steps == (ToolCall(name="close window", kwargs={}),)


def test_bare_verb_routes_when_no_tail():
    reg = _registry_with_many(("close", "command"), ("close window", "command"))
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("close")
    assert plan is not None
    assert plan.steps == (ToolCall(name="close", kwargs={}),)


def test_unknown_tail_does_not_fire_default():
    """User said 'close tab' but only 'close' + 'close window' exist —
    must miss, not fire bare 'close' with surprise extra tokens."""
    reg = _registry_with_many(("close", "command"), ("close window", "command"))
    router = VerbRouter(build_default_rules(), registry=reg)
    assert router.route("close tab") is None


def test_command_match_is_case_insensitive():
    reg = _registry_with_many(("close window", "command"))
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("Close Window.")
    assert plan is not None
    assert plan.steps == (ToolCall(name="close window", kwargs={}),)


def test_underscore_in_name_matches_spoken_words():
    """Storage names use underscores (a-z0-9_); voice arrives as words.
    'close_window' must match the spoken transcript 'close window'."""
    reg = _registry_with_many(("close_window", "command"))
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("close window")
    assert plan is not None
    assert plan.steps == (ToolCall(name="close_window", kwargs={}),)


def test_underscore_longest_match_beats_bare():
    reg = _registry_with_many(("close", "command"), ("close_window", "command"))
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("close window")
    assert plan is not None
    assert plan.steps == (ToolCall(name="close_window", kwargs={}),)


def test_synonym_routes_to_command():
    """Authors can register Whisper-friendly spellings via phrases/synonyms
    so noisy transcripts route correctly."""
    from voice_commander.registry import ToolEntry, ToolRegistry

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="paste",
            phrases=("p a c t",),
            func=lambda: None,
            module="test",
            docstring=None,
            enabled=True,
            origin="command",
        )
    )
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("P.A.C.T.")
    assert plan is not None
    assert plan.steps == (ToolCall(name="paste", kwargs={}),)


def test_punctuation_in_command_name_match():
    """Trailing/internal punct from Whisper must not block exact match."""
    reg = _registry_with_many(("copy", "command"))
    router = VerbRouter(build_default_rules(), registry=reg)
    plan = router.route("Copy.")
    assert plan is not None
    assert plan.steps == (ToolCall(name="copy", kwargs={}),)


def test_legacy_close_no_longer_routes():
    assert _router().route("close") is None


def test_legacy_minimize_no_longer_routes():
    assert _router().route("minimize") is None


def test_legacy_new_tab_no_longer_routes():
    assert _router().route("new tab") is None


# ---------------------------------------------------------------------------
# Bare primitive picker (ADR 0083)
# ---------------------------------------------------------------------------


def test_bare_focus_with_no_picker_still_misses():
    """Existing behaviour preserved when no picker registry is wired."""
    router = VerbRouter(build_default_rules())
    assert router.route("focus") is None


def test_bare_focus_with_picker_registry_routes_to_picker_open():
    from voice_commander.picker.registry import BarePickerRegistry
    from voice_commander.picker.types import PickerItem

    reg = BarePickerRegistry()
    reg.register("focus", lambda: [])
    router = VerbRouter(build_default_rules(), picker_registry=reg)
    plan = router.route("focus")
    assert plan is not None
    assert plan.steps == (ToolCall(name="__picker.open", kwargs={"verb": "focus"}),)
    assert plan.raw_response["router"] == "verb"
    assert plan.raw_response["bare_picker"] is True


def test_bare_focus_with_picker_tolerates_punctuation():
    from voice_commander.picker.registry import BarePickerRegistry

    reg = BarePickerRegistry()
    reg.register("focus", lambda: [])
    router = VerbRouter(build_default_rules(), picker_registry=reg)
    assert router.route("Focus.") is not None
    assert router.route("FOCUS!") is not None


def test_focus_with_tail_still_routes_to_focus_primitive():
    """Adding a picker registry must not change the existing tail-routing path."""
    from voice_commander.picker.registry import BarePickerRegistry

    reg = BarePickerRegistry()
    reg.register("focus", lambda: [])
    router = VerbRouter(build_default_rules(), picker_registry=reg)
    plan = router.route("focus chrome")
    assert plan is not None
    assert plan.steps == (ToolCall(name="focus", kwargs={"target": "chrome"}),)


def test_bare_tabs_with_picker_registry_routes_to_picker_open():
    """Saying 'tabs.' alone opens the tabs picker (browser tab list)."""
    from voice_commander.picker.registry import BarePickerRegistry

    reg = BarePickerRegistry()
    reg.register("tabs", lambda: [])
    router = VerbRouter(build_default_rules(), picker_registry=reg)
    plan = router.route("tabs.")
    assert plan is not None
    assert plan.steps == (ToolCall(name="__picker.open", kwargs={"verb": "tabs"}),)
    assert plan.raw_response["router"] == "verb"
    assert plan.raw_response["bare_picker"] is True


def test_bare_tabs_with_no_picker_misses():
    """Without a registered tabs picker, bare 'tabs' has no tail and no
    default target — falls through to None so the daemon miss-chimes."""
    router = VerbRouter(build_default_rules())
    assert router.route("tabs") is None


def test_tabs_with_tail_still_misses():
    """``tabs`` is bare-only — there's no raw_tail_tool, so 'tabs chrome'
    doesn't accidentally route somewhere unexpected."""
    from voice_commander.picker.registry import BarePickerRegistry

    reg = BarePickerRegistry()
    reg.register("tabs", lambda: [])
    router = VerbRouter(build_default_rules(), picker_registry=reg)
    assert router.route("tabs chrome") is None


# ---------------------------------------------------------------------------
# Chain meta-verb routing (Task 7)
# ---------------------------------------------------------------------------

def _router_with_chain() -> VerbRouter:
    from voice_commander.chain import ChainParser
    from voice_commander.registry import ToolRegistry

    rules = build_default_rules()
    reg = ToolRegistry()
    return VerbRouter(rules, registry=reg, chain_parser=ChainParser(reg, rules))


def test_chain_head_routes_to_chain_parser():
    plan = _router_with_chain().route("chain click click")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["click", "wait", "click"]
    assert plan.steps[1].internal is True


def test_chain_head_alias_chained():
    plan = _router_with_chain().route("chained click click")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["click", "wait", "click"]


def test_chain_without_parser_falls_through_to_miss():
    # If chain_parser is None the router must not crash; head is unrecognised
    # so this routes to None like any other miss.
    rules = build_default_rules()
    router = VerbRouter(rules)  # no chain_parser
    assert router.route("chain click click") is None


def test_chain_head_rejected_payload_returns_none():
    plan = _router_with_chain().route("chain open click")
    assert plan is None  # forbidden verb mid-chain


def test_chain_bare_with_parser_misses():
    # head "chain" but no tail -> parser receives empty string -> None.
    assert _router_with_chain().route("chain") is None
