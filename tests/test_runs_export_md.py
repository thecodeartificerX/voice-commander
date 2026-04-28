"""Test /api/runs/{id}/export.md endpoint and format."""
import os
import time
import tempfile
from pathlib import Path
import pytest


def test_export_md_contains_header():
    """export.md endpoint format check (direct store test)."""
    from voice_commander.observability.store import Store, RunRecord, RunUpdate, SpanRecord

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "runs.db", keep_runs=100, queue_max=100, daemon_pid=os.getpid())
        store.start()

        run_id = "test-export-run-abc"
        store.write_run_start(RunRecord(
            run_id=run_id,
            started_at=time.time(),
            transcript="play spotify",
            daemon_pid=os.getpid(),
        ))
        store.write_run_end(RunUpdate(
            run_id=run_id,
            ended_at=time.time() + 1,
            status="ok",
            error_msg=None,
            duration_ms=1000,
        ))
        store.flush()
        store.stop()

        run = store.get_run(run_id)
        assert run is not None

        from voice_commander.observability.api import _build_export_md
        spans = store.get_spans(run_id)
        md = _build_export_md(run, spans)
        assert "# Voice Commander run" in md
        assert "play spotify" in md
        assert "## Span tree" in md


def test_export_md_error_run():
    """export.md includes failure summary for error runs."""
    from voice_commander.observability.store import Store, RunRecord, RunUpdate, SpanRecord

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "runs.db", keep_runs=100, queue_max=100, daemon_pid=os.getpid())
        store.start()

        run_id = "err-run-xyz"
        store.write_run_start(RunRecord(
            run_id=run_id,
            started_at=time.time(),
            transcript="broken command",
            daemon_pid=os.getpid(),
        ))
        store.write_run_end(RunUpdate(
            run_id=run_id,
            ended_at=time.time() + 0.5,
            status="error",
            error_msg="missing kwarg",
            duration_ms=500,
            error_category="wiring",
        ))
        store.flush()
        store.stop()

        run = store.get_run(run_id)
        assert run is not None

        from voice_commander.observability.api import _build_export_md
        md = _build_export_md(run, [])
        assert "wiring" in md
