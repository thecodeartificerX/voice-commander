"""Unit tests for the {placeholder} substitution engine."""

from __future__ import annotations

import pytest

from voice_commander.commands.template import TemplateError, substitute


def test_substitute_single_placeholder() -> None:
    out = substitute({"text": "{query}"}, {"query": "hello world"})
    assert out == {"text": "hello world"}


def test_substitute_multiple_placeholders() -> None:
    out = substitute(
        {"text": "{greeting} {name}"},
        {"greeting": "hi", "name": "sakib"},
    )
    assert out == {"text": "hi sakib"}


def test_substitute_non_string_kwargs_pass_through() -> None:
    out = substitute({"count": 5, "flag": True}, {})
    assert out == {"count": 5, "flag": True}


def test_substitute_unknown_placeholder_raises() -> None:
    with pytest.raises(TemplateError, match="Unknown placeholder"):
        substitute({"text": "{missing}"}, {})


def test_substitute_plain_string_passes_through() -> None:
    out = substitute({"combo": "ctrl+t"}, {})
    assert out == {"combo": "ctrl+t"}


def test_substitute_escapes_not_supported_literal_braces_copied() -> None:
    # {123} is not a valid placeholder (digits can't start a name) → copied verbatim.
    out = substitute({"text": "literal {123} text"}, {})
    assert out == {"text": "literal {123} text"}


def test_substitute_returns_new_dict() -> None:
    src = {"text": "{a}"}
    out = substitute(src, {"a": "x"})
    assert out is not src
