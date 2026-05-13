"""Bare-primitive picker registry.

The registry maps a primitive verb name (``"focus"``, ``"open"``, …) to a
zero-arg :class:`PickerProvider`. The verb name is lowercased on register
and lookup so casing differences from the transcript pipeline cannot cause
a miss.

The ``@bare_picker(verb)`` decorator registers on the module-level global
singleton, mirroring how :func:`voice_commander.registry.tool` works for
primitive tools.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from voice_commander.picker.types import PickerProvider

P = TypeVar("P", bound=PickerProvider)


class DuplicatePickerError(Exception):
    """Raised when two providers try to register the same verb."""


class BarePickerRegistry:
    def __init__(self) -> None:
        self._by_verb: dict[str, PickerProvider] = {}

    def register(self, verb: str, provider: PickerProvider) -> None:
        key = verb.lower().strip()
        if not key:
            raise ValueError("verb must be a non-empty string")
        if key in self._by_verb:
            raise DuplicatePickerError(f"Picker for verb {key!r} already registered")
        self._by_verb[key] = provider

    def get(self, verb: str) -> PickerProvider | None:
        return self._by_verb.get(verb.lower().strip())

    def has(self, verb: str) -> bool:
        return verb.lower().strip() in self._by_verb

    def verbs(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_verb))


_GLOBAL_PICKER_REGISTRY = BarePickerRegistry()


def get_global_picker_registry() -> BarePickerRegistry:
    return _GLOBAL_PICKER_REGISTRY


def reset_global_picker_registry() -> None:
    """Test hook — drop every registered provider."""
    global _GLOBAL_PICKER_REGISTRY
    _GLOBAL_PICKER_REGISTRY = BarePickerRegistry()


def bare_picker(verb: str) -> Callable[[P], P]:
    """Register *func* as the provider for bare invocation of *verb*.

    Usage::

        @bare_picker("focus")
        def focus_picker() -> list[PickerItem]:
            ...
    """

    def _register(func: P) -> P:
        get_global_picker_registry().register(verb, func)
        return func

    return _register
