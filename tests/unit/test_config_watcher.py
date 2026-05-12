from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from voice_commander.config_watcher import ConfigWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEBOUNCE = 50  # ms — fast enough for tests, still exercises debounce logic


def _write(path: Path, text: str = "x") -> None:
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_watcher_invokes_callback_on_modify(tmp_path: Path) -> None:
    """Modifying the watched file triggers the callback."""
    cfg = tmp_path / "config.toml"
    _write(cfg, "foo = 1")

    received: list[Path] = []
    ev = threading.Event()

    def _cb(p: Path) -> None:
        received.append(p)
        ev.set()

    w = ConfigWatcher(cfg, on_change=_cb, debounce_ms=_DEBOUNCE)
    w.start()
    try:
        time.sleep(0.05)  # let observer settle before touching the file
        _write(cfg, "foo = 2")
        fired = ev.wait(timeout=2.0)
    finally:
        w.stop()

    assert fired, "callback did not fire within 2 s"
    assert len(received) >= 1
    assert received[0] == cfg


def test_watcher_debounces_rapid_writes(tmp_path: Path) -> None:
    """Five rapid writes within the debounce window produce exactly one callback."""
    cfg = tmp_path / "config.toml"
    _write(cfg, "foo = 0")

    call_count = 0
    finished = threading.Event()

    def _cb(p: Path) -> None:
        nonlocal call_count
        call_count += 1
        finished.set()

    w = ConfigWatcher(cfg, on_change=_cb, debounce_ms=_DEBOUNCE)
    w.start()
    try:
        time.sleep(0.05)  # let observer settle
        # Write 5 times faster than the debounce window.
        for i in range(5):
            _write(cfg, f"foo = {i}")
            time.sleep(0.005)  # 5 ms << 50 ms debounce

        # Wait for the single debounced callback (debounce + generous margin).
        finished.wait(timeout=2.0)
        # Pause an extra debounce window to confirm no second callback fires.
        time.sleep(_DEBOUNCE / 1000.0 * 3)
    finally:
        w.stop()

    assert call_count == 1, f"expected 1 callback, got {call_count}"


def test_watcher_swallows_callback_exception(tmp_path: Path) -> None:
    """A callback that raises must not kill the observer; next change still fires."""
    cfg = tmp_path / "config.toml"
    _write(cfg, "foo = 1")

    call_count = 0
    second_ev = threading.Event()

    def _cb(p: Path) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("deliberate test error")
        second_ev.set()

    w = ConfigWatcher(cfg, on_change=_cb, debounce_ms=_DEBOUNCE)
    w.start()
    try:
        time.sleep(0.05)
        # First write — callback raises.
        _write(cfg, "foo = 2")
        # Wait for the first (raising) callback to have fired.
        time.sleep((_DEBOUNCE / 1000.0) * 4)

        # Second write — should still fire if observer is alive.
        _write(cfg, "foo = 3")
        alive = second_ev.wait(timeout=2.0)
    finally:
        w.stop()

    assert alive, "second callback did not fire — observer may have died after exception"
    assert call_count == 2


def test_watcher_stop_is_idempotent(tmp_path: Path) -> None:
    """Calling stop() twice raises no exception."""
    cfg = tmp_path / "config.toml"
    _write(cfg, "foo = 1")

    w = ConfigWatcher(cfg, on_change=lambda p: None, debounce_ms=_DEBOUNCE)
    w.start()
    w.stop()
    w.stop()  # must not raise
