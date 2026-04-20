from pathlib import Path

import pytest

from voice_commander.registry import (
    ToolEntry,
    ToolRegistry,
)
from voice_commander.tool_metadata import ToolMetadataError, ToolMetadataStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tools_sidecar"


def test_missing_toml_for_function_raises():
    """A registered function with no TOML match should raise."""
    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="no_toml_match",
            phrases=(),
            func=lambda: None,
            module="test",
            docstring=None,
        )
    )
    store = ToolMetadataStore(FIXTURES)
    with pytest.raises(ToolMetadataError, match="no_toml_match"):
        registry.bind_metadata(store)


def test_toml_entry_without_function_raises():
    """A TOML entry with no registered function should raise."""
    registry = ToolRegistry()
    # Only register "alpha", leaving "beta" and "gamma" without functions
    registry.register(
        ToolEntry(
            name="alpha",
            phrases=(),
            func=lambda: None,
            module="test",
            docstring=None,
        )
    )
    store = ToolMetadataStore(FIXTURES)
    with pytest.raises(ToolMetadataError):
        registry.bind_metadata(store)


def test_exact_match_succeeds():
    """When every function has a TOML and every TOML has a function, no error."""
    registry = ToolRegistry()
    for name in ("alpha", "beta", "gamma"):
        registry.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=lambda: None,
                module="test",
                docstring=None,
            )
        )
    store = ToolMetadataStore(FIXTURES)
    # Should not raise
    registry.bind_metadata(store)
    assert registry.by_name("alpha").phrases == ("alpha one", "alpha two")
