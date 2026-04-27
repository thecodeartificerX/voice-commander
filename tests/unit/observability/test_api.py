from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from voice_commander.observability.api import build_observability_router
from voice_commander.observability.store import (
    RunRecord,
    RunUpdate,
    SpanRecord,
    Store,
)


def _drain(s):
    while not s._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)


@pytest.fixture
def store_with_runs(tmp_path: Path):
    s = Store(tmp_path / "runs.db", keep_runs=100, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("aaa", 1000.0, "open chrome", 1))
    s.write_span(SpanRecord(
        span_id="root", run_id="aaa", parent_span_id=None,
        type="run", name="run", started_at=1000.0, ended_at=1000.5,
        duration_ms=500, status="ok",
    ))
    s.write_run_end(RunUpdate("aaa", 1000.5, "ok", None, 500))
    s.write_run_start(RunRecord("bbb", 1100.0, "do the thing", 1))
    s.write_run_end(RunUpdate("bbb", 1100.7, "error", "FocusWindowError", 700))
    _drain(s)
    yield s
    s.stop()


def _app(store, tracer=None, bus=None):
    app = FastAPI()
    app.include_router(build_observability_router(store, tracer=tracer, bus=bus))
    return app


def test_list_runs(store_with_runs):
    client = TestClient(_app(store_with_runs))
    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2


def test_list_runs_filter_status(store_with_runs):
    client = TestClient(_app(store_with_runs))
    r = client.get("/api/runs?status=error")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["runs"][0]["run_id"] == "bbb"


def test_get_last_run_includes_spans(store_with_runs):
    client = TestClient(_app(store_with_runs))
    r = client.get("/api/runs/last")
    assert r.status_code == 200
    body = r.json()
    assert "spans" in body
    assert body["run_id"] == "bbb"


def test_get_run_404(store_with_runs):
    client = TestClient(_app(store_with_runs))
    r = client.get("/api/runs/nonexistent")
    assert r.status_code == 404


def test_get_run_llm_returns_404_when_no_llm_span(store_with_runs):
    client = TestClient(_app(store_with_runs))
    r = client.get("/api/runs/aaa/llm")
    assert r.status_code == 404


def test_runs_stream_503_when_no_bus(store_with_runs):
    """Without a bus, /api/runs/stream must return 503."""
    client = TestClient(_app(store_with_runs, bus=None))
    r = client.get("/api/runs/stream")
    assert r.status_code == 503


def test_runs_stream_returns_200(store_with_runs):
    """With a bus wired, /api/runs/stream returns 200 + text/event-stream.

    Uses an asyncio-based direct ASGI call to read just the response start
    without blocking on the infinite SSE body.
    """
    import asyncio

    from voice_commander.event_bus import EventBus

    bus = EventBus()
    application = _app(store_with_runs, bus=bus)

    received: dict = {}

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/runs/stream",
            "query_string": b"",
            "headers": [],
            "asgi": {"version": "3.0"},
        }

        async def receive():
            # Signal disconnect immediately so the generator exits
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                received["status"] = message["status"]
                received["headers"] = {
                    k.decode(): v.decode()
                    for k, v in message.get("headers", [])
                }
            # Don't block on body chunks

        await application(scope, receive, send)

    asyncio.run(_run())
    assert received.get("status") == 200
    ct = received.get("headers", {}).get("content-type", "")
    assert "text/event-stream" in ct


def test_replay_llm_503_when_no_router(store_with_runs):
    """Without llm_router, /api/runs/{id}/replay-llm returns 503."""
    client = TestClient(_app(store_with_runs))
    r = client.post("/api/runs/aaa/replay-llm", json={})
    assert r.status_code == 503


def test_replay_full_requires_confirm_header(store_with_runs):
    """Without X-Replay-Confirm header, /api/runs/{id}/replay-full returns 412."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    from voice_commander.observability.api import build_observability_router

    router_obj = build_observability_router(
        store_with_runs,
        tracer=MagicMock(),
        llm_router=MagicMock(),
        dispatcher=MagicMock(),
        registry=MagicMock(),
    )
    app = FastAPI()
    app.include_router(router_obj)
    client = TestClient(app)

    # Without confirmation header → 412
    r = client.post("/api/runs/aaa/replay-full")
    assert r.status_code == 412
