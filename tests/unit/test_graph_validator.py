"""Tests for graph_validator — all 7 rules."""
from __future__ import annotations

from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_validator import (
    FOREACH_GLOBAL_CEILING,
    ValidationSeverity,
    validate,
)
from voice_commander.registry import ToolEntry, ToolRegistry


def _empty_graph(name: str = "g", kind: str = "command") -> Graph:
    return Graph(
        name=name, kind=kind,  # type: ignore[arg-type]
        description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(), edges=(),
    )


def _reg_with(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for name in names:
        reg.register(ToolEntry(
            name=name, phrases=(), func=lambda **kw: None,
            module="x", docstring=None, internal=True,
        ))
    return reg


# --- Rule 1: intra-cycle ---

def test_intra_graph_cycle_is_error():
    g = Graph(
        name="cyc", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("a", "pipeline.x", {}), Node("b", "pipeline.x", {})),
        edges=(
            Edge(PortRef("a", "ok"), PortRef("b", "in")),
            Edge(PortRef("b", "ok"), PortRef("a", "in")),
        ),
    )
    errors = validate(g, registry=None, peers={})
    assert any(e.severity == ValidationSeverity.ERROR and "cycle" in e.message for e in errors)


# --- Rule 2: unknown-ref ---

def test_unknown_pipeline_ref_is_error():
    g = Graph(
        name="g", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "pipeline.bogus", {}),), edges=(),
    )
    errors = validate(g, registry=_reg_with("press"), peers={})
    assert any(e.severity == ValidationSeverity.ERROR for e in errors)


def test_unknown_command_ref_is_error():
    g = Graph(
        name="g", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "command.bogus", {}),), edges=(),
    )
    errors = validate(g, registry=_reg_with("press"), peers={})
    assert any(e.severity == ValidationSeverity.ERROR for e in errors)


def test_builtin_control_ref_is_ok():
    g = Graph(
        name="g", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "control.branch", {}),), edges=(),
    )
    errors = validate(g, registry=_reg_with(), peers={})
    assert not any(
        e.severity == ValidationSeverity.ERROR and "unknown" in e.message for e in errors
    )


# --- Rule 3: orphan required port ---

def test_required_input_unbound_is_error():
    reg = ToolRegistry()
    reg.register(ToolEntry(
        name="type", phrases=(), func=lambda **kw: None, module="x", docstring=None,
        internal=True,
        args_meta={"text": {"type": "string", "required": True, "description": ""}},
    ))

    g = Graph(
        name="g", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "pipeline.type", {}),),
        edges=(),
    )
    errs = validate(g, registry=reg, peers={})
    hard = [e for e in errs if e.severity == ValidationSeverity.ERROR]
    assert any(
        "required" in e.message.lower() and e.node_id == "n1" and e.port == "text"
        for e in hard
    )


def test_required_input_satisfied_by_baked_kwarg_is_ok():
    reg = ToolRegistry()
    reg.register(ToolEntry(
        name="type", phrases=(), func=lambda **kw: None, module="x", docstring=None,
        internal=True,
        args_meta={"text": {"type": "string", "required": True, "description": ""}},
    ))
    g = Graph(
        name="g", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n1", "pipeline.type", {"text": "hi"}),),
        edges=(),
    )
    errs = validate(g, registry=reg, peers={})
    hard = [
        e for e in errs
        if e.severity == ValidationSeverity.ERROR and e.node_id == "n1" and e.port == "text"
    ]
    assert not hard


# --- Rule 4: foreach cap ---

def test_foreach_cap_exceeds_ceiling_is_error():
    g = Graph(
        name="g", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=FOREACH_GLOBAL_CEILING + 1,
        nodes=(Node("f1", "control.foreach", {"cap": FOREACH_GLOBAL_CEILING + 1}),),
        edges=(),
    )
    errs = validate(g, registry=None, peers={})
    assert any(
        e.severity == ValidationSeverity.ERROR and "cap" in e.message and e.node_id == "f1"
        for e in errs
    )


def test_foreach_cap_at_ceiling_is_ok():
    g = Graph(
        name="g", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=FOREACH_GLOBAL_CEILING,
        nodes=(Node("f1", "control.foreach", {"cap": FOREACH_GLOBAL_CEILING}),),
        edges=(),
    )
    errs = validate(g, registry=None, peers={})
    hard = [e for e in errs if e.severity == ValidationSeverity.ERROR and "cap" in e.message]
    assert not hard


# --- Rule 5: branch unreachable warning ---

def test_branch_with_no_outgoing_warns():
    g = Graph(
        name="g", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(
            Node("c1", "value.constant", {"value": True}),
            Node("b1", "control.branch", {}),
        ),
        edges=(Edge(PortRef("c1", "value"), PortRef("b1", "cond")),),
    )
    errs = validate(g, registry=None, peers={})
    warns = [e for e in errs if e.severity == ValidationSeverity.WARNING]
    assert any("branch" in w.message.lower() and w.node_id == "b1" for w in warns)


# --- Rule 6: cross-graph cycle ---

def test_cross_graph_cycle_is_error():
    a = Graph(
        name="a", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n", "command.b", {}),), edges=(),
    )
    b = Graph(
        name="b", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node("n", "command.a", {}),), edges=(),
    )
    errs = validate(a, registry=None, peers={"b": b})
    hard = [e for e in errs if e.severity == ValidationSeverity.ERROR]
    assert any("cycle" in e.message.lower() and "a" in e.message and "b" in e.message for e in hard)


# --- Rule 7: name collision ---

def test_name_collision_is_error():
    a = Graph(name="dup", kind="command", description="", synonyms=(), inputs=(),
              llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
              foreach_iteration_cap=50, nodes=(), edges=())
    a_other = Graph(name="dup", kind="workflow", description="", synonyms=(), inputs=(),
                    llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
                    foreach_iteration_cap=50, nodes=(), edges=())
    errs = validate(a, registry=None, peers={"dup": a_other})
    hard = [e for e in errs if e.severity == ValidationSeverity.ERROR]
    assert any("collision" in e.message.lower() or "duplicate" in e.message.lower() for e in hard)
