"""Bare-primitive picker framework.

When a primitive verb is uttered without an argument (e.g. ``focus`` alone),
the framework opens a numbered on-screen picker built from a registered
``BarePickerProvider``. The next utterance is coerced to an integer and
dispatched as the chosen item's pre-built ``Plan``.

Public exports are exposed here so callers can import from
``voice_commander.picker`` without reaching into submodules. The export
list grows as submodules land (T1–T9); plan deviation noted because the
plan's literal T0 content would eager-import unwritten modules and break
all downstream tests.
"""

from voice_commander.picker.coerce import coerce_number
from voice_commander.picker.registry import (
    BarePickerRegistry,
    bare_picker,
    get_global_picker_registry,
)
from voice_commander.picker.types import PickerItem, PickerProvider

__all__ = [
    "BarePickerRegistry",
    "PickerItem",
    "PickerProvider",
    "bare_picker",
    "coerce_number",
    "get_global_picker_registry",
]
