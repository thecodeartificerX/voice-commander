"""Unit tests for the ElementsSession state machine (ADR 0087)."""

from typing import Any

from voice_commander.elements.scanner import Element
from voice_commander.elements.session import ElementsSession, ElementsState


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


def _elements(count: int) -> list[Element]:
    return [
        Element(index=i, label=f"el{i}", control_type="ButtonControl",
                rect=(i, i, 10, 10), center=(i + 5, i + 5))
        for i in range(1, count + 1)
    ]


MONITOR = (0, 0, 1920, 1080)


def test_starts_idle() -> None:
    session = ElementsSession(_FakeBus(), hint_timeout_s=0.0)
    assert session.state is ElementsState.IDLE
    assert session.active is False


def test_begin_scan_moves_to_scanning() -> None:
    session = ElementsSession(_FakeBus(), hint_timeout_s=0.0)
    session.begin_scan()
    assert session.state is ElementsState.SCANNING
    assert session.active is True


def test_begin_scan_is_noop_when_not_idle() -> None:
    session = ElementsSession(_FakeBus(), hint_timeout_s=0.0)
    session.begin_scan()
    session.begin_scan()
    assert session.state is ElementsState.SCANNING


def test_show_publishes_elements_show() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(2), MONITOR)
    assert session.state is ElementsState.HINTS_SHOWN
    assert bus.events == [
        (
            "elements.show",
            {
                "monitor": [0, 0, 1920, 1080],
                "elements": [
                    {"index": 1, "rect": [1, 1, 10, 10], "label": "el1"},
                    {"index": 2, "rect": [2, 2, 10, 10], "label": "el2"},
                ],
            },
        )
    ]


def test_show_is_noop_when_not_scanning() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.show(_elements(1), MONITOR)  # never began a scan
    assert session.state is ElementsState.IDLE
    assert bus.events == []


def test_fail_returns_to_idle_without_event() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.fail()
    assert session.state is ElementsState.IDLE
    assert bus.events == []


def test_handle_valid_number_returns_element_and_hides() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(3), MONITOR)
    chosen = session.handle_utterance("two")
    assert chosen is not None
    assert chosen.index == 2
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})


def test_handle_out_of_range_number_cancels() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(3), MONITOR)
    assert session.handle_utterance("nine") is None
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})


def test_handle_non_number_cancels() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(3), MONITOR)
    assert session.handle_utterance("never mind") is None
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})


def test_handle_is_noop_when_not_showing() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    assert session.handle_utterance("two") is None
    assert bus.events == []


def test_cancel_from_hints_shown_hides() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(2), MONITOR)
    session.cancel()
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})


def test_cancel_from_scanning_emits_no_event() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.cancel()
    assert session.state is ElementsState.IDLE
    assert bus.events == []


def test_timeout_hides_overlay() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(2), MONITOR)
    session._on_timeout(session._generation)
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})


def test_stale_timer_generation_guard() -> None:
    """A timer captured during scan #1 must not publish elements.hide if scan
    #2 has already started (i.e. _generation has advanced)."""
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)

    # Scan #1: begin, show, capture generation, then cancel (simulates
    # handle_utterance / cancel returning to IDLE before the timer fires).
    session.begin_scan()
    session.show(_elements(2), MONITOR)
    gen_1 = session._generation  # == 1
    session.cancel()  # → IDLE, clears elements, publishes elements.hide

    # Scan #2: begin (increments generation to 2).
    session.begin_scan()
    gen_2 = session._generation  # == 2
    assert gen_2 == gen_1 + 1

    # Simulate the stale timer from scan #1 firing now.
    events_before = list(bus.events)
    session._on_timeout(gen_1)  # stale generation — should be a no-op

    # State machine must remain in SCANNING (scan #2 is still in flight).
    assert session.state is ElementsState.SCANNING
    # No new events must have been published.
    assert bus.events == events_before


def test_current_generation_timer_fires_correctly() -> None:
    """A timer with the *current* generation must still dismiss the overlay."""
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(2), MONITOR)
    gen = session._generation

    session._on_timeout(gen)

    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})
