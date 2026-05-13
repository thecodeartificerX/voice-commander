from __future__ import annotations

import dataclasses

import pytest

from voice_commander.picker.types import PickerItem
from voice_commander.plan import Plan, ToolCall


def _plan(name: str = "focus") -> Plan:
    return Plan(
        steps=(ToolCall(name=name, kwargs={}),),
        raw_response={"router": "picker"},
    )


def test_picker_item_holds_label_and_action():
    item = PickerItem(label="Chrome", action=_plan())
    assert item.label == "Chrome"
    assert item.action.steps[0].name == "focus"


def test_picker_item_is_frozen():
    item = PickerItem(label="Chrome", action=_plan())
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.label = "Edge"  # type: ignore[misc]


def test_picker_item_requires_both_fields():
    with pytest.raises(TypeError):
        PickerItem(label="Chrome")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        PickerItem(action=_plan())  # type: ignore[call-arg]
