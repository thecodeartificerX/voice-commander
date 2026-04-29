"""Unit tests for the unified GraphStore (commands.json / workflows.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_commander.commands.graph import (
    Graph,
    GraphInput,
    Node,
)
from voice_commander.commands.store import GraphStore, GraphStoreError


def _make_graph(name: str = "search_web", kind: str = "command") -> Graph:
    return Graph(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        description="example",
        synonyms=("search for",),
        inputs=(GraphInput(name="query", type="str", required=True, description=""),),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(Node(id="n1", ref="pipeline.focus", kwargs={"target": "comet"}, pos=(0, 0)),),
        edges=(),
    )


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")

    g = _make_graph()
    store.save_one(g)

    loaded = store.load_all()
    assert g.name in loaded
    assert loaded[g.name] == g


def test_load_rejects_legacy_schema(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    payload = {"commands": {"new_tab": {"primitive": "press", "kwargs": {"combo": "ctrl+t"}}}}
    p.write_text(json.dumps(payload))
    store = GraphStore(p, kind="command")
    with pytest.raises(GraphStoreError) as exc:
        store.load_all()
    assert "legacy" in str(exc.value).lower() or "schema_version" in str(exc.value).lower()


def test_save_one_atomic_write(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    store.save_one(_make_graph("a"))
    store.save_one(_make_graph("b"))
    loaded = store.load_all()
    assert {"a", "b"} <= loaded.keys()


def test_delete(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    store.save_one(_make_graph("a"))
    assert store.delete("a") is True
    assert store.delete("a") is False
    assert "a" not in store.load_all()


def test_kind_mismatch_rejected(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    with pytest.raises(GraphStoreError):
        store.save_one(_make_graph("a", kind="workflow"))


def test_rename_swaps_key_and_updates_name_field(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    store.save_one(_make_graph("test"))

    store.rename("test", "open_notepad")

    loaded = store.load_all()
    assert "test" not in loaded
    assert "open_notepad" in loaded
    assert loaded["open_notepad"].name == "open_notepad"


def test_rename_missing_source_raises(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    with pytest.raises(GraphStoreError, match="not found"):
        store.rename("missing", "anything")


def test_rename_collision_raises(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    store.save_one(_make_graph("a"))
    store.save_one(_make_graph("b"))
    with pytest.raises(GraphStoreError, match="already exists"):
        store.rename("a", "b")


def test_rename_invalid_new_name_raises(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    store.save_one(_make_graph("a"))
    with pytest.raises(GraphStoreError):
        store.rename("a", "Has Spaces")


def test_rename_same_name_is_noop(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    store.save_one(_make_graph("a"))
    store.rename("a", "a")
    assert "a" in store.load_all()


def test_duplicate_creates_copy_suffix(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    store.save_one(_make_graph("reopen_tab"))

    new_graph = store.duplicate("reopen_tab")

    assert new_graph.name == "reopen_tab_copy"
    loaded = store.load_all()
    assert "reopen_tab" in loaded
    assert "reopen_tab_copy" in loaded
    # full body preserved (synonyms, nodes, etc.)
    assert loaded["reopen_tab_copy"].synonyms == loaded["reopen_tab"].synonyms
    assert loaded["reopen_tab_copy"].nodes == loaded["reopen_tab"].nodes


def test_duplicate_increments_suffix_on_collision(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    store.save_one(_make_graph("a"))
    store.duplicate("a")  # → a_copy
    g2 = store.duplicate("a")  # → a_copy2
    g3 = store.duplicate("a")  # → a_copy3

    assert g2.name == "a_copy2"
    assert g3.name == "a_copy3"
    loaded = store.load_all()
    assert {"a", "a_copy", "a_copy2", "a_copy3"} <= loaded.keys()


def test_duplicate_missing_source_raises(tmp_path: Path) -> None:
    p = tmp_path / "commands.json"
    store = GraphStore(p, kind="command")
    with pytest.raises(GraphStoreError, match="not found"):
        store.duplicate("missing")
