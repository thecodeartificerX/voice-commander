"""Integration test: POST /graph/{name} saves and hot-reloads into registry.

The builder's save endpoint must:
1. Persist the graph to the backing GraphStore.
2. Invoke reload_all_fn() — which calls registrar.reload_all(registry, ...) — so
   the new graph is immediately visible in the ToolRegistry that was passed into
   create_app.

We verify directly against the live command_store and registry objects rather than
through a _current_daemon() reference (which doesn't exist in tests).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_commander.commands.store import GraphStore
from voice_commander.event_bus import EventBus
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ArgMetadata, ToolMetadataStore
from voice_commander.web.app import create_app

# ---------------------------------------------------------------------------
# Fixture helpers (mirrors test_builder_routes.py)
# ---------------------------------------------------------------------------


def _press_args() -> dict[str, ArgMetadata]:
    return {
        "combo": ArgMetadata(
            name="combo",
            type_str="string",
            description="Key combo to press",
            required=True,
            default=None,
        )
    }


def _primitive_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for name in ("press", "focus", "type", "open"):
        reg.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=lambda **_: None,
                module="test",
                docstring=None,
                enabled=True,
                llm_only=True,
                internal=True,
                origin="primitive",
                args_meta=_press_args() if name == "press" else {},
            )
        )
    return reg


def _seed_stores(root: Path) -> tuple[GraphStore, GraphStore, Path]:
    config_path = root / "config.toml"
    config_path.write_text(
        '[llm]\nmodel_id = "test-model"\nendpoint_url = "http://x/"\n',
        encoding="utf-8",
    )
    cs = GraphStore(root / "commands.json", kind="command")
    ws = GraphStore(root / "workflows.json", kind="workflow")
    return cs, ws, config_path


# ---------------------------------------------------------------------------
# Fixture: client + live stores + live registry
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_ctx(tmp_path: Path) -> dict:
    """Returns a dict with 'client', 'registry', 'command_store', 'workflow_store'."""
    reg = _primitive_registry()
    cs, ws, config_path = _seed_stores(tmp_path)
    (tmp_path / "tools_meta_empty").mkdir()
    store = ToolMetadataStore(tmp_path / "tools_meta_empty")
    app = create_app(
        reg,
        store,
        threading.Lock(),
        event_bus=EventBus(),
        command_store=cs,
        workflow_store=ws,
        config_path=config_path,
    )
    return {
        "client": TestClient(app),
        "registry": reg,
        "command_store": cs,
        "workflow_store": ws,
    }


# ---------------------------------------------------------------------------
# Minimal valid graph payloads
# ---------------------------------------------------------------------------


def _graph_payload(name: str, description: str = "A test graph", kind: str = "command") -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "kind": kind,
        "description": description,
        "synonyms": [name.replace("_", " ")],
        "inputs": [],
        "llm_visible": True,
        "strict": True,
        "enabled": True,
        "timeout_ms": 5000,
        "foreach_iteration_cap": 50,
        "nodes": [
            {"id": "n1", "ref": "pipeline.press", "kwargs": {"combo": "ctrl+a"}, "pos": [0, 0]}
        ],
        "edges": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_post_graph_persisted_to_store(app_ctx: dict) -> None:
    """POSTing a new graph writes it to the command_store."""
    client: TestClient = app_ctx["client"]
    cs: GraphStore = app_ctx["command_store"]

    r = client.post("/graph/alpha", json=_graph_payload("alpha"))
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "name": "alpha", "version": 1}

    graphs = cs.load_all()
    assert "alpha" in graphs, "Graph 'alpha' not found in command_store after POST"
    assert graphs["alpha"].description == "A test graph"


@pytest.mark.integration
def test_post_graph_hot_reloads_into_registry(app_ctx: dict) -> None:
    """After POST /graph/{name} the ToolEntry appears in the live registry."""
    client: TestClient = app_ctx["client"]
    reg: ToolRegistry = app_ctx["registry"]

    # Graph must not exist yet
    assert reg.by_name("alpha") is None

    r = client.post("/graph/alpha", json=_graph_payload("alpha"))
    assert r.status_code == 200, r.text

    # reload_all_fn was called; the new graph is now in the registry
    entry = reg.by_name("alpha")
    assert entry is not None, "ToolEntry for 'alpha' not found in registry after hot-reload"
    assert entry.origin == "command"


@pytest.mark.integration
def test_post_updated_description_reflected_in_store(app_ctx: dict) -> None:
    """Two consecutive POSTs — the second one updates the description in the store."""
    client: TestClient = app_ctx["client"]
    cs: GraphStore = app_ctx["command_store"]

    client.post("/graph/beta", json=_graph_payload("beta", description="First"))
    assert cs.load_all()["beta"].description == "First"

    client.post("/graph/beta", json=_graph_payload("beta", description="Updated"))
    assert cs.load_all()["beta"].description == "Updated"


@pytest.mark.integration
def test_post_updated_description_reflected_in_registry(app_ctx: dict) -> None:
    """The registry entry for a re-saved graph picks up the new description."""
    client: TestClient = app_ctx["client"]
    reg: ToolRegistry = app_ctx["registry"]

    client.post("/graph/gamma", json=_graph_payload("gamma", description="Original"))
    assert reg.by_name("gamma") is not None

    client.post("/graph/gamma", json=_graph_payload("gamma", description="Revised"))
    entry = reg.by_name("gamma")
    assert entry is not None
    assert entry.description == "Revised", (
        f"Expected description 'Revised' after update; got {entry.description!r}"
    )


@pytest.mark.integration
def test_post_workflow_graph_registered_in_registry(app_ctx: dict) -> None:
    """A workflow-kind graph POSTed to /graph/{name} is registered with origin='workflow'."""
    client: TestClient = app_ctx["client"]
    reg: ToolRegistry = app_ctx["registry"]

    payload = _graph_payload("my_workflow", kind="workflow")
    r = client.post("/graph/my_workflow", json=payload)
    assert r.status_code == 200, r.text

    entry = reg.by_name("my_workflow")
    assert entry is not None, "ToolEntry for 'my_workflow' not in registry"
    assert entry.origin == "workflow"


@pytest.mark.integration
def test_invalid_graph_does_not_corrupt_registry(app_ctx: dict) -> None:
    """A 422-rejected save must not alter the registry."""
    client: TestClient = app_ctx["client"]
    reg: ToolRegistry = app_ctx["registry"]

    before_count = len(reg)

    bad_payload = _graph_payload("bad_graph")
    bad_payload["nodes"] = [
        {"id": "n1", "ref": "pipeline.nonexistent_tool", "kwargs": {}, "pos": [0, 0]}
    ]
    r = client.post("/graph/bad_graph", json=bad_payload)
    assert r.status_code == 422

    # Registry size must not have grown
    assert len(reg) == before_count
    assert reg.by_name("bad_graph") is None
