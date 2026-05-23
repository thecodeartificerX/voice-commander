"""Wiring smoke test: build_mode_session constructs a loaded ModeSession."""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_commander.config import Config


@pytest.mark.integration
def test_build_mode_session_loads_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modes = tmp_path / "modes"
    modes.mkdir()
    (modes / "video.toml").write_text(
        '[mode]\nbadge="🎬 VIDEO"\n\n[[command]]\nphrases=["split"]\naction="press ctrl+b"\n',
        encoding="utf-8",
    )
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'[modes]\nenabled = true\ndir = "{modes.as_posix()}"\n', encoding="utf-8")
    cfg = Config.load(cfg_path)

    from voice_commander.daemon import build_mode_session
    from voice_commander.event_bus import EventBus

    bus = EventBus()
    session = build_mode_session(cfg, bus)
    assert session is not None
    assert session.active is False
    assert session._registry.by_trigger("video") is not None


@pytest.mark.integration
def test_build_mode_session_none_when_disabled(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[modes]\nenabled = false\n", encoding="utf-8")
    cfg = Config.load(cfg_path)

    from voice_commander.daemon import build_mode_session
    from voice_commander.event_bus import EventBus

    assert build_mode_session(cfg, EventBus()) is None
