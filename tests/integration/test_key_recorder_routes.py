"""Integration tests for the ``/key_recorder/*`` HTTP routes.

The routes are wired through ``create_app`` with a fake KeyRecorder so
we can assert HTTP semantics (CSRF middleware, status codes, JSON
shape) without spawning a real pynput listener.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from voice_commander.event_bus import EventBus
from voice_commander.registry import ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app


class FakeKeyRecorder:
    def __init__(self) -> None:
        self.start_calls: list[float] = []
        self.cancel_calls = 0
        self._busy = False
        self._raise: Exception | None = None

    def start(self, timeout: float = 15.0) -> bool:
        self.start_calls.append(timeout)
        if self._raise is not None:
            raise self._raise
        if self._busy:
            return False
        self._busy = True
        return True

    def cancel(self) -> None:
        self.cancel_calls += 1
        self._busy = False

    def is_recording(self) -> bool:
        return self._busy


@pytest.fixture
def client_factory(tmp_path: Path):
    def _make(recorder: Any | None) -> TestClient:
        store = ToolMetadataStore(tmp_path)
        registry = ToolRegistry()
        registry.bind_metadata(store)
        app = create_app(
            registry,
            store,
            threading.Lock(),
            event_bus=EventBus(),
            key_recorder=recorder,
        )
        return TestClient(app)

    return _make


def test_start_returns_202_first_time(client_factory) -> None:
    rec = FakeKeyRecorder()
    c = client_factory(rec)
    resp = c.post("/key_recorder/start", json={})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "started"
    assert body["timeout"] == 15.0
    assert rec.start_calls == [15.0]


def test_start_clamps_timeout(client_factory) -> None:
    rec = FakeKeyRecorder()
    c = client_factory(rec)
    resp = c.post("/key_recorder/start", json={"timeout": 999})
    assert resp.status_code == 202
    assert resp.json()["timeout"] == 60.0
    assert rec.start_calls == [60.0]

    rec2 = FakeKeyRecorder()
    c2 = client_factory(rec2)
    resp2 = c2.post("/key_recorder/start", json={"timeout": 0})
    assert resp2.status_code == 202
    assert resp2.json()["timeout"] == 1.0


def test_start_409_when_busy(client_factory) -> None:
    rec = FakeKeyRecorder()
    c = client_factory(rec)
    c.post("/key_recorder/start", json={})
    resp = c.post("/key_recorder/start", json={})
    assert resp.status_code == 409


def test_start_503_when_recorder_missing(client_factory) -> None:
    c = client_factory(None)
    resp = c.post("/key_recorder/start", json={})
    assert resp.status_code == 503


def test_cancel_204_when_running(client_factory) -> None:
    rec = FakeKeyRecorder()
    c = client_factory(rec)
    c.post("/key_recorder/start", json={})
    resp = c.post("/key_recorder/cancel", json={})
    assert resp.status_code == 204
    assert rec.cancel_calls == 1


def test_cancel_204_when_idle(client_factory) -> None:
    rec = FakeKeyRecorder()
    c = client_factory(rec)
    resp = c.post("/key_recorder/cancel", json={})
    assert resp.status_code == 204
    assert rec.cancel_calls == 1


def test_cancel_503_when_recorder_missing(client_factory) -> None:
    c = client_factory(None)
    resp = c.post("/key_recorder/cancel", json={})
    assert resp.status_code == 503


def test_csrf_blocks_non_json_post(client_factory) -> None:
    rec = FakeKeyRecorder()
    c = client_factory(rec)
    # No Content-Type / no HX-Request — CSRF middleware must reject.
    resp = c.post(
        "/key_recorder/start",
        content="",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 403


def test_start_500_on_recorder_exception(client_factory) -> None:
    rec = FakeKeyRecorder()
    rec._raise = RuntimeError("hook install failed")
    c = client_factory(rec)
    resp = c.post("/key_recorder/start", json={})
    assert resp.status_code == 500
