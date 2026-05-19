"""Integration tests for /page/dictation and /dictation/vocab (ADR 0092).

The re-transcribe route is gone (no last.wav in the streaming pipeline);
these tests cover the last-transcript view and the vocab editor save.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app

HX = {"HX-Request": "true"}


def _make_client(tmp_path: Path) -> TestClient:
    (tmp_path / "tools_meta").mkdir()
    store = ToolMetadataStore(tmp_path / "tools_meta")
    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="focus",
            phrases=(),
            func=lambda **_: None,
            module="test",
            docstring=None,
        )
    )
    app = create_app(registry, store, threading.Lock())
    return TestClient(app)


@pytest.fixture()
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with cwd = tmp_path; routes use Path('outputs/dictation')."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        '[hotkey]\nkey = "scroll_lock"\n', encoding="utf-8"
    )
    dictation_dir = tmp_path / "outputs" / "dictation"
    dictation_dir.mkdir(parents=True)
    (dictation_dir / "last.txt").write_text("prior dictation", encoding="utf-8")
    return _make_client(tmp_path)


def test_page_dictation_renders_last_text(app_client: TestClient) -> None:
    """GET /page/dictation shows the text from outputs/dictation/last.txt."""
    resp = app_client.get("/page/dictation")
    assert resp.status_code == 200
    assert "prior dictation" in resp.text


def test_page_dictation_no_retranscribe_button(app_client: TestClient) -> None:
    """The re-transcribe button + route are gone (ADR 0092)."""
    resp = app_client.get("/page/dictation")
    assert resp.status_code == 200
    assert "/dictation/retranscribe" not in resp.text
    assert "Re-transcribe" not in resp.text


def test_retranscribe_route_is_removed(app_client: TestClient) -> None:
    """POST /dictation/retranscribe returns 404 — the route no longer exists."""
    resp = app_client.post("/dictation/retranscribe", headers=HX)
    assert resp.status_code == 404


def test_vocab_save_persists_vocab_json(app_client: TestClient) -> None:
    """POST /dictation/vocab writes outputs/dictation/vocab.json."""
    resp = app_client.post(
        "/dictation/vocab",
        headers=HX,
        data={
            "vocab": "Supabase\nPostgres",
            "corrections": json.dumps([{"wrong": "supa base", "right": "Supabase"}]),
            "commands": json.dumps([{"phrase": "new line", "action": "newline"}]),
        },
    )
    assert resp.status_code == 200
    saved = json.loads(
        (
            Path("outputs") / "dictation" / "vocab.json"
        ).read_text(encoding="utf-8")
    )
    assert saved["vocab"] == ["Supabase", "Postgres"]
    assert saved["corrections"] == [{"wrong": "supa base", "right": "Supabase"}]
    assert saved["commands"] == [{"phrase": "new line", "action": "newline"}]
