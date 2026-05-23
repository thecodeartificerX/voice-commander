from pathlib import Path

import pytest

from voice_commander.feedback import WindowsFeedbackSink


def test_mode_enter_exit_play_existing_sounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    played: list[Path] = []
    sink = WindowsFeedbackSink(sounds_dir=tmp_path)
    monkeypatch.setattr(sink, "_play", lambda p: played.append(p))
    sink.on_mode_enter()
    sink.on_mode_exit()
    assert played[0] == sink._start
    assert played[1] == sink._stop
