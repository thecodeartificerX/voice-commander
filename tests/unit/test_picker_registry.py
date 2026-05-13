from __future__ import annotations

import pytest

from voice_commander.picker.registry import (
    BarePickerRegistry,
    DuplicatePickerError,
    bare_picker,
    get_global_picker_registry,
    reset_global_picker_registry,
)
from voice_commander.picker.types import PickerItem
from voice_commander.plan import Plan, ToolCall


def _items() -> list[PickerItem]:
    return [
        PickerItem(
            label="Chrome",
            action=Plan(
                steps=(ToolCall(name="focus", kwargs={"_hwnd": 1}),),
                raw_response={"router": "picker"},
            ),
        )
    ]


def test_register_and_lookup():
    reg = BarePickerRegistry()
    reg.register("focus", _items)
    assert reg.has("focus")
    assert reg.verbs() == ("focus",)
    provider = reg.get("focus")
    assert provider is not None
    assert provider()[0].label == "Chrome"


def test_get_unknown_returns_none():
    reg = BarePickerRegistry()
    assert reg.get("nope") is None
    assert reg.has("nope") is False


def test_register_duplicate_raises():
    reg = BarePickerRegistry()
    reg.register("focus", _items)
    with pytest.raises(DuplicatePickerError):
        reg.register("focus", _items)


def test_verb_normalised_to_lowercase():
    reg = BarePickerRegistry()
    reg.register("Focus", _items)
    assert reg.has("focus")
    assert reg.has("FOCUS")
    assert reg.verbs() == ("focus",)


def test_decorator_registers_on_global_registry():
    reset_global_picker_registry()
    try:

        @bare_picker("focus")
        def provider() -> list[PickerItem]:
            return _items()

        reg = get_global_picker_registry()
        assert reg.has("focus")
        assert reg.get("focus") is provider
    finally:
        reset_global_picker_registry()


def test_decorator_rejects_empty_verb():
    reset_global_picker_registry()
    try:
        with pytest.raises(ValueError):

            @bare_picker("")
            def _p() -> list[PickerItem]:
                return _items()
    finally:
        reset_global_picker_registry()
