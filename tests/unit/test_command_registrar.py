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
        name="example",
        kind="command",
        description="ex",
        synonyms=("e",),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+a"}),),
        edges=(),
    )
    store.save_one(g)

    reg = ToolRegistry()
    pressed: list[str] = []
    reg.register(
        ToolEntry(
            name="press",
            phrases=(),
            func=lambda combo: pressed.append(combo),
            module="x",
            docstring=None,
            internal=True,
        )
    )

    names = register_graphs(reg, store)
    assert names == ["example"]
    entry = reg.by_name("example")
    assert entry is not None

    entry.func()  # invoking the synthesised closure
    assert pressed == ["ctrl+a"]


def test_register_graphs_excludes_disabled(tmp_path: Path) -> None:
    store = GraphStore(tmp_path / "commands.json", kind="command")
    g = Graph(
        name="disabled_cmd",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=False,
        timeout_ms=5000,
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

    cmd_store.save_one(
        Graph(
            name="cmd1",
            kind="command",
            description="",
            synonyms=(),
            inputs=(),
            llm_visible=True,
            strict=True,
            enabled=True,
            timeout_ms=5000,
            foreach_iteration_cap=50,
            nodes=(),
            edges=(),
        )
    )
    wf_store.save_one(
        Graph(
            name="wf1",
            kind="workflow",
            description="",
            synonyms=(),
            inputs=(),
            llm_visible=True,
            strict=True,
            enabled=True,
            timeout_ms=5000,
            foreach_iteration_cap=50,
            nodes=(),
            edges=(),
        )
    )

    reg = ToolRegistry()
    cmd_names, wf_names = reload_all(reg, cmd_store, wf_store)
    assert "cmd1" in cmd_names
    assert "wf1" in wf_names
    assert reg.by_name("cmd1") is not None
    assert reg.by_name("wf1") is not None


def test_register_graphs_store_wins_over_peers(tmp_path: Path) -> None:
    """When a graph name exists in both the store and peer_graphs,
    the store's own graph takes precedence over the peer."""
    store = GraphStore(tmp_path / "commands.json", kind="command")

    # Store graph: press ctrl+a
    store_graph = Graph(
        name="shared_name",
        kind="command",
        description="store version",
        synonyms=(),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+a"}),),
        edges=(),
    )
    store.save_one(store_graph)

    # Peer graph with the same name but different combo
    peer_graph = Graph(
        name="shared_name",
        kind="workflow",
        description="peer version",
        synonyms=(),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+b"}),),
        edges=(),
    )

    reg = ToolRegistry()
    pressed: list[str] = []
    reg.register(
        ToolEntry(
            name="press",
            phrases=(),
            func=lambda combo: pressed.append(combo),
            module="x",
            docstring=None,
            internal=True,
        )
    )

    register_graphs(reg, store, peer_graphs={"shared_name": peer_graph})

    entry = reg.by_name("shared_name")
    assert entry is not None
    entry.func()
    assert pressed == ["ctrl+a"], "Store graph should win over peer graph"


def test_register_graphs_peer_graphs_cross_resolution(tmp_path: Path) -> None:
    """When peer_graphs is supplied, the runtime lookup resolves
    references from the peer set — not just the store's own graphs."""
    cmd_store = GraphStore(tmp_path / "commands.json", kind="command")
    wf_store = GraphStore(tmp_path / "workflows.json", kind="workflow")

    # Command graph with a simple press node
    cmd = Graph(
        name="do_press",
        kind="command",
        description="press",
        synonyms=(),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+a"}),),
        edges=(),
    )
    cmd_store.save_one(cmd)

    # Workflow graph that references the command via command.do_press
    wf = Graph(
        name="wf_calls_cmd",
        kind="workflow",
        description="wf",
        synonyms=(),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "command.do_press", {}),),
        edges=(),
    )
    wf_store.save_one(wf)

    reg = ToolRegistry()
    pressed: list[str] = []
    reg.register(
        ToolEntry(
            name="press",
            phrases=(),
            func=lambda combo: pressed.append(combo),
            module="x",
            docstring=None,
            internal=True,
        )
    )

    # Register commands first (no peers needed — commands are standalone)
    register_graphs(reg, cmd_store)

    # Register workflows WITH peer_graphs pointing at commands
    names = register_graphs(
        reg,
        wf_store,
        peer_graphs=cmd_store.load_all(),
    )
    assert "wf_calls_cmd" in names

    # Invoke the workflow entry — it should delegate through to do_press → press
    entry = reg.by_name("wf_calls_cmd")
    assert entry is not None
    entry.func()
    assert pressed == ["ctrl+a"]
