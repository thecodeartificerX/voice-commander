from voice_commander.modes.compile import build_base_primitive_router, compile_action
from voice_commander.modes.router import ModeRouter
from voice_commander.modes.types import ModeCommand, ModeDefinition


def _video() -> ModeDefinition:
    base = build_base_primitive_router()
    return ModeDefinition(
        name="video",
        trigger="video",
        end_phrase="video end",
        badge="🎬 VIDEO",
        commands=(
            ModeCommand(phrases=("split", "cut clip"), plan=compile_action(base, "press ctrl+b")),
        ),
    )


def test_matches_mode_command() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    plan = r.route("split")
    assert plan is not None and plan.steps[0].kwargs == {"combo": "ctrl+b"}


def test_matches_multiword_synonym_punctuation_tolerant() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    assert r.route("Cut clip.") is not None


def test_primitive_fallthrough_still_works() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    plan = r.route("scroll up")
    assert plan is not None and plan.steps[0].name == "scroll"


def test_unknown_phrase_misses() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    assert r.route("open spotify by name foobar baz") is None or r.route("kerfuffle") is None
    assert r.route("kerfuffle") is None


def test_synthetic_intercept_is_a_miss_in_mode() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    assert r.route("dictate") is None  # bare dictate would be __dictation.start
