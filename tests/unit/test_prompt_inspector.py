"""Tests for the prompt inspector web endpoints."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from voice_commander.config import LLMConfig
from voice_commander.event_bus import EventBus
from voice_commander.llm_router import LLMRouter
from voice_commander.registry import ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app


@pytest.fixture()
def _prompt_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, EventBus, Path]:
    """Create a minimal app with prompt routes wired up."""
    # Use tmp_path for template file so tests don't touch the real one
    template_file = tmp_path / "prompt_template.txt"
    template_file.write_text(
        "Test prompt for {default_browser}.\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "voice_commander.llm_router._TEMPLATE_PATH", template_file
    )
    monkeypatch.setattr(
        "voice_commander.web.prompt._TEMPLATE_PATH", template_file
    )

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    store = ToolMetadataStore(tools_dir)
    registry = ToolRegistry()
    reload_lock = threading.Lock()
    event_bus = EventBus()
    cfg = LLMConfig(default_browser="comet", warmup_on_startup=False)
    llm_router = LLMRouter(cfg, registry, reload_lock)

    app = create_app(
        registry,
        store,
        reload_lock,
        event_bus=event_bus,
        llm_router=llm_router,
    )
    client = TestClient(app)
    return client, event_bus, template_file


def test_get_prompt_returns_html(_prompt_env: tuple[TestClient, EventBus, Path]) -> None:
    client, _, _ = _prompt_env
    resp = client.get("/prompt")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "template_text" in resp.text  # textarea name attribute


def test_post_prompt_template_saves_and_reloads(
    _prompt_env: tuple[TestClient, EventBus, Path],
) -> None:
    client, _, template_file = _prompt_env
    new_template = "Updated prompt for {default_browser}."
    resp = client.post(
        "/prompt/template",
        data={"template_text": new_template},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Saved" in resp.text
    # Verify file was written
    assert template_file.read_text(encoding="utf-8") == new_template


def test_post_prompt_template_rejects_missing_placeholder(
    _prompt_env: tuple[TestClient, EventBus, Path],
) -> None:
    client, _, _ = _prompt_env
    resp = client.post(
        "/prompt/template",
        data={"template_text": "No placeholder here."},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    assert "default_browser" in resp.text


def test_post_prompt_template_rejects_empty(
    _prompt_env: tuple[TestClient, EventBus, Path],
) -> None:
    client, _, _ = _prompt_env
    resp = client.post(
        "/prompt/template",
        data={"template_text": "   "},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 400
    assert "empty" in resp.text.lower()


def test_get_prompt_tools_shows_catalog(
    _prompt_env: tuple[TestClient, EventBus, Path],
) -> None:
    client, _, _ = _prompt_env
    resp = client.get("/prompt/tools")
    assert resp.status_code == 200
    assert "Tools Catalog" in resp.text


def test_prompt_template_saved_event_published(
    _prompt_env: tuple[TestClient, EventBus, Path],
) -> None:
    client, event_bus, _ = _prompt_env
    q = event_bus.subscribe()
    resp = client.post(
        "/prompt/template",
        data={"template_text": "Event test {default_browser}."},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    # Check event was published
    event = q.get(timeout=2)
    assert event.type == "prompt_template_saved"


def test_post_prompt_template_oserror_escapes_html(
    _prompt_env: tuple[TestClient, EventBus, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSError messages in save failure response are HTML-escaped."""
    client, _, _ = _prompt_env

    # Point _TEMPLATE_PATH to a non-existent directory so write fails with OSError
    bogus = Path("Z:/no/such/dir/prompt_template.txt")
    monkeypatch.setattr("voice_commander.web.prompt._TEMPLATE_PATH", bogus)

    resp = client.post(
        "/prompt/template",
        data={"template_text": "Valid template with {default_browser}."},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 500
    assert "Save failed" in resp.text
