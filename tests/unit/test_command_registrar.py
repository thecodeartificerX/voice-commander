"""Tests for the rewritten graph registrar."""
from __future__ import annotations

from pathlib import Path

from voice_commander.commands.graph import Graph, Node
from voice_commander.commands.registrar import register_graphs, reload_all
from voice_commander.commands.store import GraphStore
from voice_commander.registry import ToolEntry, ToolRegistry


def test_register_graphs_synthesises_tool_entries(tmp_path: Path) -> None:
    """A graph registered via the registrar produces a ToolEntry whose func
    delegates to GraphRuntime.run when invoked."""
    store = GraphStore(tmp_path / "commands.json", kind="command")
    g = Graph(
        name="example", kind="command", description="ex", synonyms=("e",),
        inputs=(), llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+a"}),),
        edges=(),
    )
    store.save_one(g)

    reg = ToolRegistry()
    pressed: list[str] = []
    reg.register(ToolEntry(
        name="press", phrases=(), func=lambda combo: pressed.append(combo),
        module="x", docstring=None, internal=True,
    ))

    names = register_graphs(reg, store)
    assert names == ["example"]
    entry = reg.by_name("example")
    assert entry is not None

    entry.func()  # invoking the synthesised closure
    assert pressed == ["ctrl+a"]


def test_register_graphs_excludes_disabled(tmp_path: Path) -> None:
    store = GraphStore(tmp_path / "commands.json", kind="command")
    g = Graph(
        name="disabled_cmd", kind="command", description="", synonyms=(),
        inputs=(), llm_visible=True, strict=True, enabled=False, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+a"}),),
        edges=(),
    )
    store.save_one(g)

    reg = ToolRegistry()
    names = register_graphs(reg, store)
    assert "disabled_cmd" not in names
    assert reg.by_name("disabled_cmd") is None


def test_reload_all_updates_both_stores(tmp_path: Path) -> None:
    cmd_store = GraphStore(tmp_path / "commands.json", kind="command")
    wf_store = GraphStore(tmp_path / "workflows.json", kind="workflow")

    cmd_store.save_one(Graph(
        name="cmd1", kind="command", description="", synonyms=(),
        inputs=(), llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50, nodes=(), edges=(),
    ))
    wf_store.save_one(Graph(
        name="wf1", kind="workflow", description="", synonyms=(),
        inputs=(), llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50, nodes=(), edges=(),
    ))

    reg = ToolRegistry()
    cmd_names, wf_names = reload_all(reg, cmd_store, wf_store)
    assert "cmd1" in cmd_names
    assert "wf1" in wf_names
    assert reg.by_name("cmd1") is not None
    assert reg.by_name("wf1") is not None
