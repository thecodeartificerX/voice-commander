from pathlib import Path

import pytest

from voice_commander.tool_metadata import ToolMetadata, ToolMetadataError, ToolMetadataStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tools_sidecar"


def test_load_all_returns_all_tools():
    store = ToolMetadataStore(FIXTURES)
    result = store.load_all()
    assert "alpha" in result
    assert "beta" in result
    assert "gamma" in result
    assert len(result) == 3


def test_load_all_category_default():
    store = ToolMetadataStore(FIXTURES)
    result = store.load_all()
    assert result["alpha"].category == "test"
    assert result["gamma"].category == "other"


def test_load_all_phrases():
    store = ToolMetadataStore(FIXTURES)
    result = store.load_all()
    assert result["alpha"].phrases == ("alpha one", "alpha two")


def test_load_all_enabled_flag():
    store = ToolMetadataStore(FIXTURES)
    result = store.load_all()
    assert result["alpha"].enabled is True
    assert result["beta"].enabled is False


def test_load_one():
    store = ToolMetadataStore(FIXTURES)
    store.load_all()  # populate index
    md = store.load_one("alpha")
    assert md.name == "alpha"
    assert md.phrases == ("alpha one", "alpha two")


def test_load_one_missing_raises():
    store = ToolMetadataStore(FIXTURES)
    store.load_all()
    with pytest.raises(ToolMetadataError):
        store.load_one("nonexistent")


def test_path_for():
    store = ToolMetadataStore(FIXTURES)
    store.load_all()
    path = store.path_for("alpha")
    assert path.name == "sample.toml"


def test_save_round_trip(tmp_path):
    # Copy fixture to tmp
    import shutil

    shutil.copy(FIXTURES / "sample.toml", tmp_path / "sample.toml")

    store = ToolMetadataStore(tmp_path)
    store.load_all()

    # Modify and save
    new_md = ToolMetadata(
        name="alpha",
        phrases=("new phrase",),
        description="Updated description.",
        category="test",
        enabled=False,
    )
    store.save("alpha", new_md)

    # Re-read and verify
    store2 = ToolMetadataStore(tmp_path)
    result = store2.load_all()
    assert result["alpha"].phrases == ("new phrase",)
    assert result["alpha"].description == "Updated description."
    assert result["alpha"].enabled is False
    # Other tool in same file should be unchanged
    assert result["beta"].phrases == ("beta phrase",)


def test_save_atomic_uses_tmp_file(tmp_path):
    import shutil

    shutil.copy(FIXTURES / "sample.toml", tmp_path / "sample.toml")

    store = ToolMetadataStore(tmp_path)
    store.load_all()

    new_md = ToolMetadata("alpha", ("x",), "d", "test", True)
    store.save("alpha", new_md)

    # tmp file should be cleaned up
    assert not (tmp_path / "sample.toml.tmp").exists()
