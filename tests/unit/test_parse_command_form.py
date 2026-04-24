"""Unit tests for _parse_command_form — guided vs advanced branching and coercion.

These tests call the pure function directly without spinning up a FastAPI app,
so they run fast and cover every coercion path that cannot easily be driven
through the integration fixture.
"""

from __future__ import annotations

import json

from voice_commander.commands.store import CommandDef
from voice_commander.tool_metadata import ArgMetadata
from voice_commander.web.admin import _parse_command_form

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _string_meta(name: str, required: bool = True) -> ArgMetadata:
    return ArgMetadata(
        name=name, type_str="string", description="", required=required, default=None
    )


def _integer_meta(name: str, required: bool = True) -> ArgMetadata:
    return ArgMetadata(
        name=name, type_str="integer", description="", required=required, default=None
    )


def _boolean_meta(name: str) -> ArgMetadata:
    return ArgMetadata(
        name=name, type_str="boolean", description="", required=False, default=None
    )


# ---------------------------------------------------------------------------
# Guided mode — string field
# ---------------------------------------------------------------------------


def test_guided_string_field_accepted():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="press",
        kwargs_json="{}",
        kwargs_mode="guided",
        kwarg_fields={"combo": "ctrl+a"},
        args_meta={"combo": _string_meta("combo")},
        enabled="true",
    )
    assert isinstance(result, CommandDef)
    assert result.kwargs == {"combo": "ctrl+a"}


# ---------------------------------------------------------------------------
# Guided mode — integer coercion
# ---------------------------------------------------------------------------


def test_guided_integer_valid_coerces():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="wait",
        kwargs_json="{}",
        kwargs_mode="guided",
        kwarg_fields={"ms": "250"},
        args_meta={"ms": _integer_meta("ms")},
        enabled="true",
    )
    assert isinstance(result, CommandDef)
    assert result.kwargs == {"ms": 250}


def test_guided_integer_invalid_returns_error():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="wait",
        kwargs_json="{}",
        kwargs_mode="guided",
        kwarg_fields={"ms": "notanint"},
        args_meta={"ms": _integer_meta("ms")},
        enabled="true",
    )
    assert isinstance(result, str)
    assert "ms" in result
    assert "integer" in result


# ---------------------------------------------------------------------------
# Guided mode — boolean presence check
# ---------------------------------------------------------------------------


def test_guided_boolean_checked_is_true():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="scroll",
        kwargs_json="{}",
        kwargs_mode="guided",
        kwarg_fields={"reverse": "true"},  # checkbox present
        args_meta={"reverse": _boolean_meta("reverse")},
        enabled="true",
    )
    assert isinstance(result, CommandDef)
    assert result.kwargs["reverse"] is True


def test_guided_boolean_unchecked_is_false():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="scroll",
        kwargs_json="{}",
        kwargs_mode="guided",
        kwarg_fields={},  # checkbox absent
        args_meta={"reverse": _boolean_meta("reverse")},
        enabled="true",
    )
    assert isinstance(result, CommandDef)
    assert result.kwargs["reverse"] is False


# ---------------------------------------------------------------------------
# Guided mode — required field missing
# ---------------------------------------------------------------------------


def test_guided_required_missing_returns_error():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="press",
        kwargs_json="{}",
        kwargs_mode="guided",
        kwarg_fields={},  # required field absent
        args_meta={"combo": _string_meta("combo", required=True)},
        enabled="true",
    )
    assert isinstance(result, str)
    assert "combo" in result
    assert "missing" in result


# ---------------------------------------------------------------------------
# Guided mode — optional absent field is omitted from kwargs
# ---------------------------------------------------------------------------


def test_guided_optional_absent_omitted():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="open",
        kwargs_json="{}",
        kwargs_mode="guided",
        kwarg_fields={},  # optional field absent — should not appear in kwargs
        args_meta={"target": _string_meta("target", required=False)},
        enabled="true",
    )
    assert isinstance(result, CommandDef)
    assert "target" not in result.kwargs


# ---------------------------------------------------------------------------
# Advanced mode — JSON parse
# ---------------------------------------------------------------------------


def test_advanced_mode_parses_json():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="press",
        kwargs_json=json.dumps({"combo": "ctrl+z"}),
        kwargs_mode="advanced",
        kwarg_fields={},
        args_meta={"combo": _string_meta("combo")},
        enabled="true",
    )
    assert isinstance(result, CommandDef)
    assert result.kwargs == {"combo": "ctrl+z"}


def test_advanced_mode_invalid_json_returns_error():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="press",
        kwargs_json="{bad json}",
        kwargs_mode="advanced",
        kwarg_fields={},
        args_meta={},
        enabled="true",
    )
    assert isinstance(result, str)
    assert "JSON" in result or "json" in result.lower()


def test_advanced_mode_non_object_json_returns_error():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="press",
        kwargs_json="[1, 2, 3]",
        kwargs_mode="advanced",
        kwarg_fields={},
        args_meta={},
        enabled="true",
    )
    assert isinstance(result, str)
    assert "object" in result


# ---------------------------------------------------------------------------
# No args_meta falls through to JSON parse
# ---------------------------------------------------------------------------


def test_no_args_meta_falls_through_to_json():
    result = _parse_command_form(
        name="cmd",
        description="",
        synonyms="",
        primitive="unknown",
        kwargs_json=json.dumps({"x": 1}),
        kwargs_mode="guided",
        kwarg_fields={},
        args_meta={},  # empty — triggers JSON fallback
        enabled="true",
    )
    assert isinstance(result, CommandDef)
    assert result.kwargs == {"x": 1}
