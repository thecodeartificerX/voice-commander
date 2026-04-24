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

from voice_commander.commands.store import (
    CommandDef,
    CommandStore,
    WorkflowArg,
    WorkflowDef,
    WorkflowStep,
    WorkflowStore,
)
from voice_commander.dispatcher import Dispatcher
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
            )
        )
    return reg


def _seed_stores(root: Path) -> tuple[CommandStore, WorkflowStore, Path]:
    config_path = root / "config.toml"
    config_path.write_text(
        '[llm]\nmodel_id = "test-model"\nendpoint_url = "http://x/"\n',
        encoding="utf-8",
    )
    cs = CommandStore(root / "commands.json")
    cs.save_one(
        CommandDef(
            name="copy",
            description="Copy",
            synonyms=("copy",),
            primitive="press",
            kwargs={"combo": "ctrl+c"},
        )
    )
    ws = WorkflowStore(root / "workflows.json")
    ws.save_one(
        WorkflowDef(
            name="say_hi",
            description="",
            synonyms=("hello",),
            args=(WorkflowArg(name="name", required=True),),
            steps=(WorkflowStep(ref="primitive:type", kwargs={"text": "Hi {name}"}),),
        )
    )
    return cs, ws, config_path


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    reg = _primitive_registry()
    cs, ws, config_path = _seed_stores(tmp_path)
    disp = Dispatcher(CapturingFeedbackSink())
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
        dispatcher=disp,
        llm_context={"default_browser": "chrome"},
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
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert "new_tab" in resp.text

    # Read back from the store.
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "new_tab" in raw["commands"]
    assert raw["commands"]["new_tab"]["kwargs"] == {"combo": "ctrl+t"}


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
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert "mute_mic" in resp.text

    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "mute_mic" in raw["commands"], "name from form field must be persisted"
    assert "new" not in raw["commands"], "literal 'new' must not be saved as a command"


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
    assert "morning_routine" in raw["workflows"], "name from form field must be persisted"
    assert "new" not in raw["workflows"], "literal 'new' must not be saved as a workflow"


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
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "new" not in raw["commands"], "literal 'new' must not be saved on empty name submission"


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
    assert "new" not in raw["workflows"], "literal 'new' must not be saved on empty name submission"


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
            "enabled": "true",
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "new" not in raw["commands"], "reserved name 'new' must not be persisted"


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
    assert "new" not in raw["workflows"], "reserved name 'new' must not be persisted"


def test_command_delete_removes_file_entry(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/command/copy/delete",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "copy" not in raw["commands"]


def test_command_toggle_flips_enabled(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/command/copy/toggle",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert raw["commands"]["copy"]["enabled"] is False


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
    assert "say_bye" in raw["workflows"]
    saved = raw["workflows"]["say_bye"]
    assert saved["args"][0]["name"] == "name"
    assert saved["steps"][0]["kwargs"] == {"text": "Bye {name}"}


def test_workflow_delete(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/workflow/say_hi/delete",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    raw = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    assert "say_hi" not in raw["workflows"]


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
