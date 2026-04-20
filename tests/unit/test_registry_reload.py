import shutil
from pathlib import Path

import pytest

from voice_commander.registry import (
    ToolEntry,
    ToolRegistry,
)
from voice_commander.tool_metadata import ToolMetadata, ToolMetadataError, ToolMetadataStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tools_sidecar"


def _make_registry_with_stubs():
    """Create a registry with stub functions matching fixture TOML names."""
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
    return registry


def test_bind_metadata_pairs_correctly():
    registry = _make_registry_with_stubs()
    store = ToolMetadataStore(FIXTURES)
    registry.bind_metadata(store)

    alpha = registry.by_name("alpha")
    assert alpha.phrases == ("alpha one", "alpha two")
    assert alpha.category == "test"
    assert alpha.enabled is True

    beta = registry.by_name("beta")
    assert beta.enabled is False


def test_bind_metadata_missing_toml_raises():
    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="missing_tool",
            phrases=(),
            func=lambda: None,
            module="test",
            docstring=None,
        )
    )
    store = ToolMetadataStore(FIXTURES)
    with pytest.raises(ToolMetadataError):
        registry.bind_metadata(store)


def test_reload_metadata_updates_phrases(tmp_path):
    shutil.copy(FIXTURES / "sample.toml", tmp_path / "sample.toml")

    registry = ToolRegistry()
    for name in ("alpha", "beta"):
        registry.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=lambda: None,
                module="test",
                docstring=None,
            )
        )

    store = ToolMetadataStore(tmp_path)
    registry.bind_metadata(store)
    assert registry.by_name("alpha").phrases == ("alpha one", "alpha two")

    # Mutate TOML on disk
    new_md = ToolMetadata("alpha", ("updated phrase",), "new desc", "test", True)
    store.save("alpha", new_md)

    # Reload
    registry.reload_metadata(store)
    assert registry.by_name("alpha").phrases == ("updated phrase",)
    assert registry.by_name("alpha").description == "new desc"


def test_reload_preserves_func_identity(tmp_path):
    shutil.copy(FIXTURES / "sample.toml", tmp_path / "sample.toml")

    def sentinel():
        return "original"

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="alpha",
            phrases=(),
            func=sentinel,
            module="test",
            docstring=None,
        )
    )
    registry.register(
        ToolEntry(
            name="beta",
            phrases=(),
            func=lambda: None,
            module="test",
            docstring=None,
        )
    )

    store = ToolMetadataStore(tmp_path)
    registry.bind_metadata(store)
    registry.reload_metadata(store)

    assert registry.by_name("alpha").func is sentinel
