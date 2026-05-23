from voice_commander.modes.types import ModeCommand, ModeDefinition, ModeOutcome
from voice_commander.plan import Plan, ToolCall


def _plan() -> Plan:
    return Plan(steps=(ToolCall(name="press", kwargs={"combo": "ctrl+b"}),), raw_response={})


def test_mode_command_holds_phrases_and_plan() -> None:
    cmd = ModeCommand(phrases=("split", "blade"), plan=_plan())
    assert cmd.phrases == ("split", "blade")
    assert cmd.plan.steps[0].name == "press"


def test_mode_definition_fields() -> None:
    d = ModeDefinition(
        name="video",
        trigger="video",
        end_phrase="video end",
        badge="🎬 VIDEO",
        commands=(ModeCommand(phrases=("split",), plan=_plan()),),
    )
    assert d.name == "video"
    assert d.trigger == "video"
    assert d.end_phrase == "video end"
    assert d.badge == "🎬 VIDEO"
    assert len(d.commands) == 1


def test_mode_outcome_kinds() -> None:
    assert ModeOutcome(kind="miss").plan is None
    assert ModeOutcome(kind="plan", plan=_plan()).plan is not None
    assert ModeOutcome(kind="exit").plan is None
