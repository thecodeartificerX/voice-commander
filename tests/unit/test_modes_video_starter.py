from pathlib import Path

from voice_commander.modes.compile import build_base_primitive_router
from voice_commander.modes.loader import parse_mode_file
from voice_commander.modes.router import ModeRouter

VIDEO = Path(__file__).resolve().parents[2] / "modes" / "video.toml"


def test_video_starter_loads_and_routes() -> None:
    d = parse_mode_file(VIDEO, build_base_primitive_router())
    assert d.trigger == "video"
    r = ModeRouter(d, build_base_primitive_router())
    cases = {
        "split": {"combo": "ctrl+b"},
        "undo": {"combo": "ctrl+z"},
        "redo": {"combo": "ctrl+shift+z"},
        "one": {"combo": "1"},
        "two": {"combo": "2"},
        "three": {"combo": "3"},
        "ripple": {"combo": "delete"},
        "back": {"combo": "up"},
        "forward": {"combo": "down"},
        "play": {"combo": "space"},
        "pause": {"combo": "space"},
    }
    for phrase, kwargs in cases.items():
        plan = r.route(phrase)
        assert plan is not None, phrase
        assert plan.steps[0].name == "press"
        assert plan.steps[0].kwargs == kwargs, phrase
