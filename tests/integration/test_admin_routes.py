"""End-to-end tests for the web admin surface (commands + workflows + config).

Spins up the full FastAPI app via ``TestClient`` against a temporary repo
layout. Verifies that:

1. The dashboard renders.
2. POSTing a new command saves it and re-registers it into the registry.
3. POSTing a workflow saves it and its schema shows the declared args.
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
    """Return ArgMetadata for the 'press' primitive — used by guided-mode tests."""
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
# Commands CRUD
# ---------------------------------------------------------------------------


def test_commands_list_includes_seeded(client: TestClient) -> None:
    resp = client.get("/commands")
    assert resp.status_code == 200
    assert "copy" in resp.text


def test_command_save_roundtrip(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/command/new_tab",
        data={
            "description": "Open a new tab",
            "synonyms": "new tab\nopen new tab",
            "primitive": "press",
            "kwargs_json": json.dumps({"combo": "ctrl+t"}),
            "kwargs_mode": "advanced",
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert "new_tab" in resp.text

    # Read back from the store (new schema uses "graphs" key).
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "new_tab" in raw["graphs"]
    saved_node = raw["graphs"]["new_tab"]["nodes"][0]
    assert saved_node["kwargs"] == {"combo": "ctrl+t"}


def test_new_command_name_from_form(client: TestClient, tmp_path: Path) -> None:
    """POST /command/new must use the form-field 'name', not the literal 'new'."""
    resp = client.post(
        "/command/new",
        data={
            "name": "mute_mic",
            "description": "Mute the microphone",
            "synonyms": "mute mic\nmute",
            "primitive": "press",
            "kwargs_json": json.dumps({"combo": "ctrl+shift+m"}),
            "kwargs_mode": "advanced",
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert "mute_mic" in resp.text

    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "mute_mic" in raw["graphs"], "name from form field must be persisted"
    assert "new" not in raw["graphs"], "literal 'new' must not be saved as a command"


def test_new_workflow_name_from_form(client: TestClient, tmp_path: Path) -> None:
    """POST /workflow/new must use the form-field 'name', not the literal 'new'."""
    resp = client.post(
        "/workflow/new",
        data={
            "name": "morning_routine",
            "description": "Run morning routine",
            "synonyms": "morning\nwake up",
            "args_json": json.dumps([]),
            "steps_json": json.dumps(
                [{"ref": "primitive:type", "kwargs": {"text": "Good morning"}}]
            ),
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text

    raw = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    assert "morning_routine" in raw["graphs"], "name from form field must be persisted"
    assert "new" not in raw["graphs"], "literal 'new' must not be saved as a workflow"


def test_new_command_empty_name_rejected(client: TestClient, tmp_path: Path) -> None:
    """POST /command/new with a blank name must return 400 and not save a 'new' key."""
    resp = client.post(
        "/command/new",
        data={
            "name": "",
            "description": "Oops blank name",
            "synonyms": "",
            "primitive": "press",
            "kwargs_json": json.dumps({"combo": "ctrl+x"}),
            "kwargs_mode": "advanced",
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "new" not in raw["graphs"], "literal 'new' must not be saved on empty name submission"


def test_new_workflow_empty_name_rejected(client: TestClient, tmp_path: Path) -> None:
    """POST /workflow/new with a blank name must return 400 and not save a 'new' key."""
    resp = client.post(
        "/workflow/new",
        data={
            "name": "",
            "description": "Oops blank name",
            "synonyms": "",
            "args_json": json.dumps([]),
            "steps_json": json.dumps([]),
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    raw = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    assert "new" not in raw["graphs"], "literal 'new' must not be saved on empty name submission"


def test_new_command_reserved_name_rejected(client: TestClient, tmp_path: Path) -> None:
    """POST /command/new with name='new' must return 400 (reserved slug)."""
    resp = client.post(
        "/command/new",
        data={
            "name": "new",
            "description": "Intentionally reserved name",
            "synonyms": "",
            "primitive": "press",
            "kwargs_json": json.dumps({"combo": "ctrl+x"}),
            "kwargs_mode": "advanced",
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "new" not in raw["graphs"], "reserved name 'new' must not be persisted"


def test_new_workflow_reserved_name_rejected(client: TestClient, tmp_path: Path) -> None:
    """POST /workflow/new with name='new' must return 400 (reserved slug)."""
    resp = client.post(
        "/workflow/new",
        data={
            "name": "new",
            "description": "Intentionally reserved name",
            "synonyms": "",
            "args_json": json.dumps([]),
            "steps_json": json.dumps([]),
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    raw = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    assert "new" not in raw["graphs"], "reserved name 'new' must not be persisted"


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


def test_kwargs_form_returns_fragment_for_known_primitive(client: TestClient) -> None:
    """GET /command/kwargs-form renders an HTML fragment (200, not full page)."""
    resp = client.get(
        "/command/kwargs-form",
        params={"primitive": "press"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "<html" not in resp.text


def test_kwargs_form_unknown_primitive_returns_raw_json_fallback(
    client: TestClient,
) -> None:
    """Unknown primitive (no ArgMetadata) falls back to raw JSON field."""
    resp = client.get(
        "/command/kwargs-form",
        params={"primitive": "nonexistent"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert 'name="kwargs_json"' in resp.text


def test_command_save_guided_mode_coerces_types(client: TestClient, tmp_path: Path) -> None:
    """Guided mode: kwarg_* fields are type-coerced via ArgMetadata (real guided path)."""
    resp = client.post(
        "/command/guided_cmd",
        data={
            "description": "Guided test",
            "synonyms": "",
            "primitive": "press",
            "kwargs_mode": "guided",
            "kwarg_combo": "ctrl+a",
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "guided_cmd" in raw["graphs"]
    # Verifies the guided parse branch ran (not JSON fallback)
    assert raw["graphs"]["guided_cmd"]["nodes"][0]["kwargs"] == {"combo": "ctrl+a"}


def test_command_save_guided_mode_required_field_missing_returns_400(
    client: TestClient,
) -> None:
    """Guided mode: missing required kwarg returns HTTP 400."""
    resp = client.post(
        "/command/bad_cmd",
        data={
            "description": "Bad test",
            "synonyms": "",
            "primitive": "press",
            "kwargs_mode": "guided",
            # kwarg_combo intentionally omitted — should fail required check
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    assert "combo" in resp.text


def test_kwargs_form_advanced_mode_returns_raw_json_field(client: TestClient) -> None:
    """GET /command/kwargs-form?mode=advanced returns a kwargs_json textarea."""
    resp = client.get(
        "/command/kwargs-form",
        params={"primitive": "press", "mode": "advanced"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert 'name="kwargs_json"' in resp.text


def test_command_edit_form_renders(client: TestClient) -> None:
    """GET /command/{name}/edit renders the edit form for a known command."""
    resp = client.get("/command/copy/edit", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "copy" in resp.text


def test_command_new_form_renders(client: TestClient) -> None:
    """GET /command/new renders a blank command creation form."""
    resp = client.get("/command/new", headers={"HX-Request": "true"})
    assert resp.status_code == 200


def test_command_save_advanced_mode_parses_json(client: TestClient, tmp_path: Path) -> None:
    """Advanced mode still accepts kwargs_json — backwards compat."""
    resp = client.post(
        "/command/adv_cmd",
        data={
            "description": "Advanced test",
            "synonyms": "",
            "primitive": "press",
            "kwargs_json": json.dumps({"combo": "ctrl+z"}),
            "kwargs_mode": "advanced",
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert raw["graphs"]["adv_cmd"]["nodes"][0]["kwargs"] == {"combo": "ctrl+z"}


# ---------------------------------------------------------------------------
# Workflows CRUD
# ---------------------------------------------------------------------------


def test_workflow_save_with_args(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/workflow/say_bye",
        data={
            "description": "Say goodbye",
            "synonyms": "bye {name}",
            "args_json": json.dumps([{"name": "name", "type": "string", "required": True}]),
            "steps_json": json.dumps([{"ref": "primitive:type", "kwargs": {"text": "Bye {name}"}}]),
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    raw = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    assert "say_bye" in raw["graphs"]
    saved = raw["graphs"]["say_bye"]
    assert saved["inputs"][0]["name"] == "name"
    assert saved["nodes"][0]["kwargs"] == {"text": "Bye {name}"}


def test_workflow_delete(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/workflow/say_hi/delete",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    raw = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    assert "say_hi" not in raw["graphs"]


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
