"""Startup validator: catches drift between Python tool signatures and TOML metadata."""

from __future__ import annotations

import inspect
import logging
import sys
import types
import typing
from typing import TYPE_CHECKING, Any

from .config import Config
from .registry import ToolRegistry
from .tool_metadata import ToolMetadataStore

if TYPE_CHECKING:
    from .commands.store import GraphStore

logger = logging.getLogger(__name__)

# Types supported by tool_schema.py
_SUPPORTED_SCALARS = (str, int, float, bool)


def validate(registry: ToolRegistry, store: ToolMetadataStore) -> list[str]:
    """Validate all registered tools against their TOML metadata.

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []
    all_meta = store.load_all()

    for entry in registry.all():
        meta = all_meta.get(entry.name)
        if meta is None:
            # Rule 1: should have been caught by bind_metadata, but double-check
            errors.append(f"[rule1] Tool '{entry.name}' has no TOML metadata")
            continue

        # Get function signature params (skip self, *args, **kwargs)
        try:
            hints = typing.get_type_hints(entry.func)
        except Exception:
            hints = {}
        sig = inspect.signature(entry.func)
        sig_params = {
            name: param
            for name, param in sig.parameters.items()
            if name != "self" and param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD)
        }

        # Rule 2: sig param without TOML arg description
        for pname in sig_params:
            if pname not in meta.args:
                errors.append(
                    f"[rule2] Tool '{entry.name}' param '{pname}' has no "
                    f"[tools.{entry.name}.args.{pname}] in TOML"
                )
            elif not meta.args[pname].description:
                errors.append(
                    f"[rule2] Tool '{entry.name}' param '{pname}' has empty description in TOML"
                )

        # Rule 3: TOML arg not in signature
        for arg_name in meta.args:
            if arg_name not in sig_params:
                errors.append(
                    f"[rule3] Tool '{entry.name}' TOML declares arg '{arg_name}' "
                    f"but function has no such parameter"
                )

        # Rule 4: unsupported type
        for pname in sig_params:
            annotation = hints.get(pname)
            if annotation is None:
                # No annotation — that's an error (tool_schema will fail)
                errors.append(f"[rule4] Tool '{entry.name}' param '{pname}' has no type annotation")
                continue
            if not _is_supported_type(annotation):
                errors.append(
                    f"[rule4] Tool '{entry.name}' param '{pname}' "
                    f"uses unsupported type: {annotation}"
                )

        # Rule 5: settle_ms range
        if meta.settle_ms < 0 or meta.settle_ms > 5000:
            errors.append(
                f"[rule5] Tool '{entry.name}' settle_ms={meta.settle_ms} out of range [0, 5000]"
            )

    # Rule 7: required primitives (only when primitives module is discovered)
    # Check if any tool from the primitives module is registered
    has_primitives = any("tools.primitives" in e.module for e in registry.all())
    if has_primitives:
        for required_name in ("wait",):
            prim_entry = registry.by_name(required_name)
            if prim_entry is None:
                errors.append(f"[rule7] Required primitive '{required_name}' not registered")
            elif not prim_entry.llm_only:
                errors.append(f"[rule7] Required primitive '{required_name}' must be llm_only=true")

    return errors


def validate_or_die(registry: ToolRegistry, store: ToolMetadataStore) -> None:
    """Run validation; print errors and sys.exit(1) if any drift detected."""
    errors = validate(registry, store)
    if errors:
        print("Startup validation FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def validate_config(cfg: Config) -> list[str]:
    """Validate runtime config values that cannot be caught by type-checking alone.

    Returns a list of error strings. Empty list means valid.
    """
    return []


def validate_config_or_die(cfg: Config) -> None:
    """Run config validation; print errors and sys.exit(1) if any are found."""
    errors = validate_config(cfg)
    if errors:
        print("Startup config validation FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)


def validate_graphs_or_die(
    command_store: GraphStore,
    workflow_store: GraphStore,
    registry: ToolRegistry,
) -> None:
    """Validate all graphs in both stores. On any ERROR, log and sys.exit(1)."""
    import sys as _sys

    from voice_commander.commands.graph_validator import ValidationSeverity
    from voice_commander.commands.graph_validator import validate as _validate_graph

    all_graphs: dict[str, Any] = {}
    all_graphs.update(command_store.load_all())
    all_graphs.update(workflow_store.load_all())

    peers = dict(all_graphs)
    errors_found = False

    for name, g in all_graphs.items():
        graph_peers = {k: v for k, v in peers.items() if k != name}
        errors = _validate_graph(g, registry=registry, peers=graph_peers)
        for err in errors:
            if err.severity == ValidationSeverity.ERROR:
                logger.error("Graph %r validation error: %s", name, err.message)
                errors_found = True
            else:
                logger.warning("Graph %r validation warning: %s", name, err.message)

    if errors_found:
        logger.error("Graph validation failed — fix the errors above and restart.")
        _sys.exit(1)


def _is_supported_type(annotation: Any) -> bool:
    """Check if a type annotation is in the supported set."""
    if annotation in _SUPPORTED_SCALARS:
        return True

    origin = typing.get_origin(annotation)

    # Literal[...]
    if origin is typing.Literal:
        return True

    # Union / X | None (typing.Union form, e.g. Optional[X])
    if origin is typing.Union:
        args = typing.get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        has_none = type(None) in args
        if has_none and len(non_none) == 1:
            return _is_supported_type(non_none[0])
        return False

    # types.UnionType instance (Python 3.10+ X | Y syntax)
    if isinstance(annotation, types.UnionType):
        args = typing.get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        has_none = type(None) in args
        if has_none and len(non_none) == 1:
            return _is_supported_type(non_none[0])
        return False

    return False
