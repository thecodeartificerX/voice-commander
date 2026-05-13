"""Picker primitive types — items + the provider callable shape.

Kept in a separate module so consumers can import the types without dragging
in the `MruTracker` (which imports pywin32).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from voice_commander.plan import Plan


@dataclass(frozen=True)
class PickerItem:
    """One row of a picker — what the modal shows + what to dispatch on selection.

    Attributes
    ----------
    label : str
        Display text rendered next to the number in the modal
        (e.g. ``"Chrome — voice-commander"``).
    action : Plan
        The plan dispatched when this item is selected. Pre-built by the
        provider so dispatch needs no extra resolution at selection time.
    """

    label: str
    action: Plan


PickerProvider = Callable[[], list[PickerItem]]
"""Zero-arg callable that produces a fresh list of items every time the
picker opens. Empty list means "nothing to show" — the framework will
miss-chime instead of opening an empty modal."""
