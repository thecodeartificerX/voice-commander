"""End-to-end tests for the web admin surface (commands + workflows + config).

Spins up the full FastAPI app via ``TestClient`` against a temporary repo
layout. Verifies that:

1. The dashboard renders.
2. The commands list includes seeded data.
3. Toggle and delete endpoints work correctly.
4. POSTing a config update rewrites ``config.toml`` atomically.
5. Delete endpoints remove entries from both disk and registry.
"""

from __future__ import annotations

import json
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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _press_args() -> dict[str, ArgMetadata]:
    """Return ArgMetadata for the 'press' primitive."""
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
    cs.save_one(Graph(
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
    ))
    ws = GraphStore(root / "workflows.json", kind="workflow")
    ws.save_one(Graph(
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
    ))
    return cs, ws, config_path


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    reg = _primitive_registry()
    cs, ws, config_path = _seed_stores(tmp_path)
    # Minimal ToolMetadataStore for the app — empty dir avoids TOML pairing.
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
# Dashboard
# ---------------------------------------------------------------------------


def test_dashboard_renders(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "Voice Commander" in body
    assert "Commands" in body
    assert "Workflows" in body
    assert "Config" in body


# ---------------------------------------------------------------------------
# Commands list / toggle / delete
# ---------------------------------------------------------------------------


def test_commands_list_includes_seeded(client: TestClient) -> None:
    resp = client.get("/commands")
    assert resp.status_code == 200
    assert "copy" in resp.text


def test_command_delete_removes_file_entry(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/command/copy/delete",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "copy" not in raw["graphs"]


def test_command_toggle_flips_enabled(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/command/copy/toggle",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert raw["graphs"]["copy"]["enabled"] is False


# ---------------------------------------------------------------------------
# Workflows list / toggle / delete
# ---------------------------------------------------------------------------


def test_workflow_delete(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/workflow/say_hi/delete",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    raw = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    assert "say_hi" not in raw["graphs"]


def test_workflow_toggle_flips_enabled(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/workflow/say_hi/toggle",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    raw = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    assert raw["graphs"]["say_hi"]["enabled"] is False


# ---------------------------------------------------------------------------
# Config save
# ---------------------------------------------------------------------------


def test_config_save_rewrites_toml(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/config",
        data={
            "llm_endpoint_url": "http://new-host:1234/v1",
            "llm_model_id": "new-model",
            "llm_default_browser": "comet",
            "llm_timeout_ms": 3000,
            "audio_device": 11,
            "transcription_model_size": "base.en",
            "transcription_min_confidence": 0.45,
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'endpoint_url = "http://new-host:1234/v1"' in text
    assert 'model_id = "new-model"' in text
    assert "device = 11" in text
    assert 'model_size = "base.en"' in text


# ---------------------------------------------------------------------------
# /restart route
# ---------------------------------------------------------------------------


def test_restart_returns_503_when_unsupervised(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VC_SUPERVISED", raising=False)
    resp = client.post("/restart", headers={"HX-Request": "true"})
    assert resp.status_code == 503
    assert "supervisor" in resp.json()["error"].lower()


def test_restart_returns_202_when_supervised(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VC_SUPERVISED", "1")
    # Patch request_restart so the test process doesn't actually exit.
    from voice_commander.commands import restart as restart_mod

    monkeypatch.setattr(restart_mod, "request_restart", lambda delay_s=0.5: None)
    resp = client.post("/restart", headers={"HX-Request": "true"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "restarting"


# ---------------------------------------------------------------------------
# Config save banner: restart-required vs hot-reload
# ---------------------------------------------------------------------------


def test_config_save_restart_required_banner(client: TestClient) -> None:
    # audio_device=3 differs from the seed default (-1) → restart required.
    resp = client.post(
        "/config",
        headers={"HX-Request": "true"},
        data={
            "llm_endpoint_url": "http://x",
            "llm_model_id": "m",
            "llm_default_browser": "chrome",
            "llm_timeout_ms": 1200,
            "audio_device": 3,
            "transcription_model_size": "small.en",
            "transcription_min_confidence": 0.3,
        },
    )
    assert resp.status_code == 200
    assert "require a restart" in resp.text


def test_config_save_hot_reload_banner(client: TestClient) -> None:
    # All restart-required fields match the seed config defaults → hot reload.
    resp = client.post(
        "/config",
        headers={"HX-Request": "true"},
        data={
            "llm_endpoint_url": "http://x",
            "llm_model_id": "m",
            "llm_default_browser": "chrome",
            "llm_timeout_ms": 1200,
            "audio_device": -1,
            "transcription_model_size": "small.en",
            "transcription_min_confidence": 0.4,
        },
    )
    assert resp.status_code == 200
    assert "applied immediately" in resp.text
