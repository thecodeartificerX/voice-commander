"""Tests for store.py v1 → v2 schema migration."""
import sqlite3
import tempfile
from pathlib import Path
import pytest


def make_v1_db(path: Path) -> None:
    """Create a v1 database (no error_category columns)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            started_at REAL NOT NULL,
            ended_at REAL,
            transcript TEXT NOT NULL,
            status TEXT NOT NULL,
            error_msg TEXT,
            duration_ms INTEGER,
            daemon_pid INTEGER NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE spans (
            span_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            parent_span_id TEXT,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            started_at REAL NOT NULL,
            ended_at REAL,
            duration_ms INTEGER,
            status TEXT NOT NULL,
            attrs TEXT NOT NULL,
            output TEXT,
            error_type TEXT,
            error_msg TEXT,
            traceback TEXT
        );
        INSERT INTO runs (run_id, started_at, transcript, status, daemon_pid, schema_version)
        VALUES ('old-run-1', 1000.0, 'test', 'ok', 9999, 1);
    """)
    conn.close()


def test_migrate_adds_error_category_to_runs():
    from voice_commander.observability.store import Store
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "runs.db"
        make_v1_db(db)
        store = Store(db, keep_runs=100, queue_max=100, daemon_pid=1)
        store.start()
        store.stop()
        # Verify column exists
        conn = sqlite3.connect(db)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()]
        conn.close()
        assert "error_category" in cols


def test_migrate_adds_error_category_to_spans():
    from voice_commander.observability.store import Store
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "runs.db"
        make_v1_db(db)
        store = Store(db, keep_runs=100, queue_max=100, daemon_pid=1)
        store.start()
        store.stop()
        conn = sqlite3.connect(db)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(spans)").fetchall()]
        conn.close()
        assert "error_category" in cols


def test_migrate_preserves_existing_rows():
    """Store migrates a v1 DB in-place, preserving existing rows.

    The idx_runs_category index is now created inside migrate() (after the
    error_category column is added), not in _SCHEMA. This means _open_or_recreate()
    no longer fails on a v1 DB, and existing run history survives the upgrade.
    """
    from voice_commander.observability.store import Store
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "runs.db"
        make_v1_db(db)
        store = Store(db, keep_runs=100, queue_max=100, daemon_pid=99999)
        store.start()
        store.stop()
        # The row from the v1 DB must still be present — migration is in-place.
        assert db.exists()
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT run_id FROM runs WHERE run_id='old-run-1'").fetchall()
        conn.close()
        assert len(rows) == 1, "v1 row must survive in-place migration (not backed-up/wiped)"


def test_migrate_idempotent():
    """Running migration twice doesn't raise."""
    from voice_commander.observability.store import Store
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "runs.db"
        make_v1_db(db)
        store = Store(db, keep_runs=100, queue_max=100, daemon_pid=1)
        store.start()
        store.stop()
        # Second start — should not raise
        store2 = Store(db, keep_runs=100, queue_max=100, daemon_pid=1)
        store2.start()
        store2.stop()


def test_migrate_rolls_back_on_partial_failure(monkeypatch):
    """B-H4: partial migration failure rolls back entirely (no half-applied schema).

    If the second ALTER raises mid-migration, the first ALTER must not persist
    — otherwise the next startup hits a duplicate-column error or a half-broken
    schema. With the transaction wrapper, all-or-nothing.
    """
    from voice_commander.observability import store as store_mod
    from voice_commander.observability.store import Store

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "runs.db"
        make_v1_db(db)

        # Patch _safe_alter to raise on the SECOND call so the first ALTER
        # has already happened inside the transaction.
        original = Store._safe_alter
        call_count = {"n": 0}

        def flaky_alter(conn, ddl):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated mid-migration crash")
            return original(conn, ddl)

        monkeypatch.setattr(Store, "_safe_alter", staticmethod(flaky_alter))

        store = Store(db, keep_runs=100, queue_max=100, daemon_pid=1)
        with pytest.raises(RuntimeError):
            store.start()

        # After failed migration, the DB must NOT have any of the new columns
        # (transaction rolled back).
        conn = sqlite3.connect(db)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        conn.close()
        assert "error_category" not in cols, (
            "first ALTER must have rolled back when second ALTER failed"
        )
        assert "error_summary" not in cols


def test_migrate_safe_against_duplicate_column():
    """B-H4: ``duplicate column`` from a previously-applied ALTER is swallowed."""
    import sqlite3 as _sqlite3
    from voice_commander.observability.store import Store

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "runs.db"
        make_v1_db(db)

        # Pre-add error_category to runs by hand so PRAGMA reports it absent
        # but the in-transaction ALTER would still see it. We simulate this
        # by directly forcing a `duplicate column` situation.
        conn = _sqlite3.connect(db)
        conn.execute("ALTER TABLE runs ADD COLUMN error_category TEXT NULL")
        conn.close()

        # Now Store should still migrate cleanly — it'll detect the existing
        # column via PRAGMA and skip the ALTER entirely; if a race made it
        # try anyway, _safe_alter swallows it.
        store = Store(db, keep_runs=100, queue_max=100, daemon_pid=1)
        store.start()
        store.stop()
