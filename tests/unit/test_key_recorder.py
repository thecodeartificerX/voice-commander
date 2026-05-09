"""Unit tests for the backend ``KeyRecorder``.

The pynput Listener is replaced with a fake that exposes ``on_press`` /
``on_release`` directly so tests drive the state machine synchronously
without touching real keyboard hardware. All assertions look at the
events published through ``EventBus``.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from pynput import keyboard

from voice_commander.event_bus import EventBus
from voice_commander.recorder.key_recorder import (
    EVT_CANCELLED,
    EVT_CAPTURED,
    EVT_FAILED,
    EVT_STARTED,
    EVT_TIMEOUT,
    KeyRecorder,
)


class FakeListener:
    """Synchronous stand-in for ``pynput.keyboard.Listener``.

    The recorder hands us its callbacks via the factory; tests drive
    them directly. ``daemon`` is settable to mirror the real API.
    Tracks ``stop()`` calls for assertions.
    """

    def __init__(self, *, on_press, on_release, suppress: bool) -> None:
        self.on_press = on_press
        self.on_release = on_release
        self.suppress = suppress
        self.daemon = False
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def harness():
    """Build a KeyRecorder backed by a FakeListener; expose both."""
    bus = EventBus()
    holder: dict[str, FakeListener] = {}

    def factory(**kwargs: Any) -> FakeListener:
        listener = FakeListener(**kwargs)
        holder["listener"] = listener
        return listener

    rec = KeyRecorder(bus, listener_factory=factory)
    return rec, bus, holder


def _events(bus: EventBus, want_type: str | None = None) -> list[tuple[str, dict]]:
    """Drain the event bus buffer, optionally filtered by type."""
    return [
        (e.type, e.data)
        for e in bus.replay_after(0)
        if want_type is None or e.type == want_type
    ]


# ---------------------------------------------------------------------------
# start / stop / state
# ---------------------------------------------------------------------------


def test_start_publishes_started_event(harness) -> None:
    rec, bus, holder = harness
    assert rec.start(timeout=5.0) is True
    assert rec.is_recording() is True
    assert holder["listener"].started is True
    assert holder["listener"].suppress is True
    types = [t for t, _ in _events(bus)]
    assert types == [EVT_STARTED]
    assert _events(bus, EVT_STARTED)[0][1]["timeout"] == 5.0
    rec.cancel()


def test_start_rejects_concurrent_session(harness) -> None:
    rec, _bus, _ = harness
    assert rec.start(timeout=5.0) is True
    assert rec.start(timeout=5.0) is False
    rec.cancel()


def test_cancel_is_idempotent(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    rec.cancel()
    rec.cancel()  # second cancel must be a no-op
    cancels = [t for t, _ in _events(bus) if t == EVT_CANCELLED]
    assert len(cancels) == 1
    assert holder["listener"].stopped is True


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def _press(rec: KeyRecorder, listener: FakeListener, key: object) -> bool | None:
    """Drive the recorder's on_press callback."""
    return listener.on_press(key)


def _release(rec: KeyRecorder, listener: FakeListener, key: object) -> bool | None:
    return listener.on_release(key)


def test_capture_ctrl_l(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    listener = holder["listener"]
    _press(rec, listener, keyboard.Key.ctrl_l)
    rv = _press(rec, listener, keyboard.KeyCode.from_char("l"))
    # Returning False from on_press signals listener to stop itself.
    assert rv is False
    captured = _events(bus, EVT_CAPTURED)
    assert captured == [(EVT_CAPTURED, {"combo": "ctrl+l"})]
    assert rec.is_recording() is False


def test_capture_ctrl_shift_t(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    listener = holder["listener"]
    _press(rec, listener, keyboard.Key.ctrl_l)
    _press(rec, listener, keyboard.Key.shift_l)
    _press(rec, listener, keyboard.KeyCode.from_char("t"))
    assert _events(bus, EVT_CAPTURED)[0][1]["combo"] == "ctrl+shift+t"


def test_capture_f11_no_modifiers(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    listener = holder["listener"]
    _press(rec, listener, keyboard.Key.f11)
    assert _events(bus, EVT_CAPTURED)[0][1]["combo"] == "f11"


def test_capture_win_shift_s(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    listener = holder["listener"]
    _press(rec, listener, keyboard.Key.cmd)
    _press(rec, listener, keyboard.Key.shift_l)
    _press(rec, listener, keyboard.KeyCode.from_char("s"))
    assert _events(bus, EVT_CAPTURED)[0][1]["combo"] == "shift+win+s"


def test_release_modifier_then_press_other(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    listener = holder["listener"]
    _press(rec, listener, keyboard.Key.ctrl_l)
    _release(rec, listener, keyboard.Key.ctrl_l)
    _press(rec, listener, keyboard.KeyCode.from_char("l"))
    # Modifier was released before main key, so the chord is just "l".
    assert _events(bus, EVT_CAPTURED)[0][1]["combo"] == "l"


def test_uppercase_char_lowercased(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    listener = holder["listener"]
    _press(rec, listener, keyboard.Key.shift_l)
    _press(rec, listener, keyboard.KeyCode.from_char("L"))
    assert _events(bus, EVT_CAPTURED)[0][1]["combo"] == "shift+l"


# ---------------------------------------------------------------------------
# failure modes
# ---------------------------------------------------------------------------


def test_unknown_key_publishes_failed(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    listener = holder["listener"]

    class WeirdKey:
        char = None
        name = None

    _press(rec, listener, WeirdKey())
    failures = _events(bus, EVT_FAILED)
    assert len(failures) == 1
    assert "unrepresentable" in failures[0][1]["reason"]
    assert _events(bus, EVT_CAPTURED) == []
    assert rec.is_recording() is False


def test_post_finalize_events_are_ignored(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    listener = holder["listener"]
    _press(rec, listener, keyboard.Key.ctrl_l)
    _press(rec, listener, keyboard.KeyCode.from_char("l"))
    # Listener thread sometimes drains buffered events after stop; those
    # must not produce a second captured event.
    _press(rec, listener, keyboard.KeyCode.from_char("x"))
    _release(rec, listener, keyboard.Key.ctrl_l)
    captures = _events(bus, EVT_CAPTURED)
    assert len(captures) == 1


# ---------------------------------------------------------------------------
# timeout
# ---------------------------------------------------------------------------


def test_timeout_publishes_event_and_clears_state() -> None:
    bus = EventBus()
    holder: dict[str, FakeListener] = {}

    def factory(**kw: Any) -> FakeListener:
        listener = FakeListener(**kw)
        holder["listener"] = listener
        return listener

    rec = KeyRecorder(bus, listener_factory=factory)
    rec.start(timeout=0.05)  # 50 ms
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and rec.is_recording():
        time.sleep(0.01)
    assert rec.is_recording() is False
    timeouts = [e for e in bus.replay_after(0) if e.type == EVT_TIMEOUT]
    assert len(timeouts) == 1
    assert holder["listener"].stopped is True


def test_capture_cancels_pending_timer(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=10.0)
    listener = holder["listener"]
    _press(rec, listener, keyboard.KeyCode.from_char("a"))
    # Wait long enough that an un-cancelled timer would fire.
    time.sleep(0.05)
    assert _events(bus, EVT_TIMEOUT) == []


# ---------------------------------------------------------------------------
# threading sanity
# ---------------------------------------------------------------------------


def test_concurrent_cancel_during_capture_does_not_double_publish(harness) -> None:
    rec, bus, holder = harness
    rec.start(timeout=5.0)
    listener = holder["listener"]

    barrier = threading.Barrier(2)

    def do_press() -> None:
        barrier.wait()
        listener.on_press(keyboard.KeyCode.from_char("a"))

    def do_cancel() -> None:
        barrier.wait()
        rec.cancel()

    t1 = threading.Thread(target=do_press)
    t2 = threading.Thread(target=do_cancel)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    finals = [
        e
        for e in bus.replay_after(0)
        if e.type in {EVT_CAPTURED, EVT_CANCELLED, EVT_FAILED, EVT_TIMEOUT}
    ]
    # Exactly one terminal event must be published.
    assert len(finals) == 1
