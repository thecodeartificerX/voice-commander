"""Unit tests for the Right Ctrl session toggle."""

from __future__ import annotations

from voice_commander.dictation_stream.__main__ import SessionToggle


class _FakeSession:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> str:
        self.stopped = True
        return "pasted text"


def test_first_press_starts_a_session():
    sessions: list[_FakeSession] = []

    def make() -> _FakeSession:
        s = _FakeSession()
        sessions.append(s)
        return s

    clock = iter([10.0, 20.0])
    toggle = SessionToggle(make, clock=lambda: next(clock))

    assert toggle.fire() is None
    assert len(sessions) == 1 and sessions[0].started is True


def test_second_press_stops_and_returns_text():
    session = _FakeSession()
    clock = iter([10.0, 20.0])
    toggle = SessionToggle(lambda: session, clock=lambda: next(clock))

    toggle.fire()              # start
    result = toggle.fire()     # stop
    assert result == "pasted text"
    assert session.stopped is True


def test_debounce_drops_rapid_second_press():
    sessions: list[_FakeSession] = []
    clock = iter([10.0, 10.02])  # 20 ms apart — inside the 50 ms debounce

    def make() -> _FakeSession:
        s = _FakeSession()
        sessions.append(s)
        return s

    toggle = SessionToggle(make, debounce_s=0.05, clock=lambda: next(clock))
    toggle.fire()              # start
    assert toggle.fire() is None  # debounced — ignored
    assert len(sessions) == 1
    assert sessions[0].stopped is False
