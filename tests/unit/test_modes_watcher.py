"""Unit tests for the modes hot-reload watcher."""

from __future__ import annotations

import time
from pathlib import Path

from voice_commander.modes.watcher import ModesWatcher


def test_watcher_fires_on_new_file(tmp_path: Path) -> None:
    fired: list[Path] = []
    w = ModesWatcher(tmp_path, lambda p: fired.append(p), debounce_ms=50)
    w.start()
    try:
        (tmp_path / "video.toml").write_text(
            '[[command]]\nphrases=["x"]\naction="press a"\n', "utf-8"
        )
        deadline = time.monotonic() + 5.0
        while not fired and time.monotonic() < deadline:
            time.sleep(0.05)
        assert fired, "watcher did not fire within 5s"
    finally:
        w.stop()
