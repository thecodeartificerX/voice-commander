"""Tests for /page/runs and /page/runs/{run_id} web endpoints."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_commander.event_bus import EventBus
from voice_commander.observability.store import RunRecord, RunUpdate, SpanRecord, Store
from voice_commander.registry import ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app


def _drain(s: Store) -> None:
    while not s._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)


def _seed_store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("aaa", 1000.0, "open chrome", 1))
    s.write_span(SpanRecord(
        span_id="root", run_id="aaa", parent_span_id=None,
        type="run", name="run", started_at=1000.0, ended_at=1000.5,
        duration_ms=500, status="ok",
    ))
    s.write_span(SpanRecord(
        span_id="llm1", run_id="aaa", parent_span_id="root",
        type="llm_call", name="llm_call", started_at=1000.0, ended_at=1000.3,
        duration_ms=300, status="ok",
        attrs={"transcript": "open chrome", "model": "test"},
        output={"steps": [{"name": "focus", "kwargs": {"target": "chrome"}}]},
    ))
    s.write_run_end(RunUpdate("aaa", 1000.5, "ok", None, 500))
    _drain(s)
    return s


@pytest.fixture()
def _app_with_store(tmp_path: Path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    store = ToolMetadataStore(tools_dir)
    registry = ToolRegistry()
    reload_lock = threading.Lock()
    bus = EventBus()
    obs_store = _seed_store(tmp_path)

    app = create_app(
        registry,
        store,
        reload_lock,
        event_bus=bus,
        observability_store=obs_store,
    )
    client = TestClient(app)
    yield client, obs_store
    obs_store.stop()


def test_page_runs_renders(
    _app_with_store: tuple[TestClient, Store],
) -> None:
    client, _ = _app_with_store
    r = client.get("/page/runs")
    assert r.status_code == 200
    assert "aaa" in r.text
    assert "open chrome" in r.text


def test_page_runs_detail_renders(
    _app_with_store: tuple[TestClient, Store],
) -> None:
    client, _ = _app_with_store
    r = client.get("/page/runs/aaa")
    assert r.status_code == 200
    # Detail partial contains the run_id
    assert "aaa" in r.text


def test_page_runs_detail_404_for_unknown(
    _app_with_store: tuple[TestClient, Store],
) -> None:
    client, _ = _app_with_store
    r = client.get("/page/runs/does-not-exist")
    assert r.status_code == 404
