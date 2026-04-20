from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass


class DuplicateToolError(Exception):
    pass


@dataclass(frozen=True)
class ToolEntry:
    name: str
    phrases: tuple[str, ...]
    func: Callable[[], None]
    module: str
    docstring: str | None


class ToolRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        if entry.name in self._by_name:
            raise DuplicateToolError(f"Tool '{entry.name}' already registered")
        self._by_name[entry.name] = entry

    def all(self) -> list[ToolEntry]:
        return sorted(self._by_name.values(), key=lambda e: e.name)

    def by_name(self, name: str) -> ToolEntry | None:
        return self._by_name.get(name)

    def flat_phrases(self) -> list[tuple[str, str]]:
        return [(p, e.name) for e in self._by_name.values() for p in e.phrases]

    def __len__(self) -> int:
        return len(self._by_name)


_GLOBAL_REGISTRY = ToolRegistry()


def get_global_registry() -> ToolRegistry:
    return _GLOBAL_REGISTRY


def reset_global_registry() -> None:
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = ToolRegistry()


_NORMALIZE_RE = re.compile(r"[^a-z0-9 ]+")


def _normalize(phrase: str) -> str:
    lowered = phrase.lower().strip()
    cleaned = _NORMALIZE_RE.sub(" ", lowered)
    return " ".join(cleaned.split())


def tool(phrases: list[str]) -> Callable[[Callable[[], None]], Callable[[], None]]:
    if not phrases:
        raise ValueError("@tool requires at least one phrase")

    def wrap(func: Callable[[], None]) -> Callable[[], None]:
        normalized = tuple(_normalize(p) for p in phrases)
        entry = ToolEntry(
            name=func.__name__,
            phrases=normalized,
            func=func,
            module=func.__module__,
            docstring=(func.__doc__ or "").strip() or None,
        )
        get_global_registry().register(entry)
        return func

    return wrap


def discover(package: str = "voice_commander.tools") -> ToolRegistry:
    pkg = importlib.import_module(package)
    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{package}.{name}")
    return get_global_registry()
