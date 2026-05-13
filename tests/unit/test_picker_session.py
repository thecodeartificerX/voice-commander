from __future__ import annotations

from voice_commander.picker.session import PickerSession
from voice_commander.picker.types import PickerItem
from voice_commander.plan import Plan, ToolCall


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, data: dict | None = None) -> None:
        self.events.append((event_type, data or {}))


def _item(label: str, hwnd: int) -> PickerItem:
    return PickerItem(
        label=label,
        action=Plan(
            steps=(ToolCall(name="focus", kwargs={"_hwnd": hwnd}),),
            raw_response={"router": "picker", "verb": "focus", "selection": label},
        ),
    )


def test_inactive_by_default():
    session = PickerSession(bus=_FakeBus())
    assert session.active is False
    assert session.items == ()


def test_open_sets_active_and_emits_event():
    bus = _FakeBus()
    session = PickerSession(bus=bus)
    items = [_item("Chrome", 11), _item("VS Code", 22)]
    session.open("focus", items)
    assert session.active is True
    assert session.items == tuple(items)
    assert bus.events[0][0] == "picker.open"
    payload = bus.events[0][1]
    assert payload["verb"] == "focus"
    assert payload["items"] == [
        {"n": 1, "label": "Chrome"},
        {"n": 2, "label": "VS Code"},
    ]


def test_close_clears_state_and_emits_event():
    bus = _FakeBus()
    session = PickerSession(bus=bus)
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()
    session.close()
    assert session.active is False
    assert session.items == ()
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "select"})]


def test_cancel_emits_close_with_reason_cancel():
    bus = _FakeBus()
    session = PickerSession(bus=bus)
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()
    session.cancel(reason="word")
    assert session.active is False
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "word"})]


def test_re_open_replaces_items_and_re_publishes():
    bus = _FakeBus()
    session = PickerSession(bus=bus)
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()
    session.open("focus", [_item("VS Code", 22)])
    assert session.items == (_item("VS Code", 22),)
    assert [e[0] for e in bus.events] == ["picker.open"]


def test_handle_transcript_returns_action_and_closes_on_valid_number():
    bus = _FakeBus()
    items = [_item("Chrome", 11), _item("VS Code", 22), _item("Slack", 33)]
    session = PickerSession(
        bus=bus,
        cancel_words=("cancel", "nevermind", "stop"),
    )
    session.open("focus", items)
    bus.events.clear()

    outcome = session.handle_transcript("three.")
    assert outcome is not None
    assert outcome.kind == "select"
    assert outcome.plan is items[2].action
    assert outcome.n == 3
    assert session.active is False
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "select"})]


def test_handle_transcript_cancel_word_closes_with_word_reason():
    bus = _FakeBus()
    session = PickerSession(bus=bus, cancel_words=("cancel", "nevermind"))
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()

    outcome = session.handle_transcript("Cancel.")
    assert outcome is not None
    assert outcome.kind == "cancel"
    assert outcome.plan is None
    assert session.active is False
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "word"})]


def test_handle_transcript_out_of_range_keeps_open():
    bus = _FakeBus()
    items = [_item("Chrome", 11), _item("VS Code", 22)]
    session = PickerSession(bus=bus, cancel_words=())
    session.open("focus", items)
    bus.events.clear()

    outcome = session.handle_transcript("seven")
    assert outcome is not None
    assert outcome.kind == "miss"
    assert outcome.plan is None
    assert session.active is True
    assert bus.events == []  # no close, picker stays open


def test_handle_transcript_non_number_keeps_open():
    bus = _FakeBus()
    session = PickerSession(bus=bus, cancel_words=())
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()

    outcome = session.handle_transcript("hello world")
    assert outcome is not None
    assert outcome.kind == "miss"
    assert session.active is True


def test_handle_transcript_inactive_returns_none():
    session = PickerSession(bus=_FakeBus(), cancel_words=())
    assert session.handle_transcript("three") is None


def test_tick_closes_on_timeout():
    bus = _FakeBus()
    clock = {"t": 1000.0}
    session = PickerSession(
        bus=bus,
        now=lambda: clock["t"],
        cancel_words=(),
        timeout_sec=5.0,
    )
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()

    clock["t"] = 1003.0
    session.tick()
    assert session.active is True
    assert bus.events == []

    clock["t"] = 1005.5
    session.tick()
    assert session.active is False
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "timeout"})]


def test_tick_noop_when_inactive():
    bus = _FakeBus()
    clock = {"t": 0.0}
    session = PickerSession(
        bus=bus, now=lambda: clock["t"], cancel_words=(), timeout_sec=1.0
    )
    clock["t"] = 100.0
    session.tick()
    assert bus.events == []


def test_tick_reset_on_re_open():
    """Re-opening (e.g. provider re-runs) resets the timeout window."""
    bus = _FakeBus()
    clock = {"t": 0.0}
    session = PickerSession(
        bus=bus, now=lambda: clock["t"], cancel_words=(), timeout_sec=5.0
    )
    session.open("focus", [_item("Chrome", 11)])
    clock["t"] = 4.5
    session.open("focus", [_item("VS Code", 22)])  # reset timer
    clock["t"] = 9.0
    session.tick()
    assert session.active is True  # 4.5 elapsed since re-open, under 5.0
    clock["t"] = 9.6
    session.tick()
    assert session.active is False  # now 5.1 elapsed since re-open
