import time
import pytest
from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def _make_reg_with_fail() -> tuple[ToolRegistry, list]:
    log: list = []
    reg = ToolRegistry()

    def _ok(**kw):
        log.append("ok")

    def _fail(**kw):
        raise RuntimeError("tool failed")

    reg.register(ToolEntry(name="ok_tool", phrases=(), func=_ok, module="x", docstring=None, internal=True))
    reg.register(ToolEntry(name="fail_tool", phrases=(), func=_fail, module="x", docstring=None, internal=True))
    return reg, log


def test_strict_true_halts_on_first_failure():
    reg, log = _make_reg_with_fail()
    g = Graph(
        name="test", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=True, enabled=True, timeout_ms=5000,
        nodes=(
            Node(id="n1", ref="pipeline.fail_tool", kwargs={}),
            Node(id="n2", ref="pipeline.ok_tool", kwargs={}),
        ),
        edges=(Edge(PortRef("n1", "ok"), PortRef("n2", "in")),),
        foreach_iteration_cap=50,
    )
    outcome, _ = GraphRuntime(registry=reg, graph_lookup=lambda n: None).run(g, {})
    assert outcome.status == "error"
    assert log == []  # ok_tool never ran


def test_strict_false_continues_after_failure():
    reg, log = _make_reg_with_fail()
    g = Graph(
        name="test", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=False, enabled=True, timeout_ms=5000,
        nodes=(
            Node(id="n1", ref="pipeline.fail_tool", kwargs={}),
            Node(id="n2", ref="pipeline.ok_tool",   kwargs={}),
        ),
        edges=(),  # no control dependency — both run in topo order
        foreach_iteration_cap=50,
    )
    outcome, _ = GraphRuntime(registry=reg, graph_lookup=lambda n: None).run(g, {})
    assert outcome.status == "error"
    assert "ok" in log  # ok_tool ran despite fail_tool failing


def test_timeout_aborts_graph():
    reg = ToolRegistry()

    def _slow(**kw):
        time.sleep(0.1)

    reg.register(ToolEntry(name="slow", phrases=(), func=_slow, module="x", docstring=None, internal=True))

    g = Graph(
        name="test", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=True, enabled=True, timeout_ms=10,  # 10ms
        nodes=(
            Node(id="n1", ref="pipeline.slow", kwargs={}),
            Node(id="n2", ref="pipeline.slow", kwargs={}),
            Node(id="n3", ref="pipeline.slow", kwargs={}),
        ),
        edges=(
            Edge(PortRef("n1", "ok"), PortRef("n2", "in")),
            Edge(PortRef("n2", "ok"), PortRef("n3", "in")),
        ),
        foreach_iteration_cap=50,
    )
    outcome, _ = GraphRuntime(registry=reg, graph_lookup=lambda n: None).run(g, {})
    assert outcome.status == "error"
    assert "timeout" in (outcome.error_msg or "")
