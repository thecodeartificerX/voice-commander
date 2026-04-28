"""Tests for the show_commands graph in commands.default.json.

Verifies:
- show_commands entry exists and parses without error
- GraphRuntime executes the graph successfully with a mocked pipeline.open
- pipeline.open is called with the commands page URL
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.commands.store import GraphStore
from voice_commander.registry import ToolEntry, ToolRegistry

COMMANDS_DEFAULT_JSON = Path(__file__).parent.parent.parent / "commands.default.json"
COMMANDS_PAGE_URL = "http://127.0.0.1:8765/page/commands"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def show_commands_graph():
    store = GraphStore(COMMANDS_DEFAULT_JSON, kind="command")
    graphs = store.load_all()
    assert "show_commands" in graphs, (
        "show_commands graph not found in commands.default.json — was it added?"
    )
    return graphs["show_commands"]


@pytest.fixture()
def mocked_registry():
    """ToolRegistry with pipeline.open replaced by a spy that records calls."""
    calls: list[str] = []

    def _open(target: str) -> int:
        calls.append(target)
        return 0

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="open",
            phrases=(),
            func=_open,
            module="mock",
            docstring=None,
            internal=True,
        )
    )
    return reg, calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_show_commands_graph_loads(show_commands_graph):
    """show_commands graph parses from commands.default.json without error."""
    g = show_commands_graph
    assert g.name == "show_commands"
    assert g.kind == "command"
    assert g.llm_visible is True
    assert g.enabled is True
    assert len(g.nodes) == 1
    node = g.nodes[0]
    assert node.ref == "pipeline.open"
    assert node.kwargs.get("target") == COMMANDS_PAGE_URL
    assert len(g.synonyms) >= 1, "show_commands must have at least one synonym for voice discovery"


def test_show_commands_graph_runs_ok(show_commands_graph, mocked_registry):
    """GraphRuntime executes show_commands without raising."""
    reg, calls = mocked_registry
    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ = runtime.run(show_commands_graph, inputs={})
    assert outcome.status == "ok", f"Expected ok, got {outcome.status!r}: {outcome.error_msg}"


def test_show_commands_opens_correct_url(show_commands_graph, mocked_registry):
    """pipeline.open is called with the commands page URL."""
    reg, calls = mocked_registry
    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    runtime.run(show_commands_graph, inputs={})
    assert calls == [COMMANDS_PAGE_URL], (
        f"Expected open called with {COMMANDS_PAGE_URL!r}, got {calls!r}"
    )


def test_commands_default_json_is_valid():
    """commands.default.json parses as valid JSON and passes GraphStore load."""
    store = GraphStore(COMMANDS_DEFAULT_JSON, kind="command")
    graphs = store.load_all()
    assert isinstance(graphs, dict)
    assert len(graphs) > 0
