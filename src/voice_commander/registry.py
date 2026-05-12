from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeVar, overload

if TYPE_CHECKING:
    from .tool_metadata import ArgMetadata, ToolMetadataStore

F = TypeVar("F", bound=Callable[..., Any])


# ``ToolEntry.origin`` discriminates where an entry came from:
# - "primitive": a raw Python function under ``tools/`` (e.g. ``press``).
# - "command"  : a user-defined named shortcut synthesised from commands.json.
# - "workflow" : a user-defined multi-step macro synthesised from workflows.json.
Origin = Literal["primitive", "command", "workflow"]


class DuplicateToolError(Exception):
    pass


@dataclass
class ToolEntry:
    name: str
    phrases: tuple[str, ...]
    func: Callable[..., Any]
    module: str
    docstring: str | None
    description: str = ""
    category: str = ""
    enabled: bool = True
    params_schema: dict[str, Any] = field(default_factory=dict)
    settle_ms: int = 0
    llm_only: bool = False
    # ``internal`` tools stay in the registry so commands/workflows can
    # dispatch them, but they are hidden from the LLM's tool list — the
    # LLM only ever sees user-curated commands + workflows.
    internal: bool = False
    # ``system`` tools are hidden from web UI surfaces (primitives page,
    # /api/tools endpoint, Builder palette) but remain dispatchable by the
    # LLM router and daemon. Orthogonal to ``internal``.
    system: bool = False
    origin: Origin = "primitive"
    args_meta: dict[str, ArgMetadata] = field(default_factory=dict)  # web UI kwargs form schema
    returns_meta: dict[str, dict[str, str]] = field(default_factory=dict)  # output port schema


class ToolRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        if entry.name in self._by_name:
            raise DuplicateToolError(f"Tool '{entry.name}' already registered")
        self._by_name[entry.name] = entry

    def all(self) -> list[ToolEntry]:
        return sorted(self._by_name.values(), key=lambda e: e.name)

    def all_enabled(self) -> list[ToolEntry]:
        """Return only enabled entries, sorted by name."""
        return sorted(
            (e for e in self._by_name.values() if e.enabled),
            key=lambda e: e.name,
        )

    def by_name(self, name: str) -> ToolEntry | None:
        return self._by_name.get(name)


    def by_origin(self, origin: Origin) -> list[ToolEntry]:
        """Return enabled tools that came from *origin*.

        Convenience helper for the Web UI, which groups cards by source.
        """
        return sorted(
            (e for e in self._by_name.values() if e.origin == origin),
            key=lambda e: e.name,
        )

    def remove(self, name: str) -> bool:
        """Drop *name* from the registry if present.

        Used by hot-reload paths when a user deletes a command or workflow
        from the UI. Returns True when a removal occurred.
        """
        return self._by_name.pop(name, None) is not None

    def bind_metadata(self, store: ToolMetadataStore) -> None:
        """Load all TOML metadata and pair each entry with its registered tool.

        Raises :class:`ToolMetadataError` if any function lacks a TOML match
        or any TOML entry lacks a corresponding registered function.
        """
        from .tool_metadata import ToolMetadataError as _TME

        all_meta = store.load_all()

        # Check that every registered tool has a TOML entry.
        unmatched_funcs = [name for name in self._by_name if name not in all_meta]
        if unmatched_funcs:
            raise _TME(f"Registered tools have no TOML metadata: {unmatched_funcs}")

        # Check that every TOML entry has a registered function.
        unmatched_toml = [name for name in all_meta if name not in self._by_name]
        if unmatched_toml:
            raise _TME(f"TOML metadata entries have no registered tool function: {unmatched_toml}")

        # Apply metadata to entries.
        for name, md in all_meta.items():
            entry = self._by_name[name]
            entry.phrases = tuple(_normalize(p) for p in md.phrases)
            entry.description = md.description
            entry.category = md.category
            entry.enabled = md.enabled
            entry.settle_ms = md.settle_ms
            entry.llm_only = md.llm_only
            entry.internal = md.internal
            entry.system = md.system
            entry.args_meta = dict(md.args)
            entry.returns_meta = dict(md.returns)

    def reload_metadata(self, store: ToolMetadataStore) -> None:
        """Re-read all TOML and update existing entries.

        Unlike :meth:`bind_metadata`, this does not raise on pairing mismatches
        — unknown TOML entries are ignored and unmatched tools keep their current state.
        Useful for hot-reload scenarios.
        """
        all_meta = store.load_all()
        for name, md in all_meta.items():
            entry = self._by_name.get(name)
            if entry is None:
                continue
            entry.phrases = tuple(_normalize(p) for p in md.phrases)
            entry.description = md.description
            entry.category = md.category
            entry.enabled = md.enabled
            entry.settle_ms = md.settle_ms
            entry.llm_only = md.llm_only
            entry.internal = md.internal
            entry.system = md.system
            entry.args_meta = dict(md.args)
            entry.returns_meta = dict(md.returns)

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


@overload
def tool(func: F) -> F: ...


@overload
def tool(*, name: str | None = ...) -> Callable[[F], F]: ...


def tool(
    func: F | None = None,
    *,
    name: str | None = None,
) -> F | Callable[[F], F]:
    """Decorator that registers a function as a voice command tool.

    Supports both bare ``@tool`` and ``@tool(name="...")`` usage.  Phrases are
    not specified here — they are loaded from sidecar TOML files via
    :meth:`ToolRegistry.bind_metadata`.

    Parameters
    ----------
    name:
        Optional LLM-visible name override.  When provided, the tool is
        registered under this name instead of ``func.__name__``.  Used when the
        Python symbol would shadow a builtin (e.g. ``type``, ``open``).
    """

    def _register(fn: F) -> F:
        entry = ToolEntry(
            name=name if name is not None else fn.__name__,
            phrases=(),
            func=fn,
            module=fn.__module__,
            docstring=(fn.__doc__ or "").strip() or None,
        )
        get_global_registry().register(entry)
        return fn

    # Called as @tool (no parens) — func is the decorated function directly.
    if func is not None:
        return _register(func)

    # Called as @tool(...) (with parens / kwargs) — return the decorator.
    return _register


def discover(
    package: str = "voice_commander.tools",
    store: ToolMetadataStore | None = None,
) -> ToolRegistry:
    """Import all tool modules and optionally bind TOML metadata.

    Parameters
    ----------
    package:
        Dotted module path of the tools package (default: ``voice_commander.tools``).
    store:
        If provided, :meth:`ToolRegistry.bind_metadata` is called after all
        modules are imported.  Pass ``None`` to skip metadata binding (useful
        in unit tests that don't need TOML files).
    """
    pkg = importlib.import_module(package)
    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{package}.{name}")

    registry = get_global_registry()

    if store is not None:
        registry.bind_metadata(store)

    return registry
