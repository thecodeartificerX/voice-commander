"""Integration tests for the elements-mode daemon flow (ADR 0087).

These exercise the daemon's elements-mode worker methods directly. The daemon
shell is built with __new__ (the same pattern as tests/unit/test_window.py) so
no audio/transcription stack is needed.
"""

from typing import Any

import pytest

from voice_commander.elements.scanner import Element
from voice_commander.elements.session import ElementsSession, ElementsState

pytestmark = pytest.mark.integration


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


class _SilentFeedback:
    def __init__(self) -> None:
        self.misses = 0
        self.starts = 0

    def on_recording_start(self) -> None:
        self.starts += 1

    def on_recording_stop(self) -> None:
        pass

    def on_miss(self, text: str, tools: tuple[Any, ...]) -> None:
        self.misses += 1


def _elements(count: int) -> list[Element]:
    return [
        Element(index=i, label=f"el{i}", control_type="ButtonControl",
                rect=(i * 10, i * 10, 20, 20), center=(i * 10 + 10, i * 10 + 10))
        for i in range(1, count + 1)
    ]


def _daemon_shell(session: ElementsSession, feedback: _SilentFeedback) -> Any:
    from voice_commander import daemon as daemon_mod

    daemon = daemon_mod.StreamingDaemon.__new__(daemon_mod.StreamingDaemon)
    daemon._elements_session = session
    daemon._elements_max_elements = 200
    daemon._elements_scan_timeout_s = 3.0
    daemon._feedback = feedback
    return daemon


def test_scan_worker_shows_elements(monkeypatch) -> None:
    from voice_commander import daemon as daemon_mod

    monkeypatch.setattr(daemon_mod.desktop, "foreground_window", lambda: 4242)
    monkeypatch.setattr(daemon_mod.desktop, "monitor_rect", lambda hwnd: (0, 0, 1920, 1080))
    monkeypatch.setattr(daemon_mod.scanner, "scan_window", lambda hwnd, **kw: _elements(3))

    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    _daemon_shell(session, _SilentFeedback())._do_element_scan()

    assert session.state is ElementsState.HINTS_SHOWN
    assert bus.events[-1][0] == "elements.show"
    assert len(bus.events[-1][1]["elements"]) == 3


def test_scan_worker_fails_when_nothing_found(monkeypatch) -> None:
    from voice_commander import daemon as daemon_mod

    monkeypatch.setattr(daemon_mod.desktop, "foreground_window", lambda: 4242)
    monkeypatch.setattr(daemon_mod.desktop, "monitor_rect", lambda hwnd: (0, 0, 1920, 1080))
    monkeypatch.setattr(daemon_mod.scanner, "scan_window", lambda hwnd, **kw: [])

    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    feedback = _SilentFeedback()
    _daemon_shell(session, feedback)._do_element_scan()

    assert session.state is ElementsState.IDLE
    assert feedback.misses == 1


def test_scan_worker_fails_when_no_foreground_window(monkeypatch) -> None:
    from voice_commander import daemon as daemon_mod

    monkeypatch.setattr(daemon_mod.desktop, "foreground_window", lambda: 0)

    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    feedback = _SilentFeedback()
    _daemon_shell(session, feedback)._do_element_scan()

    assert session.state is ElementsState.IDLE
    assert feedback.misses == 1


def test_click_worker_clicks_element_center(monkeypatch) -> None:
    from voice_commander import daemon as daemon_mod

    clicked: list[tuple[int, int]] = []
    monkeypatch.setattr(
        daemon_mod.clicker, "click_point",
        lambda x, y, **kw: clicked.append((x, y)),
    )

    daemon = _daemon_shell(ElementsSession(_FakeBus(), hint_timeout_s=0.0), _SilentFeedback())
    daemon._do_element_click(_elements(3)[1])  # Python index 1 → element index=2, center=(30, 30)

    assert clicked == [(30, 30)]


def test_number_utterance_selects_element() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(5), (0, 0, 1920, 1080))

    chosen = session.handle_utterance("three")

    assert chosen is not None and chosen.index == 3
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})
