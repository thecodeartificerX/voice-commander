"""Integration tests for the multi-page web UI.

Verifies that:

1. ``GET /`` issues a 302 redirect to ``/page/commands``.
2. Each ``/page/{section}`` route renders a full HTML page with the section
   heading and the persistent ``Restart daemon`` button in the layout header.
3. ``GET /guide`` adopts the shared layout (proven by the restart button).
4. The restart button on every page targets ``/restart``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_commander.commands.graph import Graph, GraphInput, Node
from voice_commander.commands.store import GraphStore
from voice_commander.event_bus import EventBus
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ArgMetadata, ToolMetadataStore
from voice_commander.web.app import create_app


def _press_args() -> dict[str, ArgMetadata]:
    return {
        "combo": ArgMetadata(
            name="combo",
            type_str="string",
            description="Key combo",
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
        "[audio]\ndevice = -1\n",
        encoding="utf-8",
    )
    cs = GraphStore(root / "commands.json", kind="command")
    cs.save_one(
        Graph(
            name="copy",
            kind="command",
            description="Copy",
            synonyms=("copy",),
            inputs=(),
            llm_visible=True,
            strict=True,
            enabled=True,
            timeout_ms=5000,
            foreach_iteration_cap=50,
            nodes=(Node("n1", "pipeline.press", {"combo": "ctrl+c"}),),
            edges=(),
        )
    )
    ws = GraphStore(root / "workflows.json", kind="workflow")
    ws.save_one(
        Graph(
            name="say_hi",
            kind="workflow",
            description="",
            synonyms=("hello",),
            inputs=(GraphInput(name="name", type="str", required=True),),
            llm_visible=True,
            strict=True,
            enabled=True,
            timeout_ms=5000,
            foreach_iteration_cap=50,
            nodes=(Node("n1", "pipeline.type", {"text": "Hi {name}"}),),
            edges=(),
        )
    )
    return cs, ws, config_path


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    reg = _primitive_registry()
    cs, ws, config_path = _seed_stores(tmp_path)
    store = ToolMetadataStore(tmp_path / "tools_meta_empty")
    (tmp_path / "tools_meta_empty").mkdir()
    app = create_app(
        reg,
        store,
        threading.Lock(),
        event_bus=EventBus(),
        command_store=cs,
        workflow_store=ws,
        config_path=config_path,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


def test_root_redirects_to_commands_page(client: TestClient) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/page/commands"


# ---------------------------------------------------------------------------
# Per-section pages
# ---------------------------------------------------------------------------


def test_page_commands_renders(client: TestClient) -> None:
    resp = client.get("/page/commands")
    assert resp.status_code == 200
    assert "Commands" in resp.text
    # New command button is an <a> link with href to builder
    assert "+ New command" in resp.text
    assert "/page/builder?kind=command" in resp.text
    # Restart button comes from the shared layout
    assert "Restart daemon" in resp.text


def test_page_workflows_renders(client: TestClient) -> None:
    resp = client.get("/page/workflows")
    assert resp.status_code == 200
    assert "Workflows" in resp.text
    # New workflow button is an <a> link with href to builder
    assert "+ New workflow" in resp.text
    assert "/page/builder?kind=workflow" in resp.text
    assert "Restart daemon" in resp.text


def test_page_prompt_renders(client: TestClient) -> None:
    resp = client.get("/page/prompt")
    assert resp.status_code == 200
    assert "Prompt" in resp.text
    assert "Restart daemon" in resp.text


def test_page_config_renders(client: TestClient) -> None:
    resp = client.get("/page/config")
    assert resp.status_code == 200
    assert "Config" in resp.text
    assert "Restart daemon" in resp.text


def test_page_primitives_renders(client: TestClient) -> None:
    resp = client.get("/page/primitives")
    assert resp.status_code == 200
    assert "Primitives" in resp.text
    assert "Restart daemon" in resp.text


# ---------------------------------------------------------------------------
# Guide adopts the shared layout
# ---------------------------------------------------------------------------


def test_guide_uses_shared_layout(client: TestClient) -> None:
    resp = client.get("/guide")
    assert resp.status_code == 200
    assert "Guide" in resp.text
    # Persistent header proves layout adoption
    assert "Restart daemon" in resp.text


# ---------------------------------------------------------------------------
# Restart button wires up to /restart on every page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/page/commands",
        "/page/workflows",
        "/page/prompt",
        "/page/config",
        "/page/primitives",
        "/guide",
    ],
)
def test_restart_button_targets_restart_endpoint(client: TestClient, url: str) -> None:
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.text
    assert "Restart daemon" in body
    # The restart click handler posts to /restart — it must be wired up.
    assert "/restart" in body


# ---------------------------------------------------------------------------
# Builder page and card actions
# ---------------------------------------------------------------------------


def test_builder_page_contains_drawflow_assets(client: TestClient) -> None:
    """Drawflow assets test — replaced by React SPA (ADR 0071).

    Drawflow was removed in ADR 0071; /page/builder now serves the React SPA
    or a friendly stub. This test confirms the route returns 200 (not 404/500).
    """
    resp = client.get("/page/builder?kind=command")
    assert resp.status_code == 200
    assert resp.text  # non-empty HTML — either SPA or stub


def test_builder_page_links_builder_css(client: TestClient) -> None:
    """builder.css stylesheet link test — replaced by React SPA (ADR 0071).

    The builder template was replaced by the React SPA; CSS is bundled into the
    SPA build.  This test now just confirms the route returns 200.
    """
    resp = client.get("/page/builder?kind=command")
    assert resp.status_code == 200


def test_command_list_has_open_in_builder(client: TestClient) -> None:
    resp = client.get("/commands")
    assert resp.status_code == 200
    assert "Open in Builder" in resp.text


def test_workflow_list_has_open_in_builder(client: TestClient) -> None:
    resp = client.get("/workflows")
    assert resp.status_code == 200
    assert "Open in Builder" in resp.text
