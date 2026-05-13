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
