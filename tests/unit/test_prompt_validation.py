"""Tests for the prompt template validation logic."""

from __future__ import annotations

from voice_commander.web.prompt import _validate_template


def test_valid_template_passes() -> None:
    """Template with required placeholder returns None (no error)."""
    result = _validate_template("Hello {default_browser} world.")
    assert result is None


def test_missing_placeholder_fails() -> None:
    """Template without {default_browser} returns error."""
    result = _validate_template("Hello world, no placeholder here.")
    assert result is not None
    assert "default_browser" in result


def test_empty_template_fails() -> None:
    """Empty or whitespace-only template returns error."""
    assert _validate_template("") is not None
    assert _validate_template("   ") is not None
    assert _validate_template("\n\t\n") is not None


def test_oversized_template_fails() -> None:
    """Template exceeding 10000 chars returns error."""
    big = "x" * 10_001 + " {default_browser}"
    result = _validate_template(big)
    assert result is not None
    assert "10,000" in result or "10000" in result


def test_stray_placeholder_fails() -> None:
    """Template with unknown {foo} placeholder returns error."""
    result = _validate_template("Hello {default_browser} and {unknown_var}.")
    assert result is not None
    assert "unknown_var" in result


def test_literal_braces_ok() -> None:
    """Template with {{escaped}} braces passes (Python format syntax)."""
    result = _validate_template("Use {{literal}} braces with {default_browser}.")
    assert result is None
