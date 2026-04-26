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
