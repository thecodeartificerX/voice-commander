"""Tests for /page/runs and /page/runs/{run_id} web endpoints."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_commander.event_bus import EventBus
from voice_commander.observability.store import RunRecord, RunUpdate, SpanRecord, Store
from voice_commander.registry import ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app


def _drain(s: Store) -> None:
    s.flush()


def _seed_store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("aaa", 1000.0, "open chrome", 1))
    s.write_span(
        SpanRecord(
            span_id="root",
            run_id="aaa",
            parent_span_id=None,
            type="run",
            name="run",
            started_at=1000.0,
            ended_at=1000.5,
            duration_ms=500,
            status="ok",
        )
    )
    s.write_span(
        SpanRecord(
            span_id="llm1",
            run_id="aaa",
            parent_span_id="root",
            type="llm_call",
            name="llm_call",
            started_at=1000.0,
            ended_at=1000.3,
            duration_ms=300,
            status="ok",
            attrs={"transcript": "open chrome", "model": "test"},
            output={"steps": [{"name": "focus", "kwargs": {"target": "chrome"}}]},
        )
    )
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


def test_page_runs_list_returns_html_rows(
    _app_with_store: tuple[TestClient, Store],
) -> None:
    """htmx swap target must receive HTML <tr> rows, not JSON."""
    client, _ = _app_with_store
    r = client.get("/page/runs/list")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<tr" in r.text
    assert "aaa" in r.text


def test_page_runs_list_status_filter(
    _app_with_store: tuple[TestClient, Store],
) -> None:
    """Filter by status should exclude non-matching runs."""
    client, store = _app_with_store
    # Seed a second run with status=error
    store.write_run_start(RunRecord("bbb", 2000.0, "fail cmd", 1))
    store.write_run_end(RunUpdate("bbb", 2000.5, "error", "boom", 500))
    store.flush()

    r = client.get("/page/runs/list?status=ok")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "aaa" in r.text  # ok run is present
    assert "bbb" not in r.text  # error run is excluded


def test_page_runs_list_empty_store(tmp_path: Path) -> None:
    """Empty store returns 200 with no <tr> rows."""
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    meta_store = ToolMetadataStore(tools_dir)
    registry = ToolRegistry()
    reload_lock = threading.Lock()
    bus = EventBus()
    obs_store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    obs_store.start()

    app = create_app(
        registry,
        meta_store,
        reload_lock,
        event_bus=bus,
        observability_store=obs_store,
    )
    client = TestClient(app)
    try:
        r = client.get("/page/runs/list")
        assert r.status_code == 200
        assert "<tr" not in r.text
    finally:
        obs_store.stop()


def test_page_runs_list_transcript_filter(
    _app_with_store: tuple[TestClient, Store],
) -> None:
    """Transcript search filter wires q -> transcript_like and returns matching rows."""
    client, store = _app_with_store
    # Seed a second run with a different transcript
    store.write_run_start(RunRecord("bbb", 2000.0, "open notepad", 1))
    store.write_run_end(RunUpdate("bbb", 2000.5, "ok", None, 500))
    store.flush()

    r = client.get("/page/runs/list?q=chrome")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "aaa" in r.text  # seeded run has transcript "open chrome"
    assert "bbb" not in r.text  # "open notepad" doesn't match "chrome"
