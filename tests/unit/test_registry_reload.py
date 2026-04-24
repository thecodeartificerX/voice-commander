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

    # Mutate TOML on disk — save() persists description/enabled but not phrases
    new_md = ToolMetadata("alpha", ("updated phrase",), "new desc", "test", True)
    store.save("alpha", new_md)

    # Reload — save() strips phrases from TOML (not persisted), so phrases
    # become empty after the rewrite; description is updated.
    registry.reload_metadata(store)
    assert registry.by_name("alpha").phrases == ()
    assert registry.by_name("alpha").description == "new desc"


def test_bind_metadata_populates_args_meta(tmp_path):
    """bind_metadata must populate entry.args_meta from TOML [args] section."""
    (tmp_path / "with_args.toml").write_text(
        """
category = "test"

[tools.alpha]
phrases = ["alpha one"]
description = "Tool with args."
enabled = true

[tools.alpha.args.combo]
type = "string"
description = "Key combo"
required = true
""",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="alpha",
            phrases=(),
            func=lambda: None,
            module="test",
            docstring=None,
        )
    )
    from voice_commander.tool_metadata import ArgMetadata, ToolMetadataStore

    store = ToolMetadataStore(tmp_path)
    registry.bind_metadata(store)

    entry = registry.by_name("alpha")
    assert "combo" in entry.args_meta
    assert isinstance(entry.args_meta["combo"], ArgMetadata)
    assert entry.args_meta["combo"].type_str == "string"
    assert entry.args_meta["combo"].required is True


def test_reload_metadata_preserves_args_meta(tmp_path):
    """reload_metadata must update entry.args_meta — regression guard for guided form."""
    toml_content = """
category = "test"

[tools.alpha]
phrases = ["alpha one"]
description = "Tool with args."
enabled = true

[tools.alpha.args.combo]
type = "string"
description = "Key combo"
required = true
"""
    (tmp_path / "with_args.toml").write_text(toml_content, encoding="utf-8")

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="alpha",
            phrases=(),
            func=lambda: None,
            module="test",
            docstring=None,
        )
    )
    from voice_commander.tool_metadata import ArgMetadata, ToolMetadataStore

    store = ToolMetadataStore(tmp_path)
    registry.bind_metadata(store)
    # Reload — args_meta must survive the second pass
    registry.reload_metadata(store)

    entry = registry.by_name("alpha")
    assert "combo" in entry.args_meta
    assert isinstance(entry.args_meta["combo"], ArgMetadata)


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
