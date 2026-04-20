"""Save new phrase via web → matcher picks it up."""

import shutil
import threading
from pathlib import Path

import pytest

from voice_commander.matcher import Matcher
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadata, ToolMetadataStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tools_sidecar"


@pytest.mark.integration
def test_save_new_phrase_matches(tmp_path):
    shutil.copy(FIXTURES / "sample.toml", tmp_path / "sample.toml")

    store = ToolMetadataStore(tmp_path)
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

    reload_lock = threading.Lock()
    registry.bind_metadata(store)
    matcher = Matcher(registry, threshold=80.0, reload_lock=reload_lock)

    # "xerox" should not match anything initially
    result = matcher.match("xerox")
    assert result.tool is None or result.score < 80.0

    # Save "xerox" as a phrase for alpha
    new_md = ToolMetadata("alpha", ("xerox", "alpha one", "alpha two"), "desc", "test", True)
    store.save("alpha", new_md)
    with reload_lock:
        registry.reload_metadata(store)

    # Now "xerox" should match alpha
    result = matcher.match("xerox")
    assert result.tool is not None
    assert result.tool.name == "alpha"
