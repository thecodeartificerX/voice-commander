"""Unit tests for voice_commander.tool_schema.sig_to_json_schema."""
# NOTE: Do NOT add `from __future__ import annotations` here.
# typing.get_type_hints() resolves annotations using a function's module
# globals. With PEP 563 deferred evaluation every annotation becomes a string
# and get_type_hints() needs to resolve it back — that only works when the
# required names (Literal, etc.) are actually present in the module namespace.
# Since we import Literal below it would resolve fine in practice, but
# avoiding __future__ annotations keeps the code path honest and avoids
# accidental string-annotation surprises in the helper functions under test.

from typing import Literal

import pytest

from voice_commander.tool_metadata import ArgMetadata
from voice_commander.tool_schema import ToolSchemaError, sig_to_json_schema

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema(func, args_meta=None, *, tool_name=None, description=""):
    """Thin wrapper that returns the inner ``function`` dict for brevity."""
    result = sig_to_json_schema(
        func,
        args_meta or {},
        tool_name=tool_name,
        description=description,
    )
    return result["function"]


def _props(func, args_meta=None, **kwargs):
    return _schema(func, args_meta, **kwargs)["parameters"]["properties"]


def _required(func, args_meta=None, **kwargs):
    return _schema(func, args_meta, **kwargs)["parameters"]["required"]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_str_param():
    def fn(x: str) -> None: ...

    props = _props(fn)
    assert props == {"x": {"type": "string"}}


def test_int_param():
    def fn(n: int) -> None: ...

    props = _props(fn)
    assert props == {"n": {"type": "integer"}}


def test_float_param():
    def fn(v: float) -> None: ...

    props = _props(fn)
    assert props == {"v": {"type": "number"}}


def test_bool_param():
    def fn(flag: bool) -> None: ...

    props = _props(fn)
    assert props == {"flag": {"type": "boolean"}}


def test_literal_param():
    def fn(mode: Literal["a", "b"]) -> None: ...

    props = _props(fn)
    assert props == {"mode": {"type": "string", "enum": ["a", "b"]}}


def test_optional_str_param():
    def fn(name: str | None) -> None: ...

    props = _props(fn)
    assert props == {"name": {"type": ["string", "null"]}}


def test_default_not_required():
    def fn(x: str, y: int = 0) -> None: ...

    req = _required(fn)
    assert "x" in req
    assert "y" not in req


def test_no_args_function():
    def fn() -> None: ...

    result = _schema(fn)
    assert result["parameters"]["properties"] == {}
    assert result["parameters"]["required"] == []


def test_unsupported_type_raises():
    def fn(items: list) -> None: ...

    with pytest.raises(ToolSchemaError) as exc_info:
        sig_to_json_schema(fn, {})

    err = exc_info.value
    assert err.param_name == "items"
    assert "unsupported" in err.reason


def test_description_from_metadata():
    def fn(path: str) -> None: ...

    meta = ArgMetadata(
        name="path",
        type_str="str",
        description="The file path to open.",
        required=True,
        default=None,
    )
    props = _props(fn, {"path": meta})
    assert props["path"]["description"] == "The file path to open."
    # Type mapping should still be present alongside description.
    assert props["path"]["type"] == "string"


def test_tool_schema_exposes_returns_meta():
    from voice_commander.tool_schema import describe_tool_for_builder
    from voice_commander.registry import ToolEntry

    entry = ToolEntry(
        name="focus",
        phrases=(),
        func=lambda **kw: None,
        module="x",
        docstring=None,
        params_schema={"type": "function", "function": {"name": "focus", "parameters": {"type": "object", "properties": {}, "required": []}}},
        returns_meta={"hwnd": {"type": "integer", "description": "x"}},
    )
    out = describe_tool_for_builder(entry)
    assert out["returns"] == {"hwnd": {"type": "integer", "description": "x"}}
