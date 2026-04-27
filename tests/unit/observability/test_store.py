from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from voice_commander.observability.store import (
    RunRecord,
    RunUpdate,
    SpanRecord,
    Store,
)


def test_store_creates_schema(tmp_path: Path):
    db = tmp_path / "runs.db"
    store = Store(db, keep_runs=10, queue_max=64, daemon_pid=123)
    store.start()
    try:
        with sqlite3.connect(db) as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            names = [r[0] for r in cur.fetchall()]
        assert "runs" in names
        assert "spans" in names
    finally:
        store.stop()


def test_store_uses_wal_mode(tmp_path: Path):
    db = tmp_path / "runs.db"
    store = Store(db, keep_runs=10, queue_max=64, daemon_pid=123)
    store.start()
    try:
        with sqlite3.connect(db) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        store.stop()


def test_store_recreates_on_corrupt_db(tmp_path: Path):
    db = tmp_path / "runs.db"
    db.write_bytes(b"not a sqlite file at all")
    store = Store(db, keep_runs=10, queue_max=64, daemon_pid=123)
    store.start()
    try:
        # Should NOT raise — should rename corrupt file and recreate.
        backups = list(tmp_path.glob("runs.db.corrupt-*"))
        assert len(backups) == 1
        assert db.exists()
        with sqlite3.connect(db) as conn:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            names = [r[0] for r in cur.fetchall()]
        assert "runs" in names
    finally:
        store.stop()


def _drain(store: Store, timeout: float = 1.0) -> None:
    """Block until the writer queue is empty (test helper)."""
    deadline = time.monotonic() + timeout
    while not store._q.empty():
        if time.monotonic() > deadline:
            raise AssertionError("writer queue did not drain in time")
        time.sleep(0.01)
    # One more pass to let the writer commit the last row.
    time.sleep(0.05)


def test_store_round_trip_run_and_span(tmp_path):
    store = Store(tmp_path / "runs.db", keep_runs=100, queue_max=64, daemon_pid=42)
    store.start()
    try:
        store.write_run_start(RunRecord("r1", 1000.0, "open chrome", 42))
        store.write_span(
            SpanRecord(
                span_id="s1", run_id="r1", parent_span_id=None,
                type="run", name="run", started_at=1000.0, ended_at=1000.5,
                duration_ms=500, status="ok", attrs={"transcript": "open chrome"},
            )
        )
        store.write_run_end(RunUpdate("r1", 1000.5, "ok", None, 500))
        _drain(store)

        run = store.get_run("r1")
        assert run["status"] == "ok"
        assert run["duration_ms"] == 500
        spans = store.get_spans("r1")
        assert len(spans) == 1
        assert spans[0]["type"] == "run"
    finally:
        store.stop()


def test_store_prune_keeps_only_n_most_recent(tmp_path):
    store = Store(tmp_path / "runs.db", keep_runs=3, queue_max=256, daemon_pid=42)
    store.start()
    try:
        for i in range(60):
            rid = f"r{i:02d}"
            store.write_run_start(RunRecord(rid, 1000.0 + i, f"t{i}", 42))
            store.write_run_end(RunUpdate(rid, 1000.5 + i, "ok", None, 1))
        _drain(store, timeout=3.0)
        runs = store.list_runs(limit=100)
        assert len(runs) == 3
        assert {r["run_id"] for r in runs} == {"r57", "r58", "r59"}
    finally:
        store.stop()


def test_store_recover_stale_runs_on_startup(tmp_path):
    db = tmp_path / "runs.db"
    store_a = Store(db, keep_runs=10, queue_max=64, daemon_pid=11)
    store_a.start()
    try:
        store_a.write_run_start(RunRecord("zombie", 1000.0, "did not finish", 11))
        _drain(store_a)
    finally:
        store_a.stop()

    # Simulate new daemon pid; recover_stale_runs should mark prior 'running' rows as error.
    store_b = Store(db, keep_runs=10, queue_max=64, daemon_pid=22)
    store_b.start()
    try:
        store_b.recover_stale_runs()
        run = store_b.get_run("zombie")
        assert run["status"] == "error"
        assert run["error_msg"] == "daemon crash"
    finally:
        store_b.stop()
