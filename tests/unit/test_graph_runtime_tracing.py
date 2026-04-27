from __future__ import annotations

import time

from voice_commander.commands.graph import Graph, Node
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.event_bus import EventBus
from voice_commander.observability import Store, Tracer
from voice_commander.registry import ToolEntry, ToolRegistry


def _drain(store: Store) -> None:
    while not store._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)


def _make_graph() -> Graph:
    return Graph(
        name="demo",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="n1", ref="pipeline.focus", kwargs={"target": "chrome"}, pos=(0, 0)),
        ),
        edges=(),
        foreach_iteration_cap=10,
    )


def test_graph_runtime_emits_graph_and_node_spans(tmp_path):
    bus = EventBus()
    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=128, daemon_pid=1)
    store.start()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="focus",
            phrases=("focus",),
            func=lambda **_k: 0x42,
            module="m",
            docstring=None,
        )
    )

    g = _make_graph()
    runtime = GraphRuntime(reg, lambda _name: None, tracer=tracer)

    with tracer.run("graph demo") as run:
        outcome, _ret = runtime.run(g, {})

    _drain(store)

    spans = store.get_spans(run.run_id)
    types = [s["type"] for s in spans]
    assert "graph" in types, f"Expected 'graph' span, got {types}"
    assert "node" in types, f"Expected 'node' span, got {types}"
    store.stop()
