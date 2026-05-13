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
