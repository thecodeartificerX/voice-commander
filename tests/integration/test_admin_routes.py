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
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from voice_commander.commands.graph import Graph, GraphInput, Node
from voice_commander.commands.store import GraphStore
from voice_commander.event_bus import EventBus
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.streaming_recorder import SelfTestResult
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


_OK_DEVICE_RESULT = SelfTestResult(
    ok=True, device_index=3, host_api="Windows WASAPI", native_rate=48000, error=None
)


@pytest.fixture()
def ok_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch validate_device to always return ok=True for tests that POST a device name."""
    monkeypatch.setattr(
        "voice_commander.web.admin.validate_device",
        lambda saved_index, device_name, channels=1: _OK_DEVICE_RESULT,
    )


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


def test_command_duplicate_creates_copy(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/command/copy/duplicate", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "copy_copy" in resp.text  # rendered card has new name
    raw = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))
    assert "copy" in raw["graphs"]
    assert "copy_copy" in raw["graphs"]


def test_command_duplicate_missing_returns_404(client: TestClient) -> None:
    resp = client.post("/command/nope/duplicate", headers={"HX-Request": "true"})
    assert resp.status_code == 404


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


def test_workflow_duplicate_creates_copy(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/workflow/say_hi/duplicate", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "say_hi_copy" in resp.text
    raw = json.loads((tmp_path / "workflows.json").read_text(encoding="utf-8"))
    assert "say_hi" in raw["graphs"]
    assert "say_hi_copy" in raw["graphs"]


def test_workflow_duplicate_missing_returns_404(client: TestClient) -> None:
    resp = client.post("/workflow/nope/duplicate", headers={"HX-Request": "true"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Config save
# ---------------------------------------------------------------------------


def test_config_save_rewrites_toml(client: TestClient, tmp_path: Path, ok_device: None) -> None:
    resp = client.post(
        "/config",
        data={
            "audio_device_name": "Test Mic",
            "transcription_model_size": "base.en",
            "transcription_min_confidence": 0.45,
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'device_name = "Test Mic"' in text
    assert 'model_size = "base.en"' in text
    # Legacy int key must NOT be written (ADR 0081)
    device_int_lines = [l for l in text.splitlines() if l.strip().startswith("device") and "device_name" not in l]
    assert device_int_lines == [], f"Unexpected legacy device int lines: {device_int_lines}"


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


def test_config_save_restart_required_banner(client: TestClient, ok_device: None) -> None:
    # Changing audio_device_name differs from the seed default ("") → restart required.
    resp = client.post(
        "/config",
        headers={"HX-Request": "true"},
        data={
            "audio_device_name": "New Microphone",
            "transcription_model_size": "small.en",
            "transcription_min_confidence": 0.3,
        },
    )
    assert resp.status_code == 200
    assert "require a restart" in resp.text


def test_config_save_hot_reload_banner(client: TestClient) -> None:
    # No device_name change + transcription fields match defaults → hot reload.
    resp = client.post(
        "/config",
        headers={"HX-Request": "true"},
        data={
            "transcription_model_size": "small.en",
            "transcription_min_confidence": 0.4,
        },
    )
    assert resp.status_code == 200
    assert "applied immediately" in resp.text


# ---------------------------------------------------------------------------
# GET /audio/devices
# ---------------------------------------------------------------------------


def test_audio_devices_route_returns_wasapi_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route filters to WASAPI inputs; response has expected JSON shape."""
    import voice_commander.web.admin as admin_mod

    wasapi_hostapis = [
        {"name": "Windows MME"},
        {"name": "Windows WASAPI"},
    ]
    all_devices = [
        # WASAPI input — should appear
        {
            "name": "AT2020 USB",
            "hostapi": 1,
            "max_input_channels": 1,
            "default_samplerate": 48000.0,
        },
        # MME input — should be excluded
        {
            "name": "AT2020 USB",
            "hostapi": 0,
            "max_input_channels": 1,
            "default_samplerate": 48000.0,
        },
        # WASAPI output — should be excluded (no input channels)
        {
            "name": "Speakers",
            "hostapi": 1,
            "max_input_channels": 0,
            "default_samplerate": 48000.0,
        },
    ]

    monkeypatch.setattr(admin_mod, "_find_wasapi_hostapi_index", lambda: 1)

    import sounddevice as sd

    monkeypatch.setattr(sd, "query_devices", lambda: all_devices)
    monkeypatch.setattr(sd, "query_hostapis", lambda: wasapi_hostapis)

    resp = client.get("/audio/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    entry = body[0]
    assert entry["name"] == "AT2020 USB"
    assert entry["hostapi"] == "Windows WASAPI"
    assert entry["rate"] == 48000
    assert entry["channels"] == 1
    assert "index" in entry


def test_audio_devices_route_empty_when_no_wasapi(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returns [] with a Warning header when WASAPI is unavailable."""
    import voice_commander.web.admin as admin_mod

    monkeypatch.setattr(admin_mod, "_find_wasapi_hostapi_index", lambda: None)

    resp = client.get("/audio/devices")
    assert resp.status_code == 200
    assert resp.json() == []
    assert "Warning" in resp.headers


# ---------------------------------------------------------------------------
# config_save: device validation
# ---------------------------------------------------------------------------


def test_config_save_rejects_invalid_device_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """POSTing a bad device name returns 400 and config is NOT updated."""
    import voice_commander.web.admin as admin_mod

    fail_result = SelfTestResult(
        ok=False,
        device_index=None,
        host_api="(default)",
        native_rate=48000,
        error="PaErrorCode -9999: device unavailable",
    )
    monkeypatch.setattr(
        admin_mod,
        "validate_device",
        lambda saved_index, device_name, channels=1: fail_result,
    )

    config_before = (tmp_path / "config.toml").read_text(encoding="utf-8")
    resp = client.post(
        "/config",
        headers={"HX-Request": "true"},
        data={
            "audio_device_name": "Ghost Mic",
            "transcription_model_size": "small.en",
            "transcription_min_confidence": 0.3,
        },
    )
    assert resp.status_code == 400
    assert "Ghost Mic" in resp.text
    assert "device unavailable" in resp.text or "PaErrorCode" in resp.text
    # Config file must be unchanged.
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == config_before


def test_config_save_accepts_empty_device_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty device_name skips validation entirely and saves successfully."""
    calls: list[str] = []

    import voice_commander.web.admin as admin_mod

    monkeypatch.setattr(
        admin_mod,
        "validate_device",
        lambda *a, **kw: calls.append("called") or _OK_DEVICE_RESULT,
    )

    resp = client.post(
        "/config",
        headers={"HX-Request": "true"},
        data={
            "audio_device_name": "",
            "transcription_model_size": "small.en",
            "transcription_min_confidence": 0.3,
        },
    )
    assert resp.status_code == 200
    assert calls == [], "validate_device should NOT be called for empty device name"


def test_config_save_validates_before_persist(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """validate_device is called and update_user_config is called on success."""
    validate_calls: list[str] = []
    persist_calls: list[str] = []

    import voice_commander.web.admin as admin_mod

    def _fake_validate(saved_index, device_name, channels=1):
        validate_calls.append(device_name)
        return _OK_DEVICE_RESULT

    monkeypatch.setattr(admin_mod, "validate_device", _fake_validate)

    import voice_commander.config as config_mod

    original_update = config_mod.update_user_config

    def _fake_update(path, updates):
        persist_calls.append(str(path))
        return original_update(path, updates)

    monkeypatch.setattr(admin_mod, "update_user_config", _fake_update)

    resp = client.post(
        "/config",
        headers={"HX-Request": "true"},
        data={
            "audio_device_name": "Studio Mic",
            "transcription_model_size": "small.en",
            "transcription_min_confidence": 0.3,
        },
    )
    assert resp.status_code == 200
    assert validate_calls == ["Studio Mic"]
    assert len(persist_calls) == 1
