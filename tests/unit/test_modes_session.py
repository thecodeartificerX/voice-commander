from __future__ import annotations

from pathlib import Path
from typing import Any

from voice_commander.modes.registry import ModeRegistry
from voice_commander.modes.session import ModeSession


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


def _registry(tmp_path: Path) -> ModeRegistry:
    (tmp_path / "video.toml").write_text(
        '[mode]\nbadge = "🎬 VIDEO"\n\n[[command]]\nphrases=["split"]\naction="press ctrl+b"\n',
        encoding="utf-8",
    )
    reg = ModeRegistry(tmp_path)
    reg.load_all()
    return reg


def test_inactive_by_default(tmp_path: Path) -> None:
    s = ModeSession(_FakeBus(), _registry(tmp_path))
    assert s.active is False
    assert s.active_mode is None


def test_try_enter_on_trigger_publishes(tmp_path: Path) -> None:
    bus = _FakeBus()
    s = ModeSession(bus, _registry(tmp_path))
    assert s.try_enter("video") is not None
    assert s.active is True
    assert s.active_mode == "video"
    assert bus.events[0][0] == "mode.enter"
    assert bus.events[0][1] == {"name": "video", "badge": "🎬 VIDEO"}


def test_try_enter_non_trigger_returns_none(tmp_path: Path) -> None:
    s = ModeSession(_FakeBus(), _registry(tmp_path))
    assert s.try_enter("split") is None
    assert s.active is False


def test_in_mode_command_returns_plan(tmp_path: Path) -> None:
    s = ModeSession(_FakeBus(), _registry(tmp_path))
    s.try_enter("video")
    out = s.handle_utterance("split")
    assert out.kind == "plan" and out.plan is not None
    assert out.plan.steps[0].kwargs == {"combo": "ctrl+b"}


def test_end_phrase_exits_and_publishes(tmp_path: Path) -> None:
    bus = _FakeBus()
    s = ModeSession(bus, _registry(tmp_path))
    s.try_enter("video")
    out = s.handle_utterance("video end")
    assert out.kind == "exit"
    assert s.active is False
    assert bus.events[-1][0] == "mode.exit"
    assert bus.events[-1][1] == {"name": "video", "reason": "end_phrase"}


def test_unknown_in_mode_is_miss_and_stays(tmp_path: Path) -> None:
    s = ModeSession(_FakeBus(), _registry(tmp_path))
    s.try_enter("video")
    assert s.handle_utterance("kerfuffle").kind == "miss"
    assert s.active is True  # stays in mode on a miss


def test_reset_exits_silently_with_event(tmp_path: Path) -> None:
    bus = _FakeBus()
    s = ModeSession(bus, _registry(tmp_path))
    s.try_enter("video")
    s.reset()
    assert s.active is False
    assert bus.events[-1][0] == "mode.exit"
    assert bus.events[-1][1] == {"name": "video", "reason": "session_ended"}
