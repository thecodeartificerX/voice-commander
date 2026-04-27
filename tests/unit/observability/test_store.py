from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

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


def _drain(store: Store, timeout: float = 2.0) -> None:
    """Block until all queued writes have been committed to SQLite."""
    flushed = store.flush(timeout=timeout)
    if not flushed:
        raise AssertionError(f"writer did not flush within {timeout}s")


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


def test_set_keep_runs_validates_minimum(tmp_path):
    """M8: set_keep_runs rejects n < 1."""
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    try:
        s.set_keep_runs(5)
        assert s._keep_runs == 5
        with pytest.raises(ValueError):
            s.set_keep_runs(0)
        with pytest.raises(ValueError):
            s.set_keep_runs(-1)
    finally:
        s.stop()


def test_list_runs_graph_filter_sql(tmp_path):
    """M9: graph_name filter uses SQL EXISTS subquery."""
    s = Store(tmp_path / "runs.db", keep_runs=100, queue_max=64, daemon_pid=1)
    s.start()
    try:
        # Insert two runs
        s.write_run_start(RunRecord("aaa", 1000.0, "run aaa", 1))
        s.write_run_end(RunUpdate("aaa", 1000.5, "ok", None, 500))
        s.write_run_start(RunRecord("bbb", 1001.0, "run bbb", 1))
        s.write_run_end(RunUpdate("bbb", 1001.5, "ok", None, 500))
        # Insert a graph span only for run "aaa"
        s.write_span(SpanRecord(
            span_id="sp1", run_id="aaa", parent_span_id=None,
            type="graph", name="greet",
            started_at=1000.0, ended_at=1001.0, duration_ms=1000, status="ok",
        ))
        _drain(s)
        matched = s.list_runs(graph_name="greet")
        assert len(matched) == 1
        assert matched[0]["run_id"] == "aaa"
        # Non-matching graph name
        empty = s.list_runs(graph_name="nonexistent")
        assert len(empty) == 0
    finally:
        s.stop()


def test_writer_circuit_breaker_trips_after_threshold(tmp_path):
    """M15: circuit_open becomes True after _CIRCUIT_OPEN_THRESHOLD consecutive errors."""
    from voice_commander.observability.store import _CIRCUIT_OPEN_THRESHOLD
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=256, daemon_pid=1)
    s.start()
    try:
        assert not s.circuit_open
        # Trigger consecutive errors by closing the writer connection externally
        # We can simulate this by monkey-patching _insert_run_start to raise
        original_insert = s._insert_run_start

        call_count = 0

        def failing_insert(conn, rec):
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("simulated failure")

        s._insert_run_start = failing_insert  # type: ignore[method-assign]

        # Enqueue enough writes to trip the circuit
        for i in range(_CIRCUIT_OPEN_THRESHOLD):
            s.write_run_start(RunRecord(f"r{i}", 1000.0 + i, f"t{i}", 1))

        # Give the writer thread time to process
        import time as _t
        _t.sleep(0.2)
        _drain(s)

        assert s.circuit_open, "circuit should be open after consecutive failures"

        # Restore
        s._insert_run_start = original_insert  # type: ignore[method-assign]
    finally:
        s.stop()
