"""End-to-end: real uvicorn + real registry + real tool modules."""

import shutil
import threading
import time
from pathlib import Path

import httpx
import pytest

from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app
from voice_commander.web.server import WebServer

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tools_sidecar"


@pytest.fixture
def live_server(tmp_path):
    """Spin up a real uvicorn server on a random port."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

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
    server = WebServer(app, host="127.0.0.1", port=port)
    server.start()
    time.sleep(0.5)  # let uvicorn start

    yield {
        "url": f"http://127.0.0.1:{port}",
        "registry": registry,
        "store": store,
        "tmp_path": tmp_path,
    }

    server.stop(timeout=5.0)


@pytest.mark.integration
def test_dashboard_loads(live_server):
    resp = httpx.get(f"{live_server['url']}/", timeout=5.0)
    assert resp.status_code == 200
    assert "alpha" in resp.text


@pytest.mark.integration
def test_edit_save_updates_registry(live_server):
    url = live_server["url"]
    registry = live_server["registry"]

    # Save new phrases
    resp = httpx.post(
        f"{url}/tool/alpha",
        data={
            "phrases": "brand new phrase",
            "description": "Updated via test.",
            "category": "test",
        },
        headers={"HX-Request": "true"},
        timeout=5.0,
    )
    assert resp.status_code == 200

    # Registry should reflect change — save() persists description but not
    # phrases (phrases are read-only from TOML); verify the description updated.
    entry = registry.by_name("alpha")
    assert entry.description == "Updated via test."


@pytest.mark.integration
def test_toggle_disables_tool(live_server):
    url = live_server["url"]
    registry = live_server["registry"]

    assert registry.by_name("alpha").enabled is True
    resp = httpx.post(f"{url}/tool/alpha/toggle", headers={"HX-Request": "true"}, timeout=5.0)
    assert resp.status_code == 200
    assert registry.by_name("alpha").enabled is False
