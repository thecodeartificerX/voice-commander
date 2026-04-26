"""Reflect Python function signatures into OpenAI-compatible tool JSON schemas."""

from __future__ import annotations

import inspect
import logging
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .tool_metadata import ArgMetadata
    from .registry import ToolEntry


@dataclass
class ToolSchemaError(Exception):
    """Raised when a function parameter cannot be mapped to JSON schema."""

    tool_name: str
    param_name: str
    reason: str

    def __str__(self) -> str:
        return f"Tool '{self.tool_name}' param '{self.param_name}': {self.reason}"


def describe_tool_for_builder(entry: ToolEntry) -> dict[str, Any]:
    """Return a node-graph-friendly descriptor for *entry*.

    Used by the Node-Graph Builder to render tool nodes with typed input and
    output ports.  The returned dict is intentionally flat so the UI layer can
    consume it without traversing OpenAI-style nested schema.

    Returns a dict shaped like::

        {
            "name": "focus",
            "description": "...",
            "args": {<param_name>: ArgMetadata, ...},
            "returns": {<port_name>: {"type": "...", "description": "..."}, ...},
            "settle_ms": 200,
        }
    """
    return {
        "name": entry.name,
        "description": entry.docstring or "",
        "args": entry.args_meta or {},
        "returns": entry.returns_meta or {},
        "settle_ms": getattr(entry, "settle_ms", 0),
    }


def sig_to_json_schema(
    func: Callable[..., Any],
    args_meta: dict[str, ArgMetadata],
    *,
    tool_name: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Convert a function signature + TOML arg metadata into OpenAI tool schema.

    Uses ``typing.get_type_hints()`` to resolve string annotations produced by
    ``from __future__ import annotations`` back to actual type objects before
    mapping them to JSON Schema.

    Returns a dict shaped like::

        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }
        }
    """
    name = tool_name or func.__name__
    sig = inspect.signature(func)

    # Resolve string annotations → real types (handles `from __future__ import annotations`).
    try:
        hints = typing.get_type_hints(func)
    except Exception:  # pragma: no cover — guard against import-time failures
        hints = {}
        # Surface the failure as a generic ToolSchemaError on the first parameter
        # that can't be resolved; we'll hit the missing-annotation guard below.
        logger.debug(
            "get_type_hints() failed for %s; falling back to empty hints", func, exc_info=True
        )

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        # Skip the return annotation key that get_type_hints() may include.
        if param_name == "return":
            continue

        annotation = hints.get(param_name, inspect.Parameter.empty)
        if annotation is inspect.Parameter.empty:
            raise ToolSchemaError(name, param_name, "missing type annotation")

        prop = _py_type_to_json_schema(annotation, name, param_name)

        # Inject description from TOML metadata when available.
        meta = args_meta.get(param_name)
        if meta and meta.description:
            prop["description"] = meta.description

        properties[param_name] = prop

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or (func.__doc__ or "").strip(),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _py_type_to_json_schema(annotation: Any, tool_name: str, param_name: str) -> dict[str, Any]:
    """Map a Python type annotation to a JSON Schema property dict.

    Supported mappings:

    * ``str``  → ``{"type": "string"}``
    * ``int``  → ``{"type": "integer"}``
    * ``float`` → ``{"type": "number"}``
    * ``bool`` → ``{"type": "boolean"}``
    * ``Literal["a", "b"]`` → ``{"type": "string", "enum": ["a", "b"]}``
    * ``X | None`` / ``Optional[X]`` → inner schema with ``"type": [<t>, "null"]``

    Any other annotation raises :class:`ToolSchemaError`.
    """
    # ------------------------------------------------------------------ scalars
    if annotation is str:
        return {"type": "string"}

    if annotation is int:
        return {"type": "integer"}

    if annotation is float:
        return {"type": "number"}

    if annotation is bool:
        return {"type": "boolean"}

    # ------------------------------------------------------------------ Literal
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        args = typing.get_args(annotation)
        return {"type": "string", "enum": list(args)}

    # ------------------------------------------------------------------ Union / Optional
    # After get_type_hints() resolves annotations, `X | None` in Python 3.10+
    # may come back as either `types.UnionType` or `typing.Union`.  Handle both.
    is_union = origin is typing.Union or (isinstance(annotation, types.UnionType))
    if is_union:
        args = typing.get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        has_none = type(None) in args

        if has_none and len(non_none) == 1:
            # Optional[X] / X | None → inner schema + "null" in type array.
            inner = _py_type_to_json_schema(non_none[0], tool_name, param_name)
            if "type" in inner and isinstance(inner["type"], str):
                inner["type"] = [inner["type"], "null"]
            return inner

        # General unions (e.g. str | int) are not supported.
        raise ToolSchemaError(tool_name, param_name, f"unsupported union type: {annotation}")

    raise ToolSchemaError(tool_name, param_name, f"unsupported type: {annotation}")
