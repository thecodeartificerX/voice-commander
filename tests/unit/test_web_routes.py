import shutil
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tools_sidecar"


@pytest.fixture
def app_env(tmp_path):
    """Set up registry + store + app for testing."""
    shutil.copy(FIXTURES / "sample.toml", tmp_path / "sample.toml")

    store = ToolMetadataStore(tmp_path)
    registry = ToolRegistry()
    for name in ("alpha", "beta"):
        registry.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=lambda: None,
                module="test",
                docstring=None,
            )
        )
    registry.bind_metadata(store)

    reload_lock = threading.Lock()
    app = create_app(registry, store, reload_lock)
    client = TestClient(app)
    return client, registry, store


def test_index_returns_200(app_env):
    client, _, _ = app_env
    # GET / redirects to /page/commands; primitives live at /page/primitives.
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/page/commands"

    resp = client.get("/page/primitives")
    assert resp.status_code == 200
    assert "alpha" in resp.text
    assert "beta" in resp.text


def test_healthz(app_env):
    client, _, _ = app_env
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_edit_returns_form_fragment(app_env):
    client, _, _ = app_env
    resp = client.get("/tool/alpha/edit")
    assert resp.status_code == 200
    assert "<form" in resp.text
    assert "alpha" in resp.text
    # Should be a fragment, not full page
    assert "<!DOCTYPE" not in resp.text


def test_cancel_returns_card(app_env):
    client, _, _ = app_env
    resp = client.get("/tool/alpha/cancel")
    assert resp.status_code == 200
    assert "alpha" in resp.text
    assert "<form" not in resp.text


def test_csrf_rejects_post_without_hx_header(app_env):
    client, _, _ = app_env
    resp = client.post("/tool/alpha/toggle")
    assert resp.status_code == 403


HX = {"HX-Request": "true"}


def test_save_valid(app_env):
    client, registry, _ = app_env
    resp = client.post(
        "/tool/alpha",
        data={
            "phrases": "new phrase one\nnew phrase two",
            "description": "Updated.",
            "category": "test",
        },
        headers=HX,
    )
    assert resp.status_code == 200
    # save() persists description; phrases are read-only from TOML and not
    # rewritten by save(), so we verify the description was updated.
    entry = registry.by_name("alpha")
    assert entry.description == "Updated."


def test_save_empty_phrases_returns_400(app_env):
    client, _, _ = app_env
    resp = client.post(
        "/tool/alpha",
        data={
            "phrases": "",
            "description": "x",
            "category": "test",
        },
        headers=HX,
    )
    assert resp.status_code == 400
    assert "at least one phrase" in resp.text.lower()


def test_save_duplicate_phrase_returns_400(app_env):
    client, _, _ = app_env
    # "beta phrase" belongs to beta — try to set it on alpha
    resp = client.post(
        "/tool/alpha",
        data={
            "phrases": "beta phrase",
            "description": "x",
            "category": "test",
        },
        headers=HX,
    )
    assert resp.status_code == 400
    assert "already used" in resp.text.lower()


def test_toggle_flips_enabled(app_env):
    client, registry, _ = app_env
    # alpha starts enabled=True
    assert registry.by_name("alpha").enabled is True

    resp = client.post("/tool/alpha/toggle", headers=HX)
    assert resp.status_code == 200
    assert registry.by_name("alpha").enabled is False

    # Toggle back
    resp = client.post("/tool/alpha/toggle", headers=HX)
    assert resp.status_code == 200
    assert registry.by_name("alpha").enabled is True


def test_edit_nonexistent_tool(app_env):
    client, _, _ = app_env
    resp = client.get("/tool/nonexistent/edit")
    assert resp.status_code == 404
