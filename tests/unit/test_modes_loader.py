from pathlib import Path

import pytest

from voice_commander.modes.compile import build_base_primitive_router
from voice_commander.modes.loader import ModeLoadError, parse_mode_file


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_parses_valid_mode(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "video.toml",
        """
[mode]
badge = "🎬 VIDEO"

[[command]]
phrases = ["split", "blade"]
action  = "press ctrl+b"
""",
    )
    d = parse_mode_file(p, build_base_primitive_router())
    assert d.name == "video"
    assert d.trigger == "video"  # default = filename stem
    assert d.end_phrase == "video end"  # default
    assert d.badge == "🎬 VIDEO"
    assert d.commands[0].phrases == ("split", "blade")
    assert d.commands[0].plan.steps[0].kwargs == {"combo": "ctrl+b"}


def test_explicit_trigger_and_end_phrase(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "m.toml",
        '[mode]\ntrigger = "mouseless"\nend_phrase = "mouseless off"\n\n'
        '[[command]]\nphrases=["go"]\naction="scroll down"\n',
    )
    d = parse_mode_file(p, build_base_primitive_router())
    assert d.trigger == "mouseless"
    assert d.end_phrase == "mouseless off"


def test_reserved_trigger_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "press.toml", '[[command]]\nphrases=["x"]\naction="press a"\n')
    with pytest.raises(ModeLoadError, match="reserved"):
        parse_mode_file(p, build_base_primitive_router())


def test_unresolvable_action_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "v.toml", '[[command]]\nphrases=["x"]\naction="frobnicate"\n')
    with pytest.raises(ModeLoadError, match="could not be routed"):
        parse_mode_file(p, build_base_primitive_router())


def test_empty_phrases_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "v.toml", '[[command]]\nphrases=[]\naction="press a"\n')
    with pytest.raises(ModeLoadError, match="phrases"):
        parse_mode_file(p, build_base_primitive_router())


def test_missing_action_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "v.toml", '[[command]]\nphrases=["x"]\n')
    with pytest.raises(ModeLoadError, match="action"):
        parse_mode_file(p, build_base_primitive_router())


def test_empty_end_phrase_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "v.toml",
        '[mode]\nend_phrase = "  "\n\n[[command]]\nphrases=["x"]\naction="press a"\n',
    )
    with pytest.raises(ModeLoadError, match="end_phrase"):
        parse_mode_file(p, build_base_primitive_router())


def test_empty_string_phrase_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "v.toml", '[[command]]\nphrases=[""]\naction="press a"\n')
    with pytest.raises(ModeLoadError, match="phrase"):
        parse_mode_file(p, build_base_primitive_router())
