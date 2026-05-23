import pytest

from voice_commander.modes.compile import build_base_primitive_router, compile_action


def test_compiles_press_combo() -> None:
    router = build_base_primitive_router()
    plan = compile_action(router, "press ctrl+b")
    assert plan.steps[0].name == "press"
    assert plan.steps[0].kwargs == {"combo": "ctrl+b"}


def test_compiles_scroll_subcommand() -> None:
    router = build_base_primitive_router()
    plan = compile_action(router, "scroll up")
    assert plan.steps[0].name == "scroll"
    assert plan.steps[0].kwargs == {"direction": "up"}


def test_compiles_chain() -> None:
    # ``press`` is forbidden in chain (arg-taking verb); use nullary primitives.
    router = build_base_primitive_router()
    plan = compile_action(router, "chain click scroll")
    names = [s.name for s in plan.steps]
    assert names.count("click") == 1  # plus internal wait separators
    assert "wait" in names  # synthetic inter-step separator


def test_unresolvable_action_raises() -> None:
    router = build_base_primitive_router()
    with pytest.raises(ValueError, match="could not be routed"):
        compile_action(router, "frobnicate the widget")


def test_synthetic_intercept_action_raises() -> None:
    # Bare "dictate" routes to a synthetic __dictation.start step — not a valid action.
    router = build_base_primitive_router()
    with pytest.raises(ValueError, match="synthetic"):
        compile_action(router, "dictate")
