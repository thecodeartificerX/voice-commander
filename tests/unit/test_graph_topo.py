import pytest

from voice_commander.commands.graph import Edge, Node, PortRef
from voice_commander.commands.graph_topo import CycleError, topo_sort


def _node(id_: str) -> Node:
    return Node(id=id_, ref="pipeline.press", kwargs={"combo": "ctrl+a"}, pos=(0, 0))


def test_linear_chain():
    nodes = (_node("n1"), _node("n2"), _node("n3"))
    edges = (
        Edge(PortRef("n1", "ok"), PortRef("n2", "in")),
        Edge(PortRef("n2", "ok"), PortRef("n3", "in")),
    )
    order = topo_sort(nodes, edges)
    assert [n.id for n in order] == ["n1", "n2", "n3"]


def test_diamond():
    nodes = tuple(_node(n) for n in ("n1", "n2", "n3", "n4"))
    edges = (
        Edge(PortRef("n1", "ok"), PortRef("n2", "in")),
        Edge(PortRef("n1", "ok"), PortRef("n3", "in")),
        Edge(PortRef("n2", "ok"), PortRef("n4", "in")),
        Edge(PortRef("n3", "ok"), PortRef("n4", "in")),
    )
    order = [n.id for n in topo_sort(nodes, edges)]
    assert order.index("n1") < order.index("n2") < order.index("n4")
    assert order.index("n1") < order.index("n3") < order.index("n4")


def test_cycle_raises():
    nodes = (_node("n1"), _node("n2"))
    edges = (
        Edge(PortRef("n1", "ok"), PortRef("n2", "in")),
        Edge(PortRef("n2", "ok"), PortRef("n1", "in")),
    )
    with pytest.raises(CycleError):
        topo_sort(nodes, edges)


def test_disconnected_components():
    nodes = (_node("n1"), _node("n2"), _node("n3"))
    edges = (Edge(PortRef("n1", "ok"), PortRef("n2", "in")),)
    order = [n.id for n in topo_sort(nodes, edges)]
    assert set(order) == {"n1", "n2", "n3"}
    assert order.index("n1") < order.index("n2")
