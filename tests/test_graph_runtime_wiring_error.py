"""Tests for WiringError handling inside GraphRuntime."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_wiring_error_importable():
    from voice_commander.commands.graph_runtime import WiringError
    exc = WiringError("test error")
    assert str(exc) == "test error"


def test_wiring_error_is_exception():
    from voice_commander.commands.graph_runtime import WiringError
    assert issubclass(WiringError, Exception)


def test_classify_wiring_error_as_wiring():
    from voice_commander.commands.graph_runtime import WiringError
    from voice_commander.observability.errors import classify
    exc = WiringError("missing kwarg 'right'")
    # classify uses type name; WiringError must match _WIRING_TYPES
    # Update errors.py to import WiringError or match by name
    category = classify(exc, where="graph_runtime")
    assert category == "wiring"


# ----- B-M3: WiringError span must record status="error" + error_category="wiring" -----


def test_wiring_error_span_marked_status_error(tmp_path: Path):
    """B-M3: a WiringError-raising kwarg resolution must end the span with
    ``status="error"`` AND ``error_category="wiring"`` — not status="ok"."""
    from voice_commander.commands.graph import Edge, Graph, Node, PortRef
    from voice_commander.commands.graph_runtime import GraphRuntime
    from voice_commander.event_bus import EventBus
    from voice_commander.observability.store import Store
    from voice_commander.observability.tracer import Tracer
    from voice_commander.registry import ToolEntry, ToolRegistry

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="add",
            phrases=("add",),
            func=lambda **kw: kw.get("left", 0) + kw.get("right", 0),
            module="test",
            docstring="add",
        )
    )
    # Producer tool with NO returns_meta — its output is never recorded into
    # port_values. So a wire from producer.value → consumer.right has no
    # source value at resolve time → WiringError.
    registry.register(
        ToolEntry(
            name="producer",
            phrases=("p",),
            func=lambda **_kw: 99,
            module="test",
            docstring="produces nothing for the wired port",
            # Intentionally leave returns_meta empty so port_values stays empty.
        )
    )

    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    store.start()
    try:
        bus = EventBus()
        tracer = Tracer(store=store, bus=bus, enabled=True)
        runtime = GraphRuntime(registry, lambda _name: None, tracer=tracer)

        graph = Graph(
            name="bad_wiring",
            kind="command",
            description="",
            synonyms=(),
            inputs=(),
            llm_visible=False,
            strict=True,
            enabled=True,
            timeout_ms=5000,
            nodes=(
                Node(id="p", ref="pipeline.producer", kwargs={}),
                Node(id="a", ref="pipeline.add", kwargs={"left": 1}),
            ),
            edges=(
                # Dependency edge so topo runs p before a, but no actual port
                # value lands in port_values for "p.value".
                Edge(PortRef("p", "value"), PortRef("a", "right")),
            ),
            foreach_iteration_cap=10,
        )

        with tracer.run("test") as run:
            runtime.run(graph, {})
        store.flush()

        spans = store.get_spans(run.run_id)
        # The wiring-error span is opened with name=node.ref ("pipeline.add").
        node_spans = [s for s in spans if s["name"] == "pipeline.add"]
        assert node_spans, (
            f"expected a span named pipeline.add; got {[s['name'] for s in spans]}"
        )
        # Even if multiple spans exist (e.g. an outer graph span), the node
        # span representing the failed kwarg-resolution must have status=error
        # and error_category=wiring.
        wiring_spans = [
            s for s in node_spans
            if s["status"] == "error" and s["error_category"] == "wiring"
        ]
        assert wiring_spans, (
            "expected at least one node span with status='error' and "
            f"error_category='wiring'; got: "
            f"{[(s['name'], s['status'], s['error_category']) for s in node_spans]}"
        )
        # Tracer should have captured the WiringError type + traceback.
        ws = wiring_spans[0]
        assert ws["error_type"] == "WiringError"
        assert ws["traceback"]
    finally:
        store.stop()
