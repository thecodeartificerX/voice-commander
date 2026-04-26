from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def test_foreach_iterates_body():
    recorded: list = []

    def _record(item):
        recorded.append(item)

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="record",
            phrases=(),
            func=_record,
            module="x",
            docstring=None,
            internal=True,
        )
    )

    g = Graph(
        name="test",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="f1", ref="control.foreach", kwargs={"list": ["a", "b", "c"]}),
            Node(id="r1", ref="pipeline.record", kwargs={}),
        ),
        edges=(
            Edge(PortRef("f1", "item"), PortRef("r1", "in")),
            Edge(PortRef("f1", "item"), PortRef("r1", "item")),
        ),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ = runtime.run(g, {})
    assert outcome.status == "ok"
    assert recorded == ["a", "b", "c"]


def test_foreach_no_stale_port_values():
    """Body node output from iteration N must not leak into iteration N+1."""
    results: list = []

    def _produce(item):
        # Only produce output for first item
        if item == "first":
            return "produced"
        return None

    def _consume(item, value=None):
        results.append((item, value))

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="produce",
            phrases=(),
            func=_produce,
            module="x",
            docstring=None,
            internal=True,
            returns_meta={"value": "str"},
        )
    )
    reg.register(
        ToolEntry(
            name="consume",
            phrases=(),
            func=_consume,
            module="x",
            docstring=None,
            internal=True,
        )
    )

    g = Graph(
        name="test",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="f1", ref="control.foreach", kwargs={"list": ["first", "second"]}),
            Node(id="p1", ref="pipeline.produce", kwargs={}),
            Node(id="c1", ref="pipeline.consume", kwargs={}),
        ),
        edges=(
            Edge(PortRef("f1", "item"), PortRef("p1", "in")),
            Edge(PortRef("f1", "item"), PortRef("p1", "item")),
            Edge(PortRef("p1", "ok"), PortRef("c1", "in")),
            Edge(PortRef("f1", "item"), PortRef("c1", "item")),
            Edge(PortRef("p1", "value"), PortRef("c1", "value")),  # data wire
        ),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ = runtime.run(g, {})
    assert outcome.status == "ok"
    # iteration 1: value="produced"; iteration 2: value=None (cleared, not stale "produced")
    assert results[0] == ("first", "produced")
    assert results[1] == ("second", None)  # must NOT be "produced"


def test_foreach_body_error_surfaces_in_outcome():
    """Body node failure must propagate to PlanOutcome.status='error'."""
    call_count = 0

    def _fail_on_second(item):
        nonlocal call_count
        call_count += 1
        if item == "bad":
            raise RuntimeError("body exploded")

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="maybe_fail",
            phrases=(),
            func=_fail_on_second,
            module="x",
            docstring=None,
            internal=True,
        )
    )

    g = Graph(
        name="test",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="f1", ref="control.foreach", kwargs={"list": ["ok", "bad", "ok2"]}),
            Node(id="m1", ref="pipeline.maybe_fail", kwargs={}),
        ),
        edges=(
            Edge(PortRef("f1", "item"), PortRef("m1", "in")),
            Edge(PortRef("f1", "item"), PortRef("m1", "item")),
        ),
        foreach_iteration_cap=50,
    )

    outcome, _ = GraphRuntime(registry=reg, graph_lookup=lambda n: None).run(g, {})
    assert outcome.status == "error"
    assert outcome.error_msg is not None
    assert "body exploded" in outcome.error_msg
    assert "iter 1" in outcome.error_msg  # iteration index included
    assert call_count == 3  # strict=True stops body nodes within iteration but outer loop continues


def test_foreach_respects_cap():
    recorded: list = []

    def _record(item):
        recorded.append(item)

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="record",
            phrases=(),
            func=_record,
            module="x",
            docstring=None,
            internal=True,
        )
    )

    g = Graph(
        name="test",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="f1", ref="control.foreach", kwargs={"list": ["a", "b", "c", "d"]}),
            Node(id="r1", ref="pipeline.record", kwargs={}),
        ),
        edges=(
            Edge(PortRef("f1", "item"), PortRef("r1", "in")),
            Edge(PortRef("f1", "item"), PortRef("r1", "item")),
        ),
        foreach_iteration_cap=2,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ = runtime.run(g, {})
    assert outcome.status == "ok"
    assert recorded == ["a", "b"]


def test_foreach_body_error_nonstrict_continues_all_iterations():
    """Non-strict graph: body error must not halt remaining iterations."""
    recorded: list = []
    call_count = 0

    def _fail_on_bad(item):
        nonlocal call_count
        call_count += 1
        recorded.append(item)
        if item == "bad":
            raise RuntimeError("body exploded")

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="maybe_fail",
            phrases=(),
            func=_fail_on_bad,
            module="x",
            docstring=None,
            internal=True,
        )
    )

    g = Graph(
        name="test",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=False,  # non-strict: continue on body error
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="f1", ref="control.foreach", kwargs={"list": ["ok1", "bad", "ok2"]}),
            Node(id="m1", ref="pipeline.maybe_fail", kwargs={}),
        ),
        edges=(
            Edge(PortRef("f1", "item"), PortRef("m1", "in")),
            Edge(PortRef("f1", "item"), PortRef("m1", "item")),
        ),
        foreach_iteration_cap=50,
    )

    outcome, _ = GraphRuntime(registry=reg, graph_lookup=lambda n: None).run(g, {})
    # All three iterations must run
    assert call_count == 3
    assert recorded == ["ok1", "bad", "ok2"]
    # Error still surfaces in outcome
    assert outcome.status == "error"
    assert outcome.error_msg is not None
    assert "body exploded" in outcome.error_msg
    assert "iter 1" in outcome.error_msg
