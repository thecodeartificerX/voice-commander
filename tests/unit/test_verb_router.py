import pytest
from voice_commander.plan import Plan, ToolCall
from voice_commander.verb_router import VerbRouter, build_default_rules


def test_copy_routes_to_ctrl_c():
    router = VerbRouter(build_default_rules())
    plan = router.route("copy")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+c"}),)
    assert plan.raw_response == {"router": "verb", "verb": "copy", "tail": ""}


def test_close_window_prefers_subcommand():
    router = VerbRouter(build_default_rules())
    plan = router.route("close window")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "alt+f4"}),)
    assert plan.raw_response == {"router": "verb", "verb": "close", "tail": "window"}


def test_type_passes_raw_tail():
    router = VerbRouter(build_default_rules())
    plan = router.route("type hello world")
    assert plan is not None
    assert plan.steps == (ToolCall(name="type", kwargs={"text": "hello world"}),)
    assert plan.raw_response == {"router": "verb", "verb": "type", "tail": "hello world"}


def test_open_without_tail_misses():
    router = VerbRouter(build_default_rules())
    assert router.route("open") is None


def test_open_with_tail_routes_to_open_tool():
    router = VerbRouter(build_default_rules())
    plan = router.route("open spotify")
    assert plan is not None
    assert plan.steps == (ToolCall(name="open", kwargs={"target": "spotify"}),)
    assert plan.raw_response == {"router": "verb", "verb": "open", "tail": "spotify"}


def test_focus_with_tail_routes_to_focus_tool():
    router = VerbRouter(build_default_rules())
    plan = router.route("focus terminal")
    assert plan is not None
    assert plan.steps == (ToolCall(name="focus", kwargs={"target": "terminal"}),)


def test_refresh_aliases():
    router = VerbRouter(build_default_rules())
    plan = router.route("reload")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+r"}),)


def test_empty_string_returns_none():
    router = VerbRouter(build_default_rules())
    assert router.route("") is None
    assert router.route("   ") is None


def test_unknown_verb_returns_none():
    router = VerbRouter(build_default_rules())
    assert router.route("xyz") is None


def test_new_tab_subcommand():
    router = VerbRouter(build_default_rules())
    plan = router.route("new tab")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+t"}),)


def test_new_window_subcommand():
    router = VerbRouter(build_default_rules())
    plan = router.route("new window")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+n"}),)


# ---------------------------------------------------------------------------
# Bug C1: "close tab" was a silent miss (no tab subcommand on close verb)
# ---------------------------------------------------------------------------

def test_close_tab_routes_to_ctrl_w():
    router = VerbRouter(build_default_rules())
    plan = router.route("close tab")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+w"}),)


def test_close_window_still_routes_to_alt_f4():
    router = VerbRouter(build_default_rules())
    plan = router.route("close window")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "alt+f4"}),)


def test_bare_close_still_routes_to_ctrl_w():
    router = VerbRouter(build_default_rules())
    plan = router.route("close")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+w"}),)


# ---------------------------------------------------------------------------
# Bug C2: Whisper emits trailing punctuation (e.g. "Copy.") — was a miss
# ---------------------------------------------------------------------------

def test_route_tolerates_trailing_period():
    router = VerbRouter(build_default_rules())
    plan = router.route("copy.")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+c"}),)


def test_route_tolerates_trailing_exclamation():
    router = VerbRouter(build_default_rules())
    plan = router.route("copy!")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+c"}),)


def test_route_tolerates_punctuation_in_tail():
    router = VerbRouter(build_default_rules())
    plan = router.route("open spotify.")
    assert plan is not None
    assert plan.steps == (ToolCall(name="open", kwargs={"target": "spotify"}),)


def test_route_tolerates_uppercase_with_punctuation():
    router = VerbRouter(build_default_rules())
    plan = router.route("Copy.")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+c"}),)
