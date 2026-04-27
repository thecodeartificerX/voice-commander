from __future__ import annotations

import time

from voice_commander.commands.graph import Graph, Node
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.event_bus import EventBus
from voice_commander.observability import Store, Tracer
from voice_commander.registry import ToolEntry, ToolRegistry


def _drain(store: Store) -> None:
    store.flush()


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


def test_foreach_emits_per_iteration_spans(tmp_path):
    """M4: each foreach iteration produces a foreach_iter span."""
    from voice_commander.commands.graph import Edge, PortRef

    bus = EventBus()
    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=128, daemon_pid=1)
    store.start()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    recorded: list[object] = []
    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="record",
            phrases=(),
            func=lambda **kw: recorded.append(kw.get("item")),
            module="x",
            docstring=None,
            internal=True,
        )
    )

    g = Graph(
        name="test_foreach",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="f1", ref="control.foreach", kwargs={"list": ["a", "b"]}),
            Node(id="r1", ref="pipeline.record", kwargs={}),
        ),
        edges=(
            Edge(PortRef("f1", "item"), PortRef("r1", "in")),
            Edge(PortRef("f1", "item"), PortRef("r1", "item")),
        ),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(reg, lambda n: None, tracer=tracer)
    with tracer.run("foreach test") as run:
        runtime.run(g, {})

    _drain(store)
    spans = store.get_spans(run.run_id)
    iter_spans = [s for s in spans if s["type"] == "foreach_iter"]
    assert len(iter_spans) == 2, (
        f"Expected 2 foreach_iter spans, got {len(iter_spans)}: {[s['name'] for s in spans]}"
    )
    store.stop()
