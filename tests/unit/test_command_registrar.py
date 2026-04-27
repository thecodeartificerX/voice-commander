"""Tests for the rewritten graph registrar."""

from __future__ import annotations

from pathlib import Path

from voice_commander.commands.graph import Graph, Node
from voice_commander.commands.registrar import register_graphs, reload_all
from voice_commander.commands.store import GraphStore
from voice_commander.registry import ToolEntry, ToolRegistry


def _make_graph(
    name: str,
    kind: str = "command",
    *,
    description: str = "",
    synonyms: tuple[str, ...] = (),
    enabled: bool = True,
    nodes: tuple = (),
    edges: tuple = (),
) -> Graph:
    return Graph(
        name=name,
        kind=kind,
        description=description,
        synonyms=synonyms,
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=enabled,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=nodes,
        edges=edges,
    )


def _press_registry(pressed: list[str]) -> ToolRegistry:
    reg = ToolRegistry()
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
    return reg


def test_register_graphs_synthesises_tool_entries(tmp_path: Path) -> None:
    """A graph registered via the registrar produces a ToolEntry whose func
    delegates to GraphRuntime.run when invoked."""
    store = GraphStore(tmp_path / "commands.json", kind="command")
    store.save_one(
        _make_graph(
            "example",
            description="ex",
            synonyms=("e",),
            nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+a"}),),
        )
    )

    pressed: list[str] = []
    reg = _press_registry(pressed)

    names = register_graphs(reg, store)
    assert names == ["example"]
    entry = reg.by_name("example")
    assert entry is not None

    entry.func()  # invoking the synthesised closure
    assert pressed == ["ctrl+a"]


def test_register_graphs_excludes_disabled(tmp_path: Path) -> None:
    store = GraphStore(tmp_path / "commands.json", kind="command")
    store.save_one(
        _make_graph(
            "disabled_cmd",
            enabled=False,
            nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+a"}),),
        )
    )

    reg = ToolRegistry()
    names = register_graphs(reg, store)
    assert "disabled_cmd" not in names
    assert reg.by_name("disabled_cmd") is None


def test_reload_all_updates_both_stores(tmp_path: Path) -> None:
    cmd_store = GraphStore(tmp_path / "commands.json", kind="command")
    wf_store = GraphStore(tmp_path / "workflows.json", kind="workflow")

    cmd_store.save_one(_make_graph("cmd1"))
    wf_store.save_one(_make_graph("wf1", "workflow"))

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
    store.save_one(
        _make_graph(
            "shared_name",
            description="store version",
            nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+a"}),),
        )
    )
    # Peer graph with the same name but different combo
    peer_graph = _make_graph(
        "shared_name",
        "workflow",
        description="peer version",
        nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+b"}),),
    )

    pressed: list[str] = []
    reg = _press_registry(pressed)

    register_graphs(reg, store, peer_graphs={"shared_name": peer_graph})

    entry = reg.by_name("shared_name")
    assert entry is not None
    entry.func()
    assert pressed == ["ctrl+a"], "Store graph should win over peer graph"


def test_register_graphs_drops_shadowed_disabled_primitive(tmp_path: Path) -> None:
    """A graph whose name collides with a disabled+internal legacy primitive
    silently replaces it instead of raising DuplicateToolError."""
    store = GraphStore(tmp_path / "commands.json", kind="command")
    store.save_one(
        _make_graph(
            "close_window",
            nodes=(Node("n1", "pipeline.press", {"combo": "alt+f4"}),),
        )
    )

    pressed: list[str] = []
    reg = _press_registry(pressed)
    reg.register(
        ToolEntry(
            name="close_window",
            phrases=(),
            func=lambda: None,
            module="x",
            docstring=None,
            enabled=False,
            internal=True,
            origin="primitive",
        )
    )

    names = register_graphs(reg, store)
    assert names == ["close_window"]
    entry = reg.by_name("close_window")
    assert entry is not None
    assert entry.origin == "command"


def test_register_graphs_peer_graphs_cross_resolution(tmp_path: Path) -> None:
    """When peer_graphs is supplied, the runtime lookup resolves
    references from the peer set — not just the store's own graphs."""
    cmd_store = GraphStore(tmp_path / "commands.json", kind="command")
    wf_store = GraphStore(tmp_path / "workflows.json", kind="workflow")

    # Command graph with a simple press node
    cmd_store.save_one(
        _make_graph(
            "do_press",
            description="press",
            nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+a"}),),
        )
    )
    # Workflow graph that references the command via command.do_press
    wf_store.save_one(
        _make_graph(
            "wf_calls_cmd",
            "workflow",
            description="wf",
            nodes=(Node("n1", "command.do_press", {}),),
        )
    )

    pressed: list[str] = []
    reg = _press_registry(pressed)

    # Register commands first (no peers needed — commands are standalone)
    register_graphs(reg, cmd_store)

    # Register workflows WITH peer_graphs pointing at commands
    names = register_graphs(reg, wf_store, peer_graphs=cmd_store.load_all())
    assert "wf_calls_cmd" in names

    # Invoke the workflow entry — it should delegate through to do_press → press
    entry = reg.by_name("wf_calls_cmd")
    assert entry is not None
    entry.func()
    assert pressed == ["ctrl+a"]
