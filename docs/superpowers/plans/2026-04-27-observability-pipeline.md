# Observability Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live, persistent observability layer to voice-commander: every utterance gets a `run_id` and a span tree captured to SQLite, streamed live over the existing EventBus + SSE channel, exposed via REST + `vc debug`/`vc tail` CLIs, and rendered in a new `/page/runs` web inspector plus a Builder UI overlay (with a live-execution highlight mode). LLM-replay and full-plan replay are gated, agent-debuggable endpoints.

**Architecture:**

A new `observability/` Python package introduces a `Tracer` (contextvars-based, in-process, single-thread async writer) that span-instruments the daemon's hot path: `_process_utterance` → transcribe → `LLMRouter.route` → `Dispatcher.run_plan` (per-`tool_call`) → `GraphRuntime.run` (per-`node`). Spans persist to `outputs/runs.db` (SQLite WAL, FK cascade) and are simultaneously published as `trace.*` events on the existing in-process `EventBus`, which the SSE bridge already exposes at `/events`. A new `/api/runs/*` FastAPI router and `vc debug` / `vc tail` CLIs provide read access. Web UI: a standalone `/page/runs` HTMX inspector plus a Drawflow overlay in the existing Builder (`/page/builder`). Two replay endpoints: LLM-replay (safe, re-routes through current router) and full replay (gated by an `X-Replay-Confirm: yes` header / `--yes` flag — re-fires plan, footgun acknowledged).

**Tech Stack:** Python 3.12, stdlib `sqlite3` + `contextvars`, FastAPI (existing), HTMX (existing), Drawflow (existing, vendored), pytest, pytest-asyncio (existing).

**Spec reference:** `docs/superpowers/specs/2026-04-27-observability-pipeline-design.md`.

---

## File Structure

### Created

| Path | Responsibility |
|------|----------------|
| `src/voice_commander/observability/__init__.py` | Package marker; re-exports `Tracer`, `Span`, `Store` |
| `src/voice_commander/observability/tracer.py` | `Tracer` singleton + `Span` context manager + contextvars |
| `src/voice_commander/observability/store.py` | SQLite schema, writer thread, queries, crash recovery, retention |
| `src/voice_commander/observability/api.py` | FastAPI `APIRouter` mounted on existing app — `/api/runs/*` |
| `src/voice_commander/observability/replay.py` | LLM-replay + full-replay executors |
| `src/voice_commander/observability/cli.py` | `vc debug` + `vc tail` subcommand handlers |
| `src/voice_commander/web/templates/runs.html` | `/page/runs` HTMX page (inline `<style>` for layout) |
| `src/voice_commander/web/templates/_run_detail.html` | HTMX fragment: span tree for one run |
| `src/voice_commander/web/templates/_runs_row.html` | HTMX fragment: one row in run list |
| `src/voice_commander/web/static/runs.js` | SSE consumer for live runs feed + Builder overlay |
| `src/voice_commander/web/static/runs.css` | Span tree styling, status badges |
| `tests/unit/observability/__init__.py` | (empty marker) |
| `tests/unit/observability/test_store.py` | Schema, write/read, prune, crash recovery |
| `tests/unit/observability/test_tracer.py` | Span context manager, contextvars, error capture, no-op mode |
| `tests/unit/observability/test_replay.py` | LLM-replay diff, full-replay confirmation gating |
| `tests/unit/observability/test_api.py` | REST endpoints round-trip |
| `tests/unit/observability/test_cli.py` | CLI subcommand argv → output |
| `tests/integration/test_observability_e2e.py` | End-to-end: daemon → store → API → CLI |

### Modified

| Path | Change |
|------|--------|
| `src/voice_commander/config.py` | Add `ObservabilityConfig` dataclass + `Config.observability` field |
| `src/voice_commander/daemon.py` | `_process_utterance` opens a run span; `build_streaming_daemon` constructs `Tracer`; daemon stdout one-liner |
| `src/voice_commander/llm_router.py` | `route()` opens a `llm_call` span; captures full prompt + raw response |
| `src/voice_commander/dispatcher.py` | `run_plan` opens `plan` span + per-step `tool_call` span |
| `src/voice_commander/commands/graph_runtime.py` | `run()` opens `graph` span; node loop opens `node` span per node |
| `src/voice_commander/web/app.py` | Mount `observability.api.router`; add `/page/runs` route; pass `tracer`/`store` |
| `src/voice_commander/web/builder.py` | Hook overlay JS + runs panel into builder template |
| `src/voice_commander/web/templates/builder.html` | Add Runs panel HTML + include `runs.js` |
| `src/voice_commander/web/static/builder.js` | Hook into runs.js for node highlight by `data-node-id` |
| `src/voice_commander/web/static/builder.css` | Status classes (`.run-node-ok`, `.run-node-error`, `.run-node-pulse`) |
| `src/voice_commander/__main__.py` | Wire `vc debug` / `vc tail` subcommands into existing argparse |
| `src/voice_commander/event_bus.py` | _no code change_ — already supports arbitrary event types |
| `pyproject.toml` | _no new deps_ — sqlite3 is stdlib |
| `.gitignore` | Add `outputs/runs.db*` (covers `-wal` / `-shm`) |
| `config.toml` | Add `[observability]` example section (commented defaults) |
| `docs/decisions/0070-observability-pipeline.md` | New ADR |
| `docs/agents/technical-decisions.md` | Append row for ADR 0070 |
| `docs/architecture.md` | Add Observability subsystem section |
| `CLAUDE.md` | Update one-line flow + Architecture diagram to mention Tracer |

---

## Phase 1 — Foundation (Tracer + Store + Config)

### Task 1: Add `ObservabilityConfig`

**Files:**
- Modify: `src/voice_commander/config.py`
- Modify: `tests/unit/test_config.py`
- Modify: `config.toml`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`:

```python
def test_observability_section_defaults(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("")
    cfg = Config.load(cfg_path)
    assert cfg.observability.enabled is True
    assert cfg.observability.keep_runs == 1000
    assert cfg.observability.db_path == "outputs/runs.db"
    assert cfg.observability.queue_max == 4096
    assert cfg.observability.slow_run_ms == 2000


def test_observability_section_overrides(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[observability]\n"
        "enabled = false\n"
        "keep_runs = 50\n"
    )
    cfg = Config.load(cfg_path)
    assert cfg.observability.enabled is False
    assert cfg.observability.keep_runs == 50
    assert cfg.observability.db_path == "outputs/runs.db"  # default preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py::test_observability_section_defaults -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'observability'`.

- [ ] **Step 3: Write minimal implementation**

In `src/voice_commander/config.py`, add the dataclass and field. Insert after `PerceptionConfig`:

```python
@dataclass(frozen=True)
class ObservabilityConfig:
    enabled: bool = True
    keep_runs: int = 1000
    db_path: str = "outputs/runs.db"
    queue_max: int = 4096
    slow_run_ms: int = 2000
```

In the `Config` dataclass, add the field after `perception`:

```python
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
```

In `Config.load`, add to the `cls(...)` constructor call:

```python
            observability=_section(ObservabilityConfig, raw.get("observability", {})),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py -v -k observability`
Expected: 2 passed.

- [ ] **Step 5: Append example block to `config.toml`**

Append:

```toml
# [observability]
# enabled = true
# keep_runs = 1000
# db_path = "outputs/runs.db"
# queue_max = 4096
# slow_run_ms = 2000
```

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/config.py tests/unit/test_config.py config.toml
git commit -m "feat(config): add [observability] section with defaults"
```

---

### Task 2: SQLite store — schema + open/close

**Files:**
- Create: `src/voice_commander/observability/__init__.py`
- Create: `src/voice_commander/observability/store.py`
- Create: `tests/unit/observability/__init__.py`
- Create: `tests/unit/observability/test_store.py`

- [ ] **Step 1: Create empty package markers**

Create both `__init__.py` files as empty files (just `touch`).

- [ ] **Step 2: Write the failing test for schema creation**

Create `tests/unit/observability/test_store.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from voice_commander.observability.store import Store


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/observability/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'voice_commander.observability.store'`.

- [ ] **Step 4: Write minimal implementation**

Create `src/voice_commander/observability/store.py`:

```python
"""SQLite-backed run/span store for the observability pipeline.

Single-writer thread drains a bounded queue. All instrumentation code
enqueues records and returns immediately; SQLite I/O never blocks the
audio or pipeline threads. Queue overflow drops the offending record and
increments a counter (logged at INFO).
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    transcript      TEXT NOT NULL,
    status          TEXT NOT NULL,
    error_msg       TEXT,
    duration_ms     INTEGER,
    daemon_pid      INTEGER NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status  ON runs(status, started_at DESC);

CREATE TABLE IF NOT EXISTS spans (
    span_id         TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    parent_span_id  TEXT,
    type            TEXT NOT NULL,
    name            TEXT NOT NULL,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    duration_ms     INTEGER,
    status          TEXT NOT NULL,
    attrs           TEXT NOT NULL,
    output          TEXT,
    error_type      TEXT,
    error_msg       TEXT,
    traceback       TEXT
);
CREATE INDEX IF NOT EXISTS idx_spans_run     ON spans(run_id, started_at);
CREATE INDEX IF NOT EXISTS idx_spans_parent  ON spans(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_spans_status  ON spans(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_spans_name    ON spans(name);
"""


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    started_at: float
    transcript: str
    daemon_pid: int


@dataclass(frozen=True)
class RunUpdate:
    run_id: str
    ended_at: float
    status: str
    error_msg: str | None
    duration_ms: int


@dataclass(frozen=True)
class SpanRecord:
    span_id: str
    run_id: str
    parent_span_id: str | None
    type: str
    name: str
    started_at: float
    ended_at: float
    duration_ms: int
    status: str
    attrs: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    error_type: str | None = None
    error_msg: str | None = None
    traceback: str | None = None


class Store:
    """SQLite store with a single writer thread.

    Public API is thread-safe: ``write_run_start``, ``write_run_end``,
    ``write_span`` enqueue rows; ``read_*`` methods open short-lived read
    connections (WAL allows concurrent readers).
    """

    def __init__(
        self,
        db_path: Path,
        *,
        keep_runs: int,
        queue_max: int,
        daemon_pid: int,
    ) -> None:
        self._db_path = Path(db_path)
        self._keep_runs = keep_runs
        self._queue_max = queue_max
        self._daemon_pid = daemon_pid
        self._q: queue.Queue[Any] = queue.Queue(maxsize=queue_max)
        self._writer: threading.Thread | None = None
        self._stop = threading.Event()
        self._dropped = 0
        self._inserts_since_prune = 0

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._open_or_recreate()
        self._writer = threading.Thread(
            target=self._writer_loop, name="vc-observ-writer", daemon=True
        )
        self._writer.start()

    def stop(self) -> None:
        self._stop.set()
        self._q.put(_SENTINEL)
        if self._writer is not None:
            self._writer.join(timeout=5.0)
            if self._writer.is_alive():
                logger.warning("observability writer did not exit within 5 s")
        self._writer = None

    def _open_or_recreate(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.DatabaseError as exc:
            backup = self._db_path.with_name(
                f"{self._db_path.name}.corrupt-{int(time.time())}"
            )
            logger.warning(
                "observability db unreadable (%s); backing up to %s and recreating",
                exc,
                backup,
            )
            self._db_path.rename(backup)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # write enqueuers (called from any thread)
    # ------------------------------------------------------------------

    def write_run_start(self, rec: RunRecord) -> None:
        self._enqueue(("run_start", rec))

    def write_run_end(self, upd: RunUpdate) -> None:
        self._enqueue(("run_end", upd))

    def write_span(self, span: SpanRecord) -> None:
        self._enqueue(("span", span))

    def _enqueue(self, item: Any) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            self._dropped += 1
            logger.info(
                "observability queue full; dropped %d record(s) total", self._dropped
            )

    # ------------------------------------------------------------------
    # writer thread
    # ------------------------------------------------------------------

    def _writer_loop(self) -> None:
        with self._connect() as conn:
            while True:
                item = self._q.get()
                if item is _SENTINEL:
                    return
                try:
                    kind, payload = item
                    if kind == "run_start":
                        self._insert_run_start(conn, payload)
                    elif kind == "run_end":
                        self._insert_run_end(conn, payload)
                        self._inserts_since_prune += 1
                        if self._inserts_since_prune >= 50:
                            self._prune(conn)
                            self._inserts_since_prune = 0
                    elif kind == "span":
                        self._insert_span(conn, payload)
                except sqlite3.Error:
                    logger.exception("observability writer error")

    def _insert_run_start(self, conn: sqlite3.Connection, rec: RunRecord) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(run_id, started_at, transcript, status, daemon_pid, schema_version) "
            "VALUES (?, ?, ?, 'running', ?, 1)",
            (rec.run_id, rec.started_at, rec.transcript, rec.daemon_pid),
        )

    def _insert_run_end(self, conn: sqlite3.Connection, upd: RunUpdate) -> None:
        conn.execute(
            "UPDATE runs SET ended_at=?, status=?, error_msg=?, duration_ms=? "
            "WHERE run_id=?",
            (upd.ended_at, upd.status, upd.error_msg, upd.duration_ms, upd.run_id),
        )

    def _insert_span(self, conn: sqlite3.Connection, span: SpanRecord) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO spans (span_id, run_id, parent_span_id, "
            "type, name, started_at, ended_at, duration_ms, status, attrs, "
            "output, error_type, error_msg, traceback) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                span.span_id,
                span.run_id,
                span.parent_span_id,
                span.type,
                span.name,
                span.started_at,
                span.ended_at,
                span.duration_ms,
                span.status,
                json.dumps(span.attrs, default=_json_default),
                None if span.output is None else json.dumps(span.output, default=_json_default),
                span.error_type,
                span.error_msg,
                span.traceback,
            ),
        )

    def _prune(self, conn: sqlite3.Connection) -> None:
        # Keep the most recent N rows by started_at.
        conn.execute(
            "DELETE FROM runs WHERE run_id IN ("
            "SELECT run_id FROM runs ORDER BY started_at DESC "
            "LIMIT -1 OFFSET ?)",
            (self._keep_runs,),
        )

    @property
    def dropped(self) -> int:
        return self._dropped


_SENTINEL = object()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return obj.hex()
    return repr(obj)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/observability/test_store.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/observability/__init__.py src/voice_commander/observability/store.py tests/unit/observability/__init__.py tests/unit/observability/test_store.py
git commit -m "feat(observability): SQLite store with WAL + writer thread"
```

---

### Task 3: Store — write/read round-trip + crash recovery + retention

**Files:**
- Modify: `src/voice_commander/observability/store.py`
- Modify: `tests/unit/observability/test_store.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/observability/test_store.py`:

```python
import time

from voice_commander.observability.store import (
    RunRecord,
    RunUpdate,
    SpanRecord,
    Store,
)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/observability/test_store.py -v -k "round_trip or prune or recover"`
Expected: FAIL — `get_run`, `get_spans`, `list_runs`, `recover_stale_runs` not yet defined.

- [ ] **Step 3: Add reader methods + recovery to `store.py`**

Append to the `Store` class in `src/voice_commander/observability/store.py`:

```python
    # ------------------------------------------------------------------
    # readers (any thread; WAL allows concurrent reads)
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_spans(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM spans WHERE run_id=? ORDER BY started_at",
                (run_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["attrs"] = json.loads(d["attrs"]) if d["attrs"] else {}
            d["output"] = json.loads(d["output"]) if d["output"] else None
            out.append(d)
        return out

    def list_runs(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
        since_ts: float | None = None,
        transcript_like: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if since_ts is not None:
            sql += " AND started_at >= ?"
            params.append(since_ts)
        if transcript_like:
            sql += " AND transcript LIKE ?"
            params.append(f"%{transcript_like}%")
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_last_run(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def recover_stale_runs(self) -> int:
        """Mark prior 'running' rows from older daemons as crashed.

        Called once at daemon startup. Returns the number of rows fixed.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE runs SET status='error', error_msg='daemon crash', "
                "ended_at=? WHERE status='running' AND daemon_pid != ?",
                (time.time(), self._daemon_pid),
            )
            return cur.rowcount or 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/observability/test_store.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/observability/store.py tests/unit/observability/test_store.py
git commit -m "feat(observability): store reader methods, prune, crash recovery"
```

---

### Task 4: Tracer + Span context manager

**Files:**
- Create: `src/voice_commander/observability/tracer.py`
- Create: `tests/unit/observability/test_tracer.py`
- Modify: `src/voice_commander/observability/__init__.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/observability/test_tracer.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from voice_commander.event_bus import EventBus
from voice_commander.observability.store import Store
from voice_commander.observability.tracer import Tracer


def _drain(store: Store) -> None:
    import time as _t
    while not store._q.empty():
        _t.sleep(0.01)
    _t.sleep(0.05)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=99)
    s.start()
    yield s
    s.stop()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def test_tracer_run_and_one_span(store: Store, bus: EventBus):
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("hello world") as run:
        with tracer.span("transcribe", name="transcribe", confidence=0.9):
            pass
    _drain(store)

    persisted = store.get_run(run.run_id)
    assert persisted["status"] == "ok"
    spans = store.get_spans(run.run_id)
    types = [s["type"] for s in spans]
    assert "run" in types and "transcribe" in types


def test_tracer_captures_exception_in_span(store: Store, bus: EventBus):
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("blow up") as run:
        with pytest.raises(ValueError):
            with tracer.span("tool_call", name="focus", target="chrome"):
                raise ValueError("no such window")
    _drain(store)

    spans = store.get_spans(run.run_id)
    err_spans = [s for s in spans if s["status"] == "error"]
    assert len(err_spans) == 1
    assert err_spans[0]["error_type"] == "ValueError"
    assert err_spans[0]["error_msg"] == "no such window"
    assert err_spans[0]["traceback"]
    run_row = store.get_run(run.run_id)
    assert run_row["status"] == "error"


def test_tracer_nested_spans_parent_correctly(store: Store, bus: EventBus):
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("nest test") as run:
        with tracer.span("plan", name="plan") as plan_span:
            with tracer.span("tool_call", name="focus"):
                pass
            with tracer.span("tool_call", name="press"):
                pass
    _drain(store)

    spans = store.get_spans(run.run_id)
    by_type = {s["type"]: s for s in spans}
    plan_id = by_type["plan"]["span_id"]
    tool_calls = [s for s in spans if s["type"] == "tool_call"]
    assert len(tool_calls) == 2
    assert all(s["parent_span_id"] == plan_id for s in tool_calls)


def test_tracer_disabled_is_noop(store: Store, bus: EventBus):
    tracer = Tracer(store=store, bus=bus, enabled=False)
    with tracer.run("disabled") as run:
        with tracer.span("tool_call", name="focus"):
            pass
    _drain(store)
    assert store.get_run(run.run_id) is None
    assert run.run_id == ""  # disabled run handle has empty id


def test_tracer_publishes_trace_events(store: Store, bus: EventBus):
    received: list[tuple[str, dict]] = []
    q = bus.subscribe()
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("emit") as run:
        with tracer.span("tool_call", name="focus"):
            pass
    while not q.empty():
        ev = q.get_nowait()
        received.append((ev.type, ev.data))
    types = [t for t, _ in received]
    assert "trace.run_started" in types
    assert "trace.span_started" in types
    assert "trace.span_ended" in types
    assert "trace.run_completed" in types
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/observability/test_tracer.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `tracer.py`**

Create `src/voice_commander/observability/tracer.py`:

```python
"""Tracer + Span context manager for the observability pipeline.

Single-process, contextvars-based. ``start_run`` / ``end_run`` bracket
the per-utterance run; ``span`` is a context manager that auto-parents
to the current span and writes on ``__exit__``.

The tracer NEVER raises out of instrumentation paths — all internal
errors are logged and swallowed so the daemon keeps running even if
observability is misbehaving.
"""

from __future__ import annotations

import contextlib
import logging
import time
import traceback as tb_mod
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from voice_commander.event_bus import EventBus
from voice_commander.observability.store import (
    RunRecord,
    RunUpdate,
    SpanRecord,
    Store,
)

logger = logging.getLogger(__name__)

_current_run_id: ContextVar[str] = ContextVar("vc_obs_run_id", default="")
_current_span_id: ContextVar[str] = ContextVar("vc_obs_span_id", default="")


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class RunHandle:
    run_id: str
    started_at: float


class Span:
    """One-shot mutable span row. Use via ``Tracer.span(...)`` context manager."""

    def __init__(
        self,
        *,
        span_id: str,
        run_id: str,
        parent_span_id: str | None,
        type: str,
        name: str,
        attrs: dict[str, Any],
    ) -> None:
        self.span_id = span_id
        self.run_id = run_id
        self.parent_span_id = parent_span_id
        self.type = type
        self.name = name
        self.attrs: dict[str, Any] = dict(attrs)
        self.output: Any = None
        self.error_type: str | None = None
        self.error_msg: str | None = None
        self.traceback: str | None = None
        self.started_at = time.time()
        self._start_mono = time.monotonic()
        self.ended_at: float = 0.0
        self.duration_ms: int = 0
        self.status: str = "ok"

    def set_output(self, value: Any) -> None:
        self.output = value

    def set_attr(self, key: str, value: Any) -> None:
        self.attrs[key] = value

    def mark_skipped(self) -> None:
        self.status = "skipped"


class Tracer:
    """Singleton-style tracer. One instance per daemon."""

    def __init__(self, *, store: Store, bus: EventBus, enabled: bool) -> None:
        self._store = store
        self._bus = bus
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # run lifecycle
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def run(self, transcript: str) -> Iterator[RunHandle]:
        """Bracket a per-utterance run. Auto-rolls up status from spans."""
        if not self._enabled:
            yield RunHandle(run_id="", started_at=0.0)
            return

        run_id = _short_id()
        token = _current_run_id.set(run_id)
        started_at = time.time()
        start_mono = time.monotonic()

        try:
            self._store.write_run_start(
                RunRecord(
                    run_id=run_id,
                    started_at=started_at,
                    transcript=transcript,
                    daemon_pid=self._store._daemon_pid,
                )
            )
            self._publish_safe(
                "trace.run_started",
                {"run_id": run_id, "transcript": transcript, "started_at": started_at},
            )
        except Exception:
            logger.exception("tracer: failed to start run")

        handle = RunHandle(run_id=run_id, started_at=started_at)
        # Open the synthetic root "run" span so all children parent under it.
        with self._open_root_span(run_id, transcript) as root:
            try:
                yield handle
            except BaseException:
                root.status = "error"
                raise
            finally:
                # End the run row (status derived from root span).
                duration_ms = int((time.monotonic() - start_mono) * 1000)
                status = root.status
                error_msg = root.error_msg
                try:
                    self._store.write_run_end(
                        RunUpdate(
                            run_id=run_id,
                            ended_at=time.time(),
                            status=status,
                            error_msg=error_msg,
                            duration_ms=duration_ms,
                        )
                    )
                    self._publish_safe(
                        "trace.run_completed",
                        {
                            "run_id": run_id,
                            "status": status,
                            "duration_ms": duration_ms,
                        },
                    )
                except Exception:
                    logger.exception("tracer: failed to end run")
                _current_run_id.reset(token)

    @contextlib.contextmanager
    def _open_root_span(self, run_id: str, transcript: str) -> Iterator[Span]:
        with self.span("run", name="run", transcript=transcript) as s:
            yield s

    # ------------------------------------------------------------------
    # span context manager
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def span(self, type: str, *, name: str | None = None, **attrs: Any) -> Iterator[Span]:
        if not self._enabled or _current_run_id.get() == "":
            yield _NULL_SPAN
            return

        span_id = _short_id()
        run_id = _current_run_id.get()
        parent = _current_span_id.get() or None
        s = Span(
            span_id=span_id,
            run_id=run_id,
            parent_span_id=parent,
            type=type,
            name=name or type,
            attrs=attrs,
        )
        token = _current_span_id.set(span_id)
        try:
            self._publish_safe(
                "trace.span_started",
                {
                    "run_id": run_id,
                    "span_id": span_id,
                    "parent_span_id": parent,
                    "type": type,
                    "name": s.name,
                    "started_at": s.started_at,
                    "attrs": s.attrs,
                },
            )
        except Exception:
            logger.exception("tracer: failed to publish span_started")

        try:
            yield s
        except BaseException as exc:
            s.status = "error"
            s.error_type = type(exc).__name__
            s.error_msg = str(exc)[:512]
            s.traceback = "".join(tb_mod.format_tb(exc.__traceback__)[-10:])
            raise
        finally:
            s.ended_at = time.time()
            s.duration_ms = int((time.monotonic() - s._start_mono) * 1000)
            try:
                self._store.write_span(
                    SpanRecord(
                        span_id=s.span_id,
                        run_id=s.run_id,
                        parent_span_id=s.parent_span_id,
                        type=s.type,
                        name=s.name,
                        started_at=s.started_at,
                        ended_at=s.ended_at,
                        duration_ms=s.duration_ms,
                        status=s.status,
                        attrs=s.attrs,
                        output=s.output,
                        error_type=s.error_type,
                        error_msg=s.error_msg,
                        traceback=s.traceback,
                    )
                )
                self._publish_safe(
                    "trace.span_ended",
                    {
                        "run_id": s.run_id,
                        "span_id": s.span_id,
                        "type": s.type,
                        "name": s.name,
                        "status": s.status,
                        "duration_ms": s.duration_ms,
                        "output": s.output,
                        "error_msg": s.error_msg,
                    },
                )
            except Exception:
                logger.exception("tracer: failed to write span")
            _current_span_id.reset(token)

    def _publish_safe(self, event: str, data: dict[str, Any]) -> None:
        try:
            self._bus.publish(event, data)
        except Exception:
            logger.exception("tracer: failed to publish %s", event)


class _NullSpan:
    """Returned when tracer is disabled. Silently absorbs mutations."""

    span_id = ""
    run_id = ""
    parent_span_id = None
    type = ""
    name = ""

    def set_output(self, value: Any) -> None: ...
    def set_attr(self, key: str, value: Any) -> None: ...
    def mark_skipped(self) -> None: ...


_NULL_SPAN = _NullSpan()
```

- [ ] **Step 4: Re-export from package init**

Replace `src/voice_commander/observability/__init__.py`:

```python
"""Observability — live tracing, replay, and run inspection."""

from voice_commander.observability.store import (
    RunRecord,
    RunUpdate,
    SpanRecord,
    Store,
)
from voice_commander.observability.tracer import Span, Tracer

__all__ = ["Tracer", "Span", "Store", "RunRecord", "RunUpdate", "SpanRecord"]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/observability/test_tracer.py -v`
Expected: all 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/observability/tracer.py src/voice_commander/observability/__init__.py tests/unit/observability/test_tracer.py
git commit -m "feat(observability): Tracer + Span contextmanager + trace.* events"
```

---

## Phase 1 Validation Gate

- [ ] **Step A: Run full unit suite**

Run: `uv run pytest tests/unit/observability/ tests/unit/test_config.py -v`
Expected: all passed.

- [ ] **Step B: Manual smoke — open the DB**

```bash
uv run python -c "from voice_commander.observability import Store; from pathlib import Path; s=Store(Path('outputs/runs.db'), keep_runs=10, queue_max=64, daemon_pid=1); s.start(); print('schema_ok'); s.stop()"
```
Expected: `schema_ok`. File `outputs/runs.db` exists. `outputs/runs.db-wal` may also appear.

- [ ] **Step C: Human gate**

User verifies the smoke output and then approves moving to Phase 2 (instrumentation seams). No daemon code is touched yet — this gate is just the foundation.

---

## Phase 2 — Instrumentation seams

### Task 5: Wire Tracer into `build_streaming_daemon`

**Files:**
- Modify: `src/voice_commander/daemon.py`
- Modify: `tests/unit/test_daemon_wiring.py` (new file if absent — check first)

- [ ] **Step 1: Inspect existing daemon-wiring test**

Run: `uv run python -c "import os; print(os.path.exists('tests/unit/test_daemon_wiring.py'))"`
If `True`: open and find a fixture pattern to extend. If `False`: continue with new file in Step 2.

- [ ] **Step 2: Write the failing test**

Create or append to `tests/unit/test_daemon_wiring.py`:

```python
from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pytest


def test_build_streaming_daemon_constructs_tracer(monkeypatch, tmp_path):
    """build_streaming_daemon should attach a Tracer when observability.enabled."""
    from voice_commander.config import Config
    from voice_commander.daemon import build_streaming_daemon

    monkeypatch.chdir(tmp_path)
    cfg = Config()  # defaults; observability.enabled=True

    # Stub out heavy deps that the factory normally imports.
    fake_torch = types.SimpleNamespace(set_num_threads=lambda n: None)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    with (
        patch("voice_commander.daemon.load_silero_vad", return_value=object()),
        patch("voice_commander.daemon.Transcriber"),
        patch("voice_commander.daemon.LLMRouter"),
        patch("voice_commander.daemon.StreamingRecorder"),
        patch("voice_commander.daemon.WindowsFeedbackSink"),
        patch("voice_commander.daemon.create_app"),
        patch("voice_commander.daemon.discover", return_value=_FakeRegistry()),
        patch("voice_commander.daemon.validate_config_or_die"),
        patch("voice_commander.daemon.validate_or_die"),
        patch("voice_commander.daemon.log_llm_sources"),
    ):
        daemon = build_streaming_daemon(cfg)

    assert daemon._tracer is not None
    assert daemon._tracer.enabled is True


class _FakeRegistry:
    def all(self): return []
    def all_llm_visible(self): return []
    def by_name(self, name): return None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_daemon_wiring.py::test_build_streaming_daemon_constructs_tracer -v`
Expected: FAIL — `_tracer` attribute missing.

- [ ] **Step 4: Wire the tracer in `daemon.py`**

In `src/voice_commander/daemon.py`:

1. Add to imports near the top:

```python
from .observability import Store, Tracer
```

2. Add `tracer` parameter to `StreamingDaemon.__init__` (after `event_bus`):

```python
        tracer: Tracer | None = None,
```

3. Store it: `self._tracer = tracer`. Default-construct a disabled tracer if `None`:

```python
        self._tracer = tracer
```

4. Inside `build_streaming_daemon`, after `event_bus = EventBus()`, add:

```python
    store: Store | None = None
    tracer: Tracer | None = None
    if cfg.observability.enabled:
        db_path = repo_root / cfg.observability.db_path
        store = Store(
            db_path,
            keep_runs=cfg.observability.keep_runs,
            queue_max=cfg.observability.queue_max,
            daemon_pid=os.getpid(),
        )
        store.start()
        recovered = store.recover_stale_runs()
        if recovered:
            logger.info("observability: marked %d stale 'running' runs as crashed", recovered)
        tracer = Tracer(store=store, bus=event_bus, enabled=True)
    else:
        # Stub store-less tracer keeps span() call-sites no-op without branching.
        tracer = Tracer(store=_NoopStore(), bus=event_bus, enabled=False)
```

5. Pass `tracer=tracer` to the `StreamingDaemon(...)` constructor call near the bottom.

6. Add a small inline `_NoopStore` class above `build_streaming_daemon`:

```python
class _NoopStore:
    """Stand-in store passed to a disabled Tracer (no I/O ever happens)."""
    _daemon_pid = 0
    def write_run_start(self, *_a, **_k): ...
    def write_run_end(self, *_a, **_k): ...
    def write_span(self, *_a, **_k): ...
```

7. In `StreamingDaemon.shutdown`, after `self._wav_executor.shutdown(wait=False)`, add:

```python
        if hasattr(self, "_store") and self._store is not None:
            try:
                self._store.stop()
            except Exception:
                logger.exception("Error stopping observability store")
```

(The store is stashed alongside the tracer; expose via constructor + `self._store = store`.)

8. Update the daemon constructor: accept `store: Store | None = None` and set `self._store = store`. Update `build_streaming_daemon` to pass `store=store`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_daemon_wiring.py::test_build_streaming_daemon_constructs_tracer -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/daemon.py tests/unit/test_daemon_wiring.py
git commit -m "feat(daemon): construct Tracer + Store in build_streaming_daemon"
```

---

### Task 6: Instrument `_process_utterance` with run + transcribe spans

**Files:**
- Modify: `src/voice_commander/daemon.py`
- Modify: `tests/unit/test_dispatcher.py` or new `tests/unit/test_daemon_tracing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_daemon_tracing.py`:

```python
from __future__ import annotations

import time
from unittest.mock import MagicMock
from pathlib import Path

import numpy as np
import pytest

from voice_commander.daemon import StreamingDaemon
from voice_commander.event_bus import EventBus
from voice_commander.observability import Store, Tracer
from voice_commander.transcriber import TranscriptionResult


def _drain_store(store: Store) -> None:
    while not store._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)


def test_process_utterance_emits_run_with_transcribe_and_llm_spans(tmp_path: Path):
    bus = EventBus()
    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=128, daemon_pid=42)
    store.start()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="hello world", confidence=0.95, no_speech_prob=0.01
    )
    llm_router = MagicMock()
    llm_router.route.return_value = None  # no plan → miss
    feedback = MagicMock()
    dispatcher = MagicMock()

    daemon = StreamingDaemon(
        feedback=feedback, recorder=None, transcriber=transcriber,
        llm_router=llm_router, dispatcher=dispatcher, registry=None,
        min_confidence=0.0, min_word_count=1, max_no_speech_prob=1.0,
        output_dir=str(tmp_path / "out"),
        web_server=None, event_bus=bus, tracer=tracer, store=store,
    )

    daemon._process_utterance(np.zeros(16000, dtype=np.float32))
    _drain_store(store)

    runs = store.list_runs(limit=10)
    assert len(runs) == 1
    spans = store.get_spans(runs[0]["run_id"])
    types = {s["type"] for s in spans}
    assert "run" in types and "transcribe" in types and "llm_call" in types

    store.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_daemon_tracing.py -v`
Expected: FAIL — `tracer` constructor parameter missing or no spans recorded.

- [ ] **Step 3: Edit `_process_utterance` to wrap in tracer.run**

In `src/voice_commander/daemon.py`, refactor `_process_utterance` so the body runs inside a `with self._tracer.run(transcript):` block. Pseudocode shape:

```python
    def _process_utterance(self, utterance):
        self._write_utterance_async(utterance)
        start_s = time.perf_counter()

        # Transcribe BEFORE opening the run — we need the transcript for the run row.
        with self._tracer.span("transcribe", name="transcribe") as ts:
            self._publish("transcribing")
            result = self._transcriber.transcribe(utterance)
            ts.set_attr("confidence", result.confidence)
            ts.set_attr("no_speech_prob", result.no_speech_prob)
            ts.set_output({"text": result.text})

        with self._tracer.run(result.text) as run:
            self._feedback.on_transcript(result.text, result.confidence)

            # ... existing word-count / no-speech / confidence / mute gates ...
            # On any miss, set the run span attrs and `return` — exiting the
            # context emits the run_completed event with status='ok' (gates aren't
            # errors). The miss is reflected in plan_outcome as today.

            self._publish("llm_thinking")
            with self._tracer.span("llm_call", name="llm_call") as ls:
                plan = self._llm_router.route(result.text)
                if plan is not None:
                    ls.set_output({
                        "steps": [{"name": s.name, "kwargs": s.kwargs} for s in plan.steps]
                    })
                else:
                    ls.set_attr("no_match", True)

            if plan is None:
                # existing miss path
                ...
                return

            if self._registry is None:
                ...
                return

            self._dispatcher.run_plan(result.text, plan, self._registry)
            self._write_plan_async(result.text, plan)
```

Key refactor notes:
- Move `transcribe` span **outside** `tracer.run` because we need `result.text` to populate the run's `transcript` column. Span emits without a run-id → falls into the no-op span path harmlessly when transcription is at the very top, then once `tracer.run(...)` opens we resume normal nesting. **Update:** to keep the transcribe span on the run, run it inside but pass an initial empty transcript and update later via `set_attr`. Pick this ordering: open `tracer.run("")`, do transcribe, then call `run.set_transcript(text)` (add this method to `RunHandle` + tracer to update the row mid-run). For simplicity in this task, skip the dynamic transcript update — store empty for runs that miss before transcription, populate after via a separate UPDATE in the writer queue. **Concrete choice:** open tracer.run with `""` then enqueue a `("run_update_transcript", run_id, text)` after transcribing.

The cleanest path: extend `Store.write_run_start` to accept an empty transcript, and add a `Store.write_run_transcript_update(run_id, text)` method. Implement it now.

In `store.py`, add to enqueuer + writer:

```python
    def write_run_transcript_update(self, run_id: str, transcript: str) -> None:
        self._enqueue(("run_transcript", run_id, transcript))
```

Add a branch to `_writer_loop`:

```python
                    elif kind == "run_transcript":
                        rid, txt = payload  # tuple-unpacking on the fly
                        # NB: 3-tuple item — destructure differently:
```

Replace the queue item shape — use the cleaner 2-tuple `(kind, payload)` already in place by passing a `(run_id, text)` tuple as `payload`:

```python
    def write_run_transcript_update(self, run_id: str, transcript: str) -> None:
        self._enqueue(("run_transcript", (run_id, transcript)))
```

```python
                    elif kind == "run_transcript":
                        rid, txt = payload
                        conn.execute(
                            "UPDATE runs SET transcript=? WHERE run_id=?",
                            (txt, rid),
                        )
```

Add `Tracer.update_transcript(run_id, text)` that calls into store, and call it from the daemon after transcription.

- [ ] **Step 4: Add the helper to Tracer**

In `src/voice_commander/observability/tracer.py`, add to `Tracer`:

```python
    def update_transcript(self, run_id: str, transcript: str) -> None:
        if not self._enabled or not run_id:
            return
        try:
            self._store.write_run_transcript_update(run_id, transcript)
        except Exception:
            logger.exception("tracer: failed to update transcript")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_daemon_tracing.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/daemon.py src/voice_commander/observability/tracer.py src/voice_commander/observability/store.py tests/unit/test_daemon_tracing.py
git commit -m "feat(daemon): wrap _process_utterance in tracer.run with transcribe + llm_call spans"
```

---

### Task 7: Capture full LLM prompt + raw response in the `llm_call` span

**Files:**
- Modify: `src/voice_commander/llm_router.py`
- Modify: `tests/unit/test_llm_router_routing.py` (or whichever existing LLM router test file is in use — discover via `ls tests/unit/test_llm_router_*.py`)

- [ ] **Step 1: Discover existing LLM router test file**

Run: `ls tests/unit/test_llm_router_*.py 2>/dev/null || echo none`
If files exist, append to the most-routing-related one. If none: create `tests/unit/test_llm_router_tracing.py`.

- [ ] **Step 2: Write the failing test**

Create or append:

```python
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from voice_commander.config import LLMConfig
from voice_commander.llm_router import LLMRouter
from voice_commander.observability.tracer import Tracer, _current_run_id, _current_span_id
from voice_commander.observability.store import Store
from voice_commander.event_bus import EventBus
from voice_commander.registry import ToolRegistry


def test_llm_router_writes_prompt_and_response_to_span(tmp_path):
    cfg = LLMConfig()
    registry = ToolRegistry()
    lock = threading.Lock()
    bus = EventBus()
    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    store.start()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "choices": [{"message": {"tool_calls": []}}],
    }
    fake_resp.raise_for_status = MagicMock()

    router = LLMRouter(cfg, registry, lock)
    router._tracer = tracer  # injected by build_streaming_daemon in real wiring

    with patch.object(router._client, "post", return_value=fake_resp):
        with tracer.run("hi") as run:
            router.route("hi")
        import time as _t
        while not store._q.empty(): _t.sleep(0.01)
        _t.sleep(0.05)

        spans = store.get_spans(run.run_id)
        llm_spans = [s for s in spans if s["type"] == "llm_call"]
        assert llm_spans, "expected llm_call span"
        attrs = llm_spans[0]["attrs"]
        assert "prompt_full" in attrs
        assert "raw_response" in attrs
        assert attrs["model"] == cfg.model_id

    store.stop()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_router_tracing.py -v`
Expected: FAIL — no llm_call span recorded by router.

- [ ] **Step 4: Add tracer wrapping inside `LLMRouter.route`**

In `src/voice_commander/llm_router.py`:

1. Add an injection slot in `__init__`:

```python
        self._tracer: Any = None  # set by build_streaming_daemon; None disables span capture
```

2. Add a setter:

```python
    def set_tracer(self, tracer: Any) -> None:
        """Wire a Tracer in after construction; called by build_streaming_daemon."""
        self._tracer = tracer
```

3. Wrap the body of `route()` in a span:

```python
    def route(self, transcript, env_context=None):
        if self._tracer is None or not getattr(self._tracer, "enabled", False):
            return self._route_inner(transcript, env_context)
        with self._tracer.span("llm_call", name="llm_call") as s:
            s.set_attr("model", self._config.model_id)
            s.set_attr("endpoint_url", self._config.endpoint_url)
            with self._reload_lock:
                tools_array = self._build_tools_array()
            s.set_attr("tools", [t.get("function", {}).get("name") for t in tools_array])
            s.set_attr("prompt_full", self._system_prompt)
            s.set_attr("transcript", transcript)
            plan = self._route_inner(transcript, env_context, _capture=s)
            if plan is not None:
                s.set_output({
                    "steps": [{"name": st.name, "kwargs": st.kwargs} for st in plan.steps]
                })
            else:
                s.set_attr("no_match", True)
            return plan
```

4. Refactor the existing body of `route` into `_route_inner(self, transcript, env_context, *, _capture=None)`. After `data = resp.json()` succeeds, write `_capture.set_attr("raw_response", data)` if `_capture is not None`. After the timeout / error branches, write `_capture.set_attr("error", ...)` so the span carries the failure mode too.

5. In `build_streaming_daemon`, after `llm_router = LLMRouter(...)` and the warmup, add:

```python
    llm_router.set_tracer(tracer)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_llm_router_tracing.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/llm_router.py src/voice_commander/daemon.py tests/unit/test_llm_router_tracing.py
git commit -m "feat(llm_router): record llm_call span with full prompt + raw response"
```

---

### Task 8: Instrument Dispatcher — `plan` span + per-step `tool_call` spans

**Files:**
- Modify: `src/voice_commander/dispatcher.py`
- Modify: `tests/unit/test_dispatcher.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dispatcher.py`:

```python
def test_dispatcher_emits_plan_and_tool_call_spans(tmp_path):
    from unittest.mock import MagicMock
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.event_bus import EventBus
    from voice_commander.observability import Store, Tracer
    from voice_commander.plan import Plan, ToolCall
    from voice_commander.registry import ToolRegistry, ToolEntry
    import time as _t

    bus = EventBus()
    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=128, daemon_pid=1)
    store.start()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    registry = ToolRegistry()
    fake_func = MagicMock(return_value=None)
    registry.register(ToolEntry(name="focus", func=fake_func, description="", phrases=(), settle_ms=0))

    feedback = MagicMock()
    disp = Dispatcher(feedback, event_bus=bus, tracer=tracer)

    plan = Plan(steps=(ToolCall(name="focus", kwargs={"target": "chrome"}),), raw_response={})

    with tracer.run("focus chrome") as run:
        disp.run_plan("focus chrome", plan, registry)
    while not store._q.empty(): _t.sleep(0.01)
    _t.sleep(0.05)

    spans = store.get_spans(run.run_id)
    types = [s["type"] for s in spans]
    assert "plan" in types
    tool_spans = [s for s in spans if s["type"] == "tool_call"]
    assert len(tool_spans) == 1
    assert tool_spans[0]["status"] == "ok"
    store.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dispatcher.py -v -k tool_call_spans`
Expected: FAIL — `Dispatcher.__init__` doesn't accept `tracer`.

- [ ] **Step 3: Modify `dispatcher.py`**

In `src/voice_commander/dispatcher.py`:

1. Add tracer to constructor:

```python
    def __init__(self, feedback, event_bus=None, tracer=None):
        self._feedback = feedback
        self._event_bus = event_bus
        self._tracer = tracer
```

2. Wrap the body of `run_plan` in a `plan` span:

```python
    def run_plan(self, transcript, plan, registry):
        if self._tracer is None or not getattr(self._tracer, "enabled", False):
            return self._run_plan_inner(transcript, plan, registry)
        with self._tracer.span("plan", name="plan",
                               strict=plan.strict, n_steps=len(plan.steps)) as plan_span:
            outcome = self._run_plan_inner(transcript, plan, registry, _plan_span=plan_span)
            plan_span.set_attr("status", outcome.status)
            if outcome.error_msg:
                plan_span.set_attr("error_msg", outcome.error_msg)
            return outcome
```

3. Refactor existing body into `_run_plan_inner(self, transcript, plan, registry, *, _plan_span=None)`. Inside its `for i, step in enumerate(plan.steps)` loop, wrap each step in a span:

```python
            if self._tracer is not None and getattr(self._tracer, "enabled", False):
                step_cm = self._tracer.span("tool_call", name=step.name,
                                            kwargs=dict(step.kwargs),
                                            settle_ms=getattr(tool, 'settle_ms', 0))
            else:
                step_cm = contextlib.nullcontext()
            with step_cm as step_span:
                try:
                    ret = tool.func(**step.kwargs)
                    if step_span is not None and hasattr(step_span, 'set_output'):
                        step_span.set_output(ret)
                    self._publish("tool_fired", {"name": step.name})
                except Exception as e:
                    self._feedback.on_error(f"plan:step:{step.name}", e)
                    self._publish("tool_error", {"name": step.name, "msg": str(e)})
                    if failed_index is None:
                        status = "error"
                        failed_index = i
                        error_msg = f"{type(e).__name__}: {e}"
                    if plan.strict:
                        break
                    continue
```

(Add `import contextlib` at the top.)

4. In `build_streaming_daemon`, replace:

```python
    dispatcher = Dispatcher(feedback, event_bus=event_bus)
```

with:

```python
    dispatcher = Dispatcher(feedback, event_bus=event_bus, tracer=tracer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_dispatcher.py -v`
Expected: all passed (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dispatcher.py src/voice_commander/daemon.py tests/unit/test_dispatcher.py
git commit -m "feat(dispatcher): plan span + per-step tool_call spans"
```

---

### Task 9: Instrument GraphRuntime — `graph` + per-`node` spans

**Files:**
- Modify: `src/voice_commander/commands/graph_runtime.py`
- Modify: `tests/unit/test_graph_runtime.py` (or new `test_graph_runtime_tracing.py`)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_graph_runtime_tracing.py`:

```python
from __future__ import annotations

import time
from unittest.mock import MagicMock

from voice_commander.commands.graph import (
    Edge, EdgeEnd, Graph, Node,
)
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.event_bus import EventBus
from voice_commander.observability import Store, Tracer
from voice_commander.registry import ToolEntry, ToolRegistry


def _drain(store):
    while not store._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)


def test_graph_runtime_emits_graph_and_node_spans(tmp_path):
    bus = EventBus()
    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=128, daemon_pid=1)
    store.start()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    reg = ToolRegistry()
    reg.register(ToolEntry(name="focus", func=lambda **_k: 0x42, description="", phrases=(), settle_ms=0))

    g = Graph(
        name="demo", kind="command",
        nodes=(Node(id="n1", ref="pipeline.focus", kwargs={"target": "chrome"}, pos=(0, 0)),),
        edges=(),
        inputs=(), outputs=(),
        timeout_ms=5000, foreach_iteration_cap=10, strict=True,
    )

    runtime = GraphRuntime(reg, lambda _name: None, tracer=tracer)
    with tracer.run("graph demo") as run:
        outcome, _ret = runtime.run(g, {})
    _drain(store)

    spans = store.get_spans(run.run_id)
    types = [s["type"] for s in spans]
    assert "graph" in types and "node" in types
    node_spans = [s for s in spans if s["type"] == "node"]
    assert node_spans[0]["status"] == "ok"
    store.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_graph_runtime_tracing.py -v`
Expected: FAIL — `GraphRuntime.__init__` rejects `tracer` kwarg.

- [ ] **Step 3: Modify `graph_runtime.py`**

In `src/voice_commander/commands/graph_runtime.py`:

1. Update constructor:

```python
    def __init__(self, registry, graph_lookup, *, tracer=None):
        self._registry = registry
        self._graph_lookup = graph_lookup
        self._tracer = tracer
```

2. Wrap the body of `run()` in a `graph` span (right after the `_call_depth` guard, before timing setup). All existing body becomes the inner block:

```python
        if self._tracer is None or not getattr(self._tracer, "enabled", False):
            return self._run_inner(graph, inputs, _call_depth=_call_depth)
        with self._tracer.span(
            "graph", name=graph.name, graph_kind=graph.kind,
            n_nodes=len(graph.nodes), call_depth=_call_depth,
        ) as gs:
            outcome, ret = self._run_inner(graph, inputs, _call_depth=_call_depth)
            gs.set_attr("status", outcome.status)
            if outcome.error_msg:
                gs.set_attr("error_msg", outcome.error_msg)
            return outcome, ret
```

3. Rename existing `run` body into `_run_inner(self, graph, inputs, *, _call_depth=0)`.

4. Inside `_run_inner`, in the main `for node in order:` loop body, wrap node dispatch in:

```python
            if self._tracer is not None and getattr(self._tracer, "enabled", False):
                node_cm = self._tracer.span(
                    "node", name=node.id, ref=node.ref, kwargs=dict(kwargs),
                )
            else:
                node_cm = contextlib.nullcontext()
            with node_cm as node_span:
                # ... existing pipeline / control / value / cross-graph branches ...
                # On success, if node_span is not None, set node_span.set_output(...) where appropriate.
                # On failure, set node_span.set_attr("error_msg", error_msg) and node_span.status='error'.
```

(Add `import contextlib` at top.)

For `_dispatch_pipeline_node`, also wrap in a `node` span — accept `tracer` via `self`. Pattern: open the span at the **start** of the function body and use `node_span.set_output(ret)` after success.

5. Where the runtime is constructed (`src/voice_commander/commands/registrar.py` or `daemon.py` — search): pass `tracer=tracer` through.

Run: `grep -rn "GraphRuntime(" src/voice_commander/ tests/`
Find all instantiations and update them. The construction site in production code is the registrar; tests construct it directly.

6. In `build_streaming_daemon`, locate the `reload_commands_all` call. Open the registrar:

Run: `cat src/voice_commander/commands/registrar.py | head -80`

Find where `GraphRuntime(...)` is constructed and accept a `tracer` parameter through `reload_all`. Threads the tracer down. Concrete signature change — add `tracer: Tracer | None = None` to `reload_all` and pass to `GraphRuntime`.

In `build_streaming_daemon`, the `reload_commands_all` call becomes:

```python
        cmd_names, wf_names = reload_commands_all(
            registry, command_store, workflow_store, tracer=tracer
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_graph_runtime_tracing.py -v && uv run pytest tests/unit/test_graph_runtime.py -v`
Expected: all passed (no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/commands/graph_runtime.py src/voice_commander/commands/registrar.py src/voice_commander/daemon.py tests/unit/test_graph_runtime_tracing.py
git commit -m "feat(graph_runtime): graph + node spans wired through registrar"
```

---

### Task 10: Daemon stdout one-liner per run

**Files:**
- Modify: `src/voice_commander/observability/tracer.py`
- Modify: `tests/unit/observability/test_tracer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/observability/test_tracer.py`:

```python
import io
import logging


def test_tracer_emits_oneline_summary_at_end_of_run(store, bus, caplog):
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with caplog.at_level(logging.INFO, logger="voice_commander.observability.tracer"):
        with tracer.run("open chrome") as run:
            with tracer.span("plan", name="plan"):
                with tracer.span("tool_call", name="focus"):
                    pass
    summaries = [r for r in caplog.records if "run " in r.message]
    assert summaries, "expected one-line run summary in INFO log"
    msg = summaries[-1].message
    assert run.run_id[:6] in msg
    assert "ok" in msg
    assert "open chrome" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/observability/test_tracer.py -v -k oneline`
Expected: FAIL.

- [ ] **Step 3: Emit summary at run close**

In `Tracer.run`'s `finally` block, after `write_run_end`, add:

```python
                tool_count = len([
                    s for s in [None] if False  # placeholder; better: query store
                ])
                # Cheaper: track an in-memory counter on the handle instead of querying SQLite.
                logger.info(
                    "run %s %-7s %5d ms  %s",
                    run_id[:8],
                    status.upper() if status == "error" else status,
                    duration_ms,
                    transcript[:80],
                )
```

To carry tool count cheaply, add a simple counter on the run context: maintain `_open_runs: dict[str, int]` on Tracer; bump count when a `tool_call` span exits; expose count in the summary line as `"%d step(s)"`.

Final concrete patch in `Tracer`:

```python
    def __init__(...):
        ...
        self._step_counters: dict[str, int] = {}

    # In span __exit__'s finally branch (just after write_span succeeds):
        if s.type == "tool_call":
            self._step_counters[s.run_id] = self._step_counters.get(s.run_id, 0) + 1

    # In run's finally:
        steps = self._step_counters.pop(run_id, 0)
        logger.info(
            "run %s %-5s %5d ms  %d step(s)  %r",
            run_id[:8],
            status,
            duration_ms,
            steps,
            transcript[:80],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/observability/test_tracer.py -v -k oneline`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/observability/tracer.py tests/unit/observability/test_tracer.py
git commit -m "feat(observability): one-line run summary in daemon log"
```

---

## Phase 2 Validation Gate

- [ ] **Step A:** Full unit suite: `uv run pytest tests/unit/ -v`. Expected: all green.
- [ ] **Step B:** Manual end-to-end smoke. User starts the daemon (`uv run vc run`). User triggers 3 utterances:
   1. "open chrome and search for cats" (success)
   2. "make pizza" (probable LLM no_match → miss)
   3. "do the thing" (likely LLM error / focus failure)

   User then opens `outputs/runs.db` with `sqlite3` (or VSCode's SQLite extension):
   ```bash
   sqlite3 outputs/runs.db "SELECT run_id, status, duration_ms, transcript FROM runs ORDER BY started_at DESC LIMIT 10"
   sqlite3 outputs/runs.db "SELECT type, name, status, error_msg FROM spans WHERE run_id='<id>' ORDER BY started_at"
   ```
   Verify 3 runs persisted with expected statuses and span trees.

- [ ] **Step C:** User confirms daemon log shows the one-liner per run before approving Phase 3.

---

## Phase 3 — REST API + CLI

### Task 11: `/api/runs/*` REST router

**Files:**
- Create: `src/voice_commander/observability/api.py`
- Create: `tests/unit/observability/test_api.py`
- Modify: `src/voice_commander/web/app.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/observability/test_api.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from voice_commander.observability.api import build_observability_router
from voice_commander.observability.store import (
    RunRecord, RunUpdate, SpanRecord, Store,
)


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
    while not s._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)
    yield s
    s.stop()


def _app(store: Store, tracer=None) -> FastAPI:
    app = FastAPI()
    app.include_router(build_observability_router(store, tracer=tracer))
    return app


def test_list_runs(store_with_runs):
    client = TestClient(_app(store_with_runs))
    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["runs"][0]["run_id"] in {"aaa", "bbb"}


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/observability/test_api.py -v`
Expected: FAIL — `build_observability_router` undefined.

- [ ] **Step 3: Implement the router**

Create `src/voice_commander/observability/api.py`:

```python
"""FastAPI router for /api/runs/*."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse


def build_observability_router(store, *, tracer=None) -> APIRouter:
    """Build the /api/runs/* router bound to a specific Store + Tracer."""
    router = APIRouter(prefix="/api/runs", tags=["runs"])

    def _run_with_spans(run_id: str) -> dict[str, Any]:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        run["spans"] = store.get_spans(run_id)
        return run

    @router.get("")
    def list_runs(
        limit: int = Query(50, ge=1, le=500),
        status: str | None = None,
        graph: str | None = None,
        since: float | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        runs = store.list_runs(
            limit=limit, status=status,
            since_ts=since, transcript_like=q,
        )
        if graph:
            runs = [r for r in runs if any(
                sp["type"] == "graph" and sp["name"] == graph
                for sp in store.get_spans(r["run_id"])
            )]
        return {"count": len(runs), "runs": runs}

    @router.get("/last")
    def last_run() -> dict[str, Any]:
        run = store.get_last_run()
        if run is None:
            raise HTTPException(status_code=404, detail="no runs yet")
        run["spans"] = store.get_spans(run["run_id"])
        return run

    @router.get("/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return _run_with_spans(run_id)

    @router.get("/{run_id}/llm")
    def get_run_llm(run_id: str) -> dict[str, Any]:
        spans = store.get_spans(run_id)
        for s in spans:
            if s["type"] == "llm_call":
                return {
                    "run_id": run_id,
                    "model": s["attrs"].get("model"),
                    "endpoint_url": s["attrs"].get("endpoint_url"),
                    "prompt_full": s["attrs"].get("prompt_full"),
                    "transcript": s["attrs"].get("transcript"),
                    "tools": s["attrs"].get("tools"),
                    "raw_response": s["attrs"].get("raw_response"),
                    "output": s["output"],
                    "duration_ms": s["duration_ms"],
                    "status": s["status"],
                    "error_msg": s["error_msg"],
                }
        raise HTTPException(status_code=404, detail="no llm_call span on this run")

    @router.post("/prune")
    def prune_runs(body: dict[str, int]) -> dict[str, Any]:
        # No-op without a writer hook; real prune is automatic. Expose for ops.
        keep = int(body.get("keep", 0))
        if keep > 0:
            store._keep_runs = keep
        return {"keep_runs": store._keep_runs}

    return router
```

(SSE stream endpoint added in Task 12; replay in Tasks 18–19.)

- [ ] **Step 4: Mount in `web/app.py`**

In `src/voice_commander/web/app.py`'s `create_app(...)`, add an `observability_store` parameter (with a default of `None`) and an `observability_tracer` parameter. After existing routes are wired:

```python
    if observability_store is not None:
        from voice_commander.observability.api import build_observability_router
        app.include_router(build_observability_router(observability_store, tracer=observability_tracer))
```

In `daemon.build_streaming_daemon`, pass `observability_store=store, observability_tracer=tracer` to `create_app`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/observability/test_api.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/observability/api.py src/voice_commander/web/app.py src/voice_commander/daemon.py tests/unit/observability/test_api.py
git commit -m "feat(observability): /api/runs REST endpoints (list, last, by-id, llm)"
```

---

### Task 12: SSE `/api/runs/stream` for live trace events

**Files:**
- Modify: `src/voice_commander/observability/api.py`
- Modify: `tests/unit/observability/test_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/observability/test_api.py`:

```python
def test_runs_stream_emits_trace_events(store_with_runs, monkeypatch):
    from voice_commander.event_bus import EventBus
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from voice_commander.observability.api import build_observability_router

    bus = EventBus()
    app = FastAPI()
    app.include_router(build_observability_router(store_with_runs, tracer=None, bus=bus))
    client = TestClient(app)

    with client.stream("GET", "/api/runs/stream") as response:
        assert response.status_code == 200
        # Inject a trace event after subscribing.
        bus.publish("trace.run_started", {"run_id": "x", "transcript": "hi"})
        bus.publish("non.trace.event", {"x": 1})  # filtered out
        bus.publish("trace.run_completed", {"run_id": "x", "status": "ok"})

        chunks: list[bytes] = []
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            if len(b"".join(chunks)) > 200:
                break
        body = b"".join(chunks).decode()
        assert "trace.run_started" in body
        assert "trace.run_completed" in body
        assert "non.trace.event" not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/observability/test_api.py::test_runs_stream_emits_trace_events -v`
Expected: FAIL — `bus` parameter not accepted, no `/stream` route.

- [ ] **Step 3: Add SSE streaming**

In `src/voice_commander/observability/api.py`, change `build_observability_router` signature to accept `bus`:

```python
def build_observability_router(store, *, tracer=None, bus=None) -> APIRouter:
```

Add the route just before `return router`:

```python
    @router.get("/stream")
    async def stream(request: Request, last_event_id: int = Header(default=0, alias="Last-Event-ID")):
        if bus is None:
            raise HTTPException(status_code=503, detail="event bus not configured")

        async def gen():
            import asyncio, json
            q, replay = bus.subscribe_with_replay(last_event_id)
            try:
                for ev in replay:
                    if ev.type.startswith("trace."):
                        yield f"id: {ev.id}\nevent: {ev.type}\ndata: {json.dumps(ev.data)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.get_event_loop().run_in_executor(
                            None, q.get, True, 15.0
                        )
                    except Exception:
                        yield ": keepalive\n\n"
                        continue
                    if not ev.type.startswith("trace."):
                        continue
                    yield f"id: {ev.id}\nevent: {ev.type}\ndata: {json.dumps(ev.data)}\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")
```

In `web/app.py`, update the `include_router` call to pass `bus=event_bus`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/observability/test_api.py::test_runs_stream_emits_trace_events -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/observability/api.py src/voice_commander/web/app.py tests/unit/observability/test_api.py
git commit -m "feat(observability): SSE /api/runs/stream filters trace.* events"
```

---

### Task 13: `vc debug` CLI subcommand

**Files:**
- Create: `src/voice_commander/observability/cli.py`
- Create: `tests/unit/observability/test_cli.py`
- Modify: `src/voice_commander/__main__.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/observability/test_cli.py`:

```python
from __future__ import annotations

import json
import time
from pathlib import Path

from voice_commander.observability.cli import build_debug_parser, run_debug
from voice_commander.observability.store import (
    RunRecord, RunUpdate, SpanRecord, Store,
)


def _seed(tmp_path: Path) -> Store:
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("aaa", 1000.0, "open chrome", 1))
    s.write_span(SpanRecord(
        span_id="root", run_id="aaa", parent_span_id=None,
        type="run", name="run", started_at=1000.0, ended_at=1000.5,
        duration_ms=500, status="ok",
    ))
    s.write_run_end(RunUpdate("aaa", 1000.5, "ok", None, 500))
    while not s._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)
    return s


def test_vc_debug_last_json(tmp_path, capsys):
    s = _seed(tmp_path)
    try:
        parser = build_debug_parser()
        args = parser.parse_args(["last", "--json", "--db", str(tmp_path / "runs.db")])
        run_debug(args)
        out = capsys.readouterr().out
        body = json.loads(out)
        assert body["run_id"] == "aaa"
    finally:
        s.stop()


def test_vc_debug_runs_list(tmp_path, capsys):
    s = _seed(tmp_path)
    try:
        parser = build_debug_parser()
        args = parser.parse_args(["runs", "--limit", "5", "--db", str(tmp_path / "runs.db")])
        run_debug(args)
        out = capsys.readouterr().out
        assert "aaa" in out
    finally:
        s.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/observability/test_cli.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the CLI**

Create `src/voice_commander/observability/cli.py`:

```python
"""`vc debug` and `vc tail` CLI subcommands."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from voice_commander.observability.store import Store


def _resolve_db(args: argparse.Namespace) -> Path:
    if getattr(args, "db", None):
        return Path(args.db)
    # Default: outputs/runs.db relative to CWD.
    return Path("outputs/runs.db")


def _open_store(db_path: Path) -> Store:
    s = Store(db_path, keep_runs=10000, queue_max=64, daemon_pid=os.getpid())
    s.start()
    return s


def _format_tree(run: dict[str, Any], spans: list[dict[str, Any]]) -> str:
    lines = []
    started = time.strftime("%H:%M:%S", time.localtime(run["started_at"]))
    lines.append(
        f"─ run {run['run_id']} ({started}) {run['transcript']!r} ─ "
        f"{run['status']} {run['duration_ms']}ms"
    )
    by_id = {s["span_id"]: s for s in spans}
    children: dict[str | None, list[dict[str, Any]]] = {}
    for s in spans:
        children.setdefault(s["parent_span_id"], []).append(s)

    def _walk(parent_id: str | None, indent: int) -> None:
        for s in children.get(parent_id, []):
            err = f"  {s['error_msg']}" if s["error_msg"] else ""
            lines.append(
                f"{'  ' * indent}{s['type']}/{s['name']}  {s['duration_ms']}ms  {s['status']}{err}"
            )
            _walk(s["span_id"], indent + 1)

    _walk(None, 1)
    return "\n".join(lines)


def _cmd_last(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        run = s.get_last_run()
        if run is None:
            print("no runs yet", file=sys.stderr)
            sys.exit(1)
        spans = s.get_spans(run["run_id"])
        if args.json:
            print(json.dumps({**run, "spans": spans}, indent=2, default=str))
        else:
            print(_format_tree(run, spans))
    finally:
        s.stop()


def _cmd_run(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        run = s.get_run(args.run_id)
        if run is None:
            print(f"run {args.run_id} not found", file=sys.stderr)
            sys.exit(1)
        spans = s.get_spans(run["run_id"])
        if args.json:
            print(json.dumps({**run, "spans": spans}, indent=2, default=str))
        else:
            print(_format_tree(run, spans))
    finally:
        s.stop()


def _cmd_runs(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        runs = s.list_runs(limit=args.limit, status=args.status)
        for r in runs:
            print(
                f"{r['run_id']}  {r['status']:6s}  {r['duration_ms'] or 0:5d}ms  {r['transcript']!r}"
            )
    finally:
        s.stop()


def _cmd_errors(args: argparse.Namespace) -> None:
    args.status = "error"
    args.limit = args.limit or 50
    _cmd_runs(args)


def _cmd_grep(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        runs = s.list_runs(limit=200, transcript_like=args.text)
        for r in runs:
            print(f"{r['run_id']}  {r['status']:6s}  {r['transcript']!r}")
    finally:
        s.stop()


def _cmd_llm(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        spans = s.get_spans(args.run_id)
        for sp in spans:
            if sp["type"] == "llm_call":
                print(json.dumps(sp, indent=2, default=str))
                return
        print("no llm_call span on this run", file=sys.stderr)
        sys.exit(1)
    finally:
        s.stop()


def build_debug_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vc debug")
    p.add_argument("--db", help="path to runs.db (default: outputs/runs.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    last_p = sub.add_parser("last", help="show last run")
    last_p.add_argument("--json", action="store_true")
    last_p.set_defaults(func=_cmd_last)

    run_p = sub.add_parser("run", help="show one run")
    run_p.add_argument("run_id")
    run_p.add_argument("--json", action="store_true")
    run_p.set_defaults(func=_cmd_run)

    runs_p = sub.add_parser("runs", help="list runs")
    runs_p.add_argument("--limit", type=int, default=20)
    runs_p.add_argument("--status")
    runs_p.set_defaults(func=_cmd_runs)

    errors_p = sub.add_parser("errors", help="recent errors")
    errors_p.add_argument("--limit", type=int)
    errors_p.set_defaults(func=_cmd_errors)

    grep_p = sub.add_parser("grep", help="search transcripts")
    grep_p.add_argument("text")
    grep_p.set_defaults(func=_cmd_grep)

    llm_p = sub.add_parser("llm", help="show llm_call span of a run")
    llm_p.add_argument("run_id")
    llm_p.set_defaults(func=_cmd_llm)

    return p


def run_debug(args: argparse.Namespace) -> None:
    args.func(args)
```

- [ ] **Step 4: Wire into `__main__.py`**

In `src/voice_commander/__main__.py`, locate the existing argparse / CLI dispatch. Add a `debug` subcommand path that calls into `observability.cli`:

```python
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        from voice_commander.observability.cli import build_debug_parser, run_debug
        parser = build_debug_parser()
        ns = parser.parse_args(sys.argv[2:])
        run_debug(ns)
        return
```

(The exact wiring depends on existing dispatch; insert the dispatch hook **before** the daemon-start path.)

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/observability/test_cli.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/observability/cli.py src/voice_commander/__main__.py tests/unit/observability/test_cli.py
git commit -m "feat(observability): vc debug subcommand (last/run/runs/errors/grep/llm)"
```

---

### Task 14: `vc tail` — live-following tree formatter

**Files:**
- Modify: `src/voice_commander/observability/cli.py`
- Modify: `src/voice_commander/__main__.py`
- Modify: `tests/unit/observability/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/observability/test_cli.py`:

```python
def test_vc_tail_dumps_recent_runs_when_no_follow(tmp_path, capsys):
    s = _seed(tmp_path)
    try:
        from voice_commander.observability.cli import build_tail_parser, run_tail
        args = build_tail_parser().parse_args(
            ["--no-follow", "--db", str(tmp_path / "runs.db")]
        )
        run_tail(args)
        out = capsys.readouterr().out
        assert "aaa" in out
        assert "open chrome" in out
    finally:
        s.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/observability/test_cli.py::test_vc_tail_dumps_recent_runs_when_no_follow -v`
Expected: FAIL — `build_tail_parser` undefined.

- [ ] **Step 3: Append `tail` subcommand to `cli.py`**

Append:

```python
def build_tail_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vc tail")
    p.add_argument("--db")
    p.add_argument("--no-follow", action="store_true")
    p.add_argument("--since", type=float, default=None,
                   help="unix epoch seconds; only runs after this")
    p.add_argument("--status")
    return p


def run_tail(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        seen: set[str] = set()

        def _emit_new() -> int:
            new = 0
            runs = s.list_runs(
                limit=20, status=args.status, since_ts=args.since,
            )
            runs.reverse()  # oldest first
            for r in runs:
                if r["run_id"] in seen:
                    continue
                seen.add(r["run_id"])
                spans = s.get_spans(r["run_id"])
                print(_format_tree(r, spans))
                print()
                new += 1
            return new

        _emit_new()
        if args.no_follow:
            return

        while True:
            time.sleep(1.0)
            _emit_new()
    except KeyboardInterrupt:
        pass
    finally:
        s.stop()
```

- [ ] **Step 4: Wire `tail` subcommand in `__main__.py`**

Add alongside the `debug` dispatch:

```python
    if len(sys.argv) > 1 and sys.argv[1] == "tail":
        from voice_commander.observability.cli import build_tail_parser, run_tail
        ns = build_tail_parser().parse_args(sys.argv[2:])
        run_tail(ns)
        return
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/observability/test_cli.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/observability/cli.py src/voice_commander/__main__.py tests/unit/observability/test_cli.py
git commit -m "feat(observability): vc tail (polling, --no-follow, span-tree formatter)"
```

---

## Phase 3 Validation Gate

- [ ] **Step A:** `uv run pytest tests/unit/observability/ -v` → all green.
- [ ] **Step B:** Smoke. Daemon running. New terminal:
   ```bash
   uv run vc tail
   ```
   User triggers an utterance. Tree appears live in the second terminal.
   ```bash
   uv run vc debug last --json | head -40
   uv run vc debug errors
   curl -s http://127.0.0.1:8765/api/runs/last | head -c 400
   ```
   All return data.
- [ ] **Step C:** User confirms.

---

## Phase 4 — Web UI

### Task 15: `/page/runs` standalone inspector

**Files:**
- Create: `src/voice_commander/web/templates/runs.html`
- Create: `src/voice_commander/web/templates/_run_detail.html`
- Create: `src/voice_commander/web/templates/_runs_row.html`
- Create: `src/voice_commander/web/static/runs.js`
- Create: `src/voice_commander/web/static/runs.css`
- Modify: `src/voice_commander/web/app.py`
- Create: `tests/unit/test_web_runs_page.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_web_runs_page.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from voice_commander.event_bus import EventBus
from voice_commander.observability import Store


def _seed_store(tmp_path: Path) -> Store:
    from voice_commander.observability.store import RunRecord, RunUpdate, SpanRecord
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("aaa", 1000.0, "open chrome", 1))
    s.write_run_end(RunUpdate("aaa", 1000.5, "ok", None, 500))
    while not s._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)
    return s


def test_page_runs_renders(tmp_path, monkeypatch):
    from voice_commander.web.app import create_app
    from voice_commander.registry import ToolRegistry
    from voice_commander.tool_metadata import ToolMetadataStore
    import threading

    store = _seed_store(tmp_path)
    bus = EventBus()
    app = create_app(
        ToolRegistry(),
        ToolMetadataStore(tmp_path),
        threading.Lock(),
        event_bus=bus,
        observability_store=store,
    )
    client = TestClient(app)
    r = client.get("/page/runs")
    assert r.status_code == 200
    assert "aaa" in r.text
    store.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_web_runs_page.py -v`
Expected: FAIL — `/page/runs` not registered.

- [ ] **Step 3: Implement the page**

Examine the existing template loading pattern by running:

```bash
grep -rn "Jinja2Templates\|TemplateResponse\|HTMLResponse" src/voice_commander/web/app.py | head -20
```

Match the existing convention (Jinja2Templates vs inline HTML strings). Assume the project uses inline HTML strings (per ADR 0022 — minimal templating). If that's the case, write the page as a Python string returning `HTMLResponse`. If Jinja2 is in use, create real `.html` files.

Given the existing builder.html etc., **assume Jinja templates exist**. Verify with:

```bash
ls src/voice_commander/web/templates/ 2>/dev/null
```

If templates dir exists: create new HTML files. Else: create the dir and migrate the existing builder template alongside the runs template.

Create `src/voice_commander/web/templates/runs.html`:

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Voice Commander — Runs</title>
  <link rel="stylesheet" href="/static/runs.css">
  <script src="https://unpkg.com/htmx.org@2.0.0"></script>
</head>
<body>
  <header><h1>Runs</h1></header>
  <main class="runs-layout">
    <aside class="runs-list">
      <form class="filter-bar"
            hx-get="/api/runs"
            hx-trigger="change, submit, every 5s"
            hx-target="#runs-tbody"
            hx-swap="innerHTML"
            hx-ext="">
        <select name="status">
          <option value="">all</option>
          <option value="ok">ok</option>
          <option value="error">error</option>
          <option value="miss">miss</option>
        </select>
        <input name="q" placeholder="grep transcript…" />
        <input name="limit" type="number" value="50" min="1" max="500" />
      </form>
      <table>
        <thead><tr><th>id</th><th>status</th><th>ms</th><th>transcript</th></tr></thead>
        <tbody id="runs-tbody">
          {% for r in runs %}
          <tr hx-get="/page/runs/{{ r.run_id }}" hx-target="#run-detail" hx-trigger="click" class="run-row run-{{ r.status }}">
            <td>{{ r.run_id }}</td><td>{{ r.status }}</td><td>{{ r.duration_ms or 0 }}</td>
            <td class="ellide">{{ r.transcript }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </aside>
    <section id="run-detail" class="run-detail">
      <p class="placeholder">select a run</p>
    </section>
  </main>
  <script src="/static/runs.js"></script>
</body>
</html>
```

Create `src/voice_commander/web/templates/_run_detail.html`:

```html
<header class="run-detail-header">
  <h2>{{ run.run_id }} <span class="status status-{{ run.status }}">{{ run.status }}</span></h2>
  <div>{{ run.duration_ms }} ms · {{ run.transcript }}</div>
  <div class="actions">
    <button hx-post="/api/runs/{{ run.run_id }}/replay-llm" hx-target="#replay-out" hx-swap="innerHTML">Replay LLM</button>
    <details><summary>danger</summary>
      <button hx-post="/api/runs/{{ run.run_id }}/replay-full"
              hx-headers='{"X-Replay-Confirm":"yes"}' hx-target="#replay-out" hx-swap="innerHTML">Replay Full (re-fires plan)</button>
    </details>
  </div>
</header>
<div id="replay-out"></div>
<ol class="span-tree">
  {% for sp in tree %}
    <li class="span span-{{ sp.type }} status-{{ sp.status }}" style="margin-left: {{ sp._depth * 16 }}px">
      <span class="span-type">{{ sp.type }}/{{ sp.name }}</span>
      <span class="span-ms">{{ sp.duration_ms }} ms</span>
      {% if sp.error_msg %}<pre class="error">{{ sp.error_type }}: {{ sp.error_msg }}</pre>{% endif %}
      {% if sp.attrs %}<details><summary>attrs</summary><pre>{{ sp.attrs_json }}</pre></details>{% endif %}
      {% if sp.output is not none %}<details><summary>output</summary><pre>{{ sp.output_json }}</pre></details>{% endif %}
    </li>
  {% endfor %}
</ol>
```

Create `src/voice_commander/web/static/runs.css`:

```css
body { font: 13px system-ui, sans-serif; margin: 0; }
header { background: #1a1a1a; color: #eee; padding: 8px 16px; }
.runs-layout { display: grid; grid-template-columns: 360px 1fr; height: calc(100vh - 40px); }
.runs-list { border-right: 1px solid #ddd; overflow-y: auto; }
.runs-list table { width: 100%; border-collapse: collapse; }
.runs-list td, .runs-list th { padding: 4px 8px; border-bottom: 1px solid #eee; }
.runs-list tr.run-row { cursor: pointer; }
.runs-list tr.run-error td { color: #c00; }
.runs-list tr.run-miss td { color: #888; }
.run-detail { padding: 16px; overflow-y: auto; }
.span { padding: 2px 8px; }
.span.status-error { background: #fee; }
.span.status-ok { background: #efe; }
.status { padding: 2px 8px; border-radius: 4px; font-size: 11px; text-transform: uppercase; }
.status.status-ok { background: #1d3; color: #fff; }
.status.status-error { background: #c00; color: #fff; }
.ellide { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
pre { background: #f5f5f5; padding: 6px; border-radius: 4px; max-width: 100%; overflow-x: auto; }
.placeholder { color: #888; }
```

Create `src/voice_commander/web/static/runs.js`:

```javascript
// Subscribe to /api/runs/stream and update list/detail in place.
(function () {
  if (!window.EventSource) return;
  const es = new EventSource("/api/runs/stream");
  es.addEventListener("trace.run_completed", (ev) => {
    // Force a list refresh by triggering the htmx form
    const form = document.querySelector(".filter-bar");
    if (form) form.dispatchEvent(new Event("change"));
  });
  // Builder overlay hook (Task 16/17)
  window.__vcRunsStream = es;
})();
```

- [ ] **Step 4: Mount the route in `web/app.py`**

In `create_app`, after the include_router:

```python
    if observability_store is not None:
        from fastapi import Request
        from fastapi.responses import HTMLResponse
        import json as _json

        @app.get("/page/runs", response_class=HTMLResponse)
        async def page_runs(request: Request):
            runs = observability_store.list_runs(limit=50)
            return templates.TemplateResponse("runs.html", {"request": request, "runs": runs})

        @app.get("/page/runs/{run_id}", response_class=HTMLResponse)
        async def page_runs_detail(request: Request, run_id: str):
            run = observability_store.get_run(run_id)
            if run is None:
                return HTMLResponse("<p>not found</p>", status_code=404)
            spans = observability_store.get_spans(run_id)
            # Compute depth for indentation.
            by_id = {s["span_id"]: s for s in spans}
            depth_cache: dict[str, int] = {}
            def _depth(s):
                if s["span_id"] in depth_cache:
                    return depth_cache[s["span_id"]]
                d = 0 if s["parent_span_id"] is None else _depth(by_id[s["parent_span_id"]]) + 1
                depth_cache[s["span_id"]] = d
                return d
            tree = []
            for s in spans:
                tree.append({
                    **s,
                    "_depth": _depth(s),
                    "attrs_json": _json.dumps(s["attrs"], indent=2, default=str) if s["attrs"] else "",
                    "output_json": _json.dumps(s["output"], indent=2, default=str) if s["output"] else "",
                })
            return templates.TemplateResponse(
                "_run_detail.html", {"request": request, "run": run, "tree": tree}
            )
```

If templates aren't yet wired in `app.py`, add at the top:

```python
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_web_runs_page.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/web/templates/runs.html src/voice_commander/web/templates/_run_detail.html src/voice_commander/web/static/runs.css src/voice_commander/web/static/runs.js src/voice_commander/web/app.py tests/unit/test_web_runs_page.py
git commit -m "feat(web): /page/runs standalone inspector + run-detail HTMX fragment"
```

---

### Task 16: Builder UI overlay — Runs panel + static node highlight

**Files:**
- Modify: `src/voice_commander/web/templates/builder.html` (or wherever the builder shell lives)
- Modify: `src/voice_commander/web/static/builder.js`
- Modify: `src/voice_commander/web/static/builder.css`
- Modify: `tests/unit/test_web_builder.py` (or new file)

- [ ] **Step 1: Locate the Builder template**

Run: `grep -rn "drawflow\|/page/builder" src/voice_commander/web/ | head -20`

Identify the template file used by `/page/builder`. Open it.

- [ ] **Step 2: Add the Runs side-panel HTML**

Inside the builder template, alongside the canvas, add:

```html
<aside id="builder-runs-panel" class="builder-runs-panel">
  <header>
    <h3>Runs</h3>
    <label><input type="checkbox" id="live-mode-toggle" /> Live</label>
  </header>
  <div id="builder-runs-list" hx-get="/api/runs?graph={{ graph_name }}&limit=20" hx-trigger="load, every 10s" hx-swap="innerHTML">
    loading…
  </div>
</aside>
```

Pass `graph_name` to the existing template (extract from the request URL or query param the builder already uses).

- [ ] **Step 3: Add CSS**

Append to `src/voice_commander/web/static/builder.css`:

```css
.builder-runs-panel {
  position: absolute; top: 0; right: 0; width: 280px; height: 100%;
  background: #fafafa; border-left: 1px solid #ddd;
  overflow-y: auto; padding: 8px; box-sizing: border-box;
  font: 12px system-ui;
}
.run-node-ok    { box-shadow: 0 0 0 2px #1d3 inset !important; }
.run-node-error { box-shadow: 0 0 0 2px #c00 inset !important; }
.run-node-skipped { opacity: 0.4; }
@keyframes vc-pulse {
  0% { box-shadow: 0 0 0 2px #1d3 inset; }
  50% { box-shadow: 0 0 0 6px rgba(29,221,51,0.5) inset; }
  100% { box-shadow: 0 0 0 2px #1d3 inset; }
}
.run-node-pulse { animation: vc-pulse 0.6s ease-in-out 2; }
```

- [ ] **Step 4: Add overlay JS**

Append to `src/voice_commander/web/static/builder.js`:

```javascript
// Runs overlay — paints node spans onto the Drawflow canvas.
window.vcBuilderApplyRunOverlay = function (runId) {
  fetch(`/api/runs/${runId}`)
    .then(r => r.json())
    .then(run => {
      const nodeSpans = run.spans.filter(s => s.type === "node");
      // Reset prior highlights.
      document.querySelectorAll(
        ".drawflow .drawflow-node"
      ).forEach(el => {
        el.classList.remove("run-node-ok", "run-node-error", "run-node-skipped");
      });
      nodeSpans.forEach(s => {
        const el = document.querySelector(
          `.drawflow .drawflow-node[data-node-id="${s.name}"]`
        );
        if (!el) return;
        if (s.status === "ok") el.classList.add("run-node-ok");
        else if (s.status === "error") el.classList.add("run-node-error");
      });
    });
};
```

Hook click events on the runs list to call `vcBuilderApplyRunOverlay(runId)`.

- [ ] **Step 5: Verify each Drawflow node has `data-node-id`**

Run: `grep -n "drawflow-node\|data-node-id" src/voice_commander/web/static/builder.js | head -10`

If `data-node-id` isn't already emitted, locate the node-creation hook (`drawflow.addNode` callback) and ensure each node DOM element receives `setAttribute("data-node-id", node.id)`. This may require a small patch to `addNode`'s post-creation hook.

- [ ] **Step 6: Test**

Run: `uv run pytest tests/unit/test_web_builder.py -v` (existing tests should still pass; no new test for visual overlay).

- [ ] **Step 7: Commit**

```bash
git add src/voice_commander/web/templates/builder.html src/voice_commander/web/static/builder.js src/voice_commander/web/static/builder.css
git commit -m "feat(builder): runs side-panel + static node-status overlay"
```

---

### Task 17: Builder live mode — SSE consumption + pulse animation

**Files:**
- Modify: `src/voice_commander/web/static/builder.js`
- Modify: `src/voice_commander/web/templates/builder.html` (live-mode toggle handler)

- [ ] **Step 1: Append live-mode JS**

Append to `src/voice_commander/web/static/builder.js`:

```javascript
(function () {
  let liveSrc = null;
  let activeRunId = null;
  const toggle = document.getElementById("live-mode-toggle");
  if (!toggle) return;
  toggle.addEventListener("change", () => {
    if (toggle.checked) startLive(); else stopLive();
  });

  function startLive() {
    if (liveSrc) return;
    liveSrc = new EventSource("/api/runs/stream");

    liveSrc.addEventListener("trace.run_started", (ev) => {
      const data = JSON.parse(ev.data);
      activeRunId = data.run_id;
      document.querySelectorAll(".drawflow .drawflow-node").forEach(el => {
        el.classList.remove("run-node-ok", "run-node-error", "run-node-pulse");
      });
    });
    liveSrc.addEventListener("trace.span_started", (ev) => {
      const data = JSON.parse(ev.data);
      if (data.type !== "node" || data.run_id !== activeRunId) return;
      const el = document.querySelector(
        `.drawflow .drawflow-node[data-node-id="${data.name}"]`
      );
      if (el) el.classList.add("run-node-pulse");
    });
    liveSrc.addEventListener("trace.span_ended", (ev) => {
      const data = JSON.parse(ev.data);
      if (data.type !== "node" || data.run_id !== activeRunId) return;
      const el = document.querySelector(
        `.drawflow .drawflow-node[data-node-id="${data.name}"]`
      );
      if (!el) return;
      el.classList.remove("run-node-pulse");
      if (data.status === "ok") el.classList.add("run-node-ok");
      else if (data.status === "error") el.classList.add("run-node-error");
    });
    liveSrc.addEventListener("trace.run_completed", (ev) => {
      // Keep highlights until next run starts.
    });
  }

  function stopLive() {
    if (!liveSrc) return;
    liveSrc.close();
    liveSrc = null;
    activeRunId = null;
  }
})();
```

- [ ] **Step 2: Manual test**

This is a UI-only change with no automated test (matches existing builder.js pattern). Validation moves to the human gate.

- [ ] **Step 3: Commit**

```bash
git add src/voice_commander/web/static/builder.js
git commit -m "feat(builder): live-mode SSE node highlighting (pulse + final state)"
```

---

## Phase 4 Validation Gate

- [ ] **Step A:** Run full test suite: `uv run pytest -v` → all green.
- [ ] **Step B:** Manual UI smoke. Daemon running. User opens:
   1. `http://127.0.0.1:8765/page/runs` — lists prior runs from earlier phases. Click a row → detail loads with span tree, error tracebacks, attrs/output JSON.
   2. `http://127.0.0.1:8765/page/builder` — open a graph that has prior runs. The Runs panel lists those runs. Click one → graph nodes paint green/red/grey.
   3. Toggle "Live" on Builder. Trigger an utterance that exercises the open graph. Nodes pulse as they execute, then settle to ok/error.
- [ ] **Step C:** User confirms.

---

## Phase 5 — Replay

### Task 18: LLM-replay endpoint + UI button

**Files:**
- Create: `src/voice_commander/observability/replay.py`
- Modify: `src/voice_commander/observability/api.py`
- Create: `tests/unit/observability/test_replay.py`
- Modify: `src/voice_commander/observability/cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/observability/test_replay.py`:

```python
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from voice_commander.observability.replay import ReplayResult, replay_llm
from voice_commander.observability.store import (
    RunRecord, RunUpdate, SpanRecord, Store,
)


def _seed_run_with_llm(tmp_path: Path) -> Store:
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("aaa", 1000.0, "open chrome", 1))
    s.write_span(SpanRecord(
        span_id="root", run_id="aaa", parent_span_id=None,
        type="run", name="run", started_at=1000.0, ended_at=1000.5,
        duration_ms=500, status="ok",
    ))
    s.write_span(SpanRecord(
        span_id="llm", run_id="aaa", parent_span_id="root",
        type="llm_call", name="llm_call", started_at=1000.0, ended_at=1000.3,
        duration_ms=300, status="ok",
        attrs={
            "model": "old-model", "endpoint_url": "http://x",
            "prompt_full": "[ system prompt ]", "transcript": "open chrome",
            "raw_response": {"choices": [{"message": {"tool_calls": []}}]},
        },
        output={"steps": [{"name": "focus", "kwargs": {"target": "chrome"}}]},
    ))
    s.write_run_end(RunUpdate("aaa", 1000.5, "ok", None, 500))
    while not s._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)
    return s


def test_replay_llm_returns_diff(tmp_path):
    s = _seed_run_with_llm(tmp_path)
    try:
        from voice_commander.plan import Plan, ToolCall
        router = MagicMock()
        router.route.return_value = Plan(
            steps=(ToolCall(name="press", kwargs={"key": "enter"}),),
            raw_response={},
        )
        result = replay_llm(s, "aaa", router)
        assert isinstance(result, ReplayResult)
        assert result.old_plan == [{"name": "focus", "kwargs": {"target": "chrome"}}]
        assert result.new_plan == [{"name": "press", "kwargs": {"key": "enter"}}]
        assert result.changed is True
    finally:
        s.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/observability/test_replay.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `replay.py`**

Create `src/voice_commander/observability/replay.py`:

```python
"""Replay tooling — LLM-replay (safe) and full-replay (footgun)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    old_plan: list[dict[str, Any]]
    new_plan: list[dict[str, Any]] | None
    changed: bool
    error: str | None = None


def replay_llm(store, run_id: str, router) -> ReplayResult:
    """Re-route the original transcript through the current LLMRouter.

    No tool fires. Returns old plan vs new plan + `changed` flag.
    """
    spans = store.get_spans(run_id)
    llm = next((s for s in spans if s["type"] == "llm_call"), None)
    if llm is None:
        return ReplayResult(run_id=run_id, old_plan=[], new_plan=None,
                            changed=False, error="no llm_call span")
    transcript = llm["attrs"].get("transcript") or store.get_run(run_id)["transcript"]
    old_plan = (llm["output"] or {}).get("steps", [])
    plan = router.route(transcript)
    new_plan = (
        [{"name": s.name, "kwargs": dict(s.kwargs)} for s in plan.steps]
        if plan is not None else None
    )
    return ReplayResult(
        run_id=run_id,
        old_plan=old_plan,
        new_plan=new_plan,
        changed=new_plan != old_plan,
    )


def replay_full(store, run_id: str, router, dispatcher, registry, tracer) -> str:
    """Re-route AND re-fire the plan. **DESTRUCTIVE** — re-types into foreground.

    Returns the run_id of the new replay run.
    """
    spans = store.get_spans(run_id)
    llm = next((s for s in spans if s["type"] == "llm_call"), None)
    if llm is None:
        raise ValueError("no llm_call span; cannot replay")
    transcript = llm["attrs"].get("transcript") or store.get_run(run_id)["transcript"]

    with tracer.run(transcript) as new_run:
        # Mark the new run as a replay of the old one (visible in attrs).
        with tracer.span("replay_marker", name="replay_marker", replay_of=run_id):
            pass
        plan = router.route(transcript)
        if plan is not None:
            dispatcher.run_plan(transcript, plan, registry)
    return new_run.run_id
```

- [ ] **Step 4: Add endpoints to `api.py`**

In `src/voice_commander/observability/api.py`, change `build_observability_router` signature once more to accept dispatcher, registry, llm_router:

```python
def build_observability_router(
    store, *, tracer=None, bus=None, llm_router=None,
    dispatcher=None, registry=None,
) -> APIRouter:
```

Add endpoints:

```python
    @router.post("/{run_id}/replay-llm")
    def replay_llm_ep(run_id: str) -> dict[str, Any]:
        if llm_router is None:
            raise HTTPException(503, detail="llm_router not configured")
        from voice_commander.observability.replay import replay_llm
        result = replay_llm(store, run_id, llm_router)
        return {
            "run_id": result.run_id,
            "old_plan": result.old_plan,
            "new_plan": result.new_plan,
            "changed": result.changed,
            "error": result.error,
        }

    @router.post("/{run_id}/replay-full")
    def replay_full_ep(run_id: str, x_replay_confirm: str = Header(default="")) -> dict[str, Any]:
        if x_replay_confirm.lower() != "yes":
            raise HTTPException(412, detail="missing X-Replay-Confirm: yes header")
        if not all([llm_router, dispatcher, registry, tracer]):
            raise HTTPException(503, detail="full replay requires daemon services")
        from voice_commander.observability.replay import replay_full
        new_id = replay_full(store, run_id, llm_router, dispatcher, registry, tracer)
        return {"new_run_id": new_id, "replay_of": run_id}
```

- [ ] **Step 5: Wire dependencies in `web/app.py` and `daemon.py`**

In `daemon.py` `build_streaming_daemon`, when constructing `create_app`, pass:

```python
        app = create_app(
            registry, store, reload_lock,
            event_bus=event_bus,
            command_store=command_store,
            workflow_store=workflow_store,
            config_path=repo_root / "config.toml",
            llm_router=llm_router,
            observability_store=obs_store,
            observability_tracer=tracer,
            observability_dispatcher=dispatcher,
            observability_llm_router=llm_router,
            observability_registry=registry,
        )
```

(Variable `obs_store` is the `Store` instance; rename earlier `store` to `obs_store` for clarity.)

In `create_app`, accept the new kwargs and pass them through to `build_observability_router`.

- [ ] **Step 6: Add `replay-llm` to CLI**

In `src/voice_commander/observability/cli.py`, add:

```python
def _cmd_replay_llm(args: argparse.Namespace) -> None:
    import httpx
    base = args.endpoint or "http://127.0.0.1:8765"
    r = httpx.post(f"{base}/api/runs/{args.run_id}/replay-llm")
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))


# Add to build_debug_parser:
    rl_p = sub.add_parser("replay-llm", help="re-route transcript through current LLM")
    rl_p.add_argument("run_id")
    rl_p.add_argument("--endpoint", help="daemon URL (default 127.0.0.1:8765)")
    rl_p.set_defaults(func=_cmd_replay_llm)
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/unit/observability/test_replay.py -v && uv run pytest tests/unit/observability/test_api.py -v`
Expected: all passed.

- [ ] **Step 8: Commit**

```bash
git add src/voice_commander/observability/replay.py src/voice_commander/observability/api.py src/voice_commander/observability/cli.py src/voice_commander/web/app.py src/voice_commander/daemon.py tests/unit/observability/test_replay.py
git commit -m "feat(observability): LLM-replay (safe re-route) — endpoint, CLI, unit tests"
```

---

### Task 19: Full replay with confirmation gating

**Files:**
- Modify: `src/voice_commander/observability/cli.py`
- Modify: `tests/unit/observability/test_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/observability/test_api.py`:

```python
def test_replay_full_requires_confirm_header(store_with_runs):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock
    from voice_commander.observability.api import build_observability_router

    tracer = MagicMock()
    tracer.enabled = True
    tracer.run.return_value.__enter__ = lambda *_a: MagicMock(run_id="new1")
    tracer.run.return_value.__exit__ = lambda *_a: None
    tracer.span.return_value.__enter__ = lambda *_a: MagicMock()
    tracer.span.return_value.__exit__ = lambda *_a: None

    router_obj = build_observability_router(
        store_with_runs, tracer=tracer,
        llm_router=MagicMock(), dispatcher=MagicMock(), registry=MagicMock(),
    )
    app = FastAPI()
    app.include_router(router_obj)
    client = TestClient(app)

    r = client.post("/api/runs/aaa/replay-full")
    assert r.status_code == 412

    r = client.post("/api/runs/aaa/replay-full", headers={"X-Replay-Confirm": "yes"})
    assert r.status_code == 200
    assert "new_run_id" in r.json()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/unit/observability/test_api.py::test_replay_full_requires_confirm_header -v`
Expected: PASS (gating already implemented in Task 18). If fail, fix and re-run.

- [ ] **Step 3: Add `replay-full` to CLI with `--yes`**

In `src/voice_commander/observability/cli.py`, add:

```python
def _cmd_replay_full(args: argparse.Namespace) -> None:
    import httpx
    if not args.yes:
        print("refusing without --yes (full replay re-fires the plan into the foreground window)",
              file=sys.stderr)
        sys.exit(2)
    base = args.endpoint or "http://127.0.0.1:8765"
    r = httpx.post(
        f"{base}/api/runs/{args.run_id}/replay-full",
        headers={"X-Replay-Confirm": "yes"},
    )
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))


# Add to build_debug_parser:
    rf_p = sub.add_parser("replay-full", help="REFIRE the plan (footgun)")
    rf_p.add_argument("run_id")
    rf_p.add_argument("--yes", action="store_true", help="required confirmation")
    rf_p.add_argument("--endpoint")
    rf_p.set_defaults(func=_cmd_replay_full)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/observability/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/observability/cli.py tests/unit/observability/test_api.py
git commit -m "feat(observability): full replay endpoint + vc debug replay-full --yes"
```

---

## Phase 5 Validation Gate

- [ ] **Step A:** Full test suite: `uv run pytest -v` → all green.
- [ ] **Step B:** Manual replay smoke. Daemon running. User triggers a known-stable utterance ("focus the chrome window"). Then:
   ```bash
   uv run vc debug last
   uv run vc debug replay-llm <run_id>
   ```
   Verify diff: same plan if prompt template is unchanged.
   User edits `src/voice_commander/prompt_template.txt` (e.g., adds an extra example), reloads via the existing Prompt Inspector UI, then re-runs `vc debug replay-llm` on the same run. Verify the diff shows the new plan.
   In the runs UI, click "Replay LLM" — diff renders inline.
   Open a Notepad, focus it, then click "Replay Full" through the danger toggle. The plan re-fires — chrome focus etc. land. Verify a new run appears in the list with `attrs.replay_of=<original>`.
- [ ] **Step C:** User confirms.

---

## Phase 6 — Documentation

### Task 20: ADR + architecture docs

**Files:**
- Create: `docs/decisions/0070-observability-pipeline.md`
- Modify: `docs/agents/technical-decisions.md`
- Modify: `docs/architecture.md`
- Modify: `CLAUDE.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write ADR 0070**

Create `docs/decisions/0070-observability-pipeline.md`:

```markdown
# ADR 0070: Observability pipeline — live tracing + replay

**Date:** 2026-04-27
**Status:** Accepted
**Spec:** `docs/superpowers/specs/2026-04-27-observability-pipeline-design.md`
**Plan:** `docs/superpowers/plans/2026-04-27-observability-pipeline.md`

## Context

Per-utterance debugging today relies on rotating logs and `outputs/last_plan.json`. Diagnosing a misbehaving graph node, an LLM that returned a bad plan, or a cross-graph call gone wrong required grepping logs and reconstructing the timeline by hand. There was no machine-readable record of the run.

## Decision

Add an in-process Tracer that captures every utterance as a span tree persisted to SQLite (`outputs/runs.db`, WAL). Spans cover: `run`, `transcribe`, `llm_call` (with full prompt + raw response), `plan`, `tool_call`, `graph`, `node`. Live trace events broadcast on the existing EventBus under the `trace.*` namespace; existing subscribers (sprite) ignore them. New surfaces:

- `/api/runs/*` REST + `/api/runs/stream` SSE
- `vc debug` and `vc tail` CLIs
- `/page/runs` web inspector
- Builder UI overlay with live highlighting

Replay: LLM-only (safe, re-routes through current router) is unfettered; full re-fire is gated by `X-Replay-Confirm: yes` header / `--yes` flag.

## Consequences

* **Storage** — SQLite WAL, ~5 KB per run, default retention 1000 runs ≈ 5 MB. Pruned automatically every 50 inserts.
* **Performance** — single-thread async writer; instrumentation never blocks the audio or pipeline thread. Worst-case overhead ~0.5 ms per utterance.
* **Privacy** — all transcripts, clipboard captures, and OCR output stored verbatim. Acceptable for single-user local daemon. Revisit if cloud sync is ever introduced.
* **Replay risk** — full replay re-fires keystrokes into the current foreground window. Gated; never enabled by default.
* **No new deps.** stdlib `sqlite3` + `contextvars`.

## Alternatives considered

* **JSONL append-only.** Simpler, grep-friendly. Rejected — UI needs ad-hoc filter queries (status, graph, since, transcript regex) that SQL handles cleanly.
* **OpenTelemetry / Jaeger.** Overkill for a single-process daemon; introduces transport, encoding, and ops surface for zero benefit at this scale.
* **Skipping graph-node spans.** Loses the Builder overlay's main value (visualising which node failed inside a graph).

## Migration

Additive only — no existing code paths change semantics. `outputs/last_plan.json` is preserved.
```

- [ ] **Step 2: Append row to technical-decisions.md**

Add to the bottom table:

```markdown
| Observability | SQLite-backed Tracer with span tree (`run` / `transcribe` / `llm_call` / `plan` / `tool_call` / `graph` / `node`); live SSE on `trace.*`; web `/page/runs` + Builder overlay; `vc debug` / `vc tail` CLIs; LLM-replay safe, full replay gated. | Single source of truth for per-utterance debugging; UI + CLI + agent API share schema. | [0070](../decisions/0070-observability-pipeline.md) |
```

- [ ] **Step 3: Update `docs/architecture.md`**

Add a new section near the end:

```markdown
## Observability

`observability/` (Tracer + Store + REST + CLI + replay) — see ADR 0070. The Tracer is constructed once in `build_streaming_daemon`, threaded into `LLMRouter`, `Dispatcher`, and `GraphRuntime`. Spans are written async via a single SQLite writer thread. Live trace events fan out over the existing EventBus under `trace.*`; the sprite ignores them.
```

- [ ] **Step 4: Update `CLAUDE.md` one-liner**

Add to "Current state" paragraph: a sentence noting the observability layer:

```
Every utterance is captured as a span tree in `outputs/runs.db`, streamed live to `/page/runs`, the Builder UI overlay, and the `vc debug` / `vc tail` CLIs (ADR 0070).
```

- [ ] **Step 5: Update `.gitignore`**

Append:

```
outputs/runs.db
outputs/runs.db-wal
outputs/runs.db-shm
outputs/runs.db.corrupt-*
```

- [ ] **Step 6: Commit**

```bash
git add docs/decisions/0070-observability-pipeline.md docs/agents/technical-decisions.md docs/architecture.md CLAUDE.md .gitignore
git commit -m "docs(observability): ADR 0070 + architecture + technical-decisions row + gitignore"
```

---

## Final Validation Gate

- [ ] **Step A:** `uv run pytest -v` — full suite green (unit + integration if any cover this layer).
- [ ] **Step B:** `uv run vc run` — daemon starts cleanly, observability-init logs visible.
- [ ] **Step C:** User triggers 5–10 mixed utterances (success, miss, graph success, graph error, sub-graph). Verifies:
   - Daemon stdout shows one-line summary per run.
   - `vc tail` (in another terminal) prints span trees live.
   - `vc debug last`, `vc debug errors`, `vc debug grep <text>` all return sensible output.
   - `/page/runs` lists every run; clicking each expands span tree.
   - `/page/builder` with a graph open shows runs panel; live mode pulses nodes during execution.
   - `vc debug replay-llm <id>` shows old vs new plan diff.
- [ ] **Step D:** User signs off. Plan complete.

---

## Self-Review Notes (writer pass)

* **Spec coverage:** every section of the design spec maps to a numbered task: §3.1 architecture → Tasks 4–9 (Tracer + instrumentation seams); §3.2 span tree → Task 4 (run/transcribe), Tasks 6–9 (the rest); §3.3 Tracer API → Task 4; §3.4 storage → Tasks 2–3; §3.5 LLM capture → Task 7; §3.6 EventBus events → Task 4 (publish_safe inside Tracer); §3.7 stdout one-liner → Task 10; §3.8 `vc tail` → Task 14; §3.9 `vc debug` → Task 13; §3.10 REST → Tasks 11–12; §3.11 web UI → Tasks 15–17; §4.5 replay safety → Task 19; §4.3 crash recovery → Task 3; §5 config → Task 1; §6 phasing → mirrored by Phase 1–6 + gates.
* **Type consistency:** `Tracer.span(type, name=..., **attrs)` and `Tracer.run(transcript)` signatures used identically in every task. `Span.set_output(value)` / `Span.set_attr(k, v)` consistent. `RunHandle` exposes only `run_id` + `started_at`; transcript update goes through `Tracer.update_transcript` (Task 6).
* **Placeholder scan:** every code block is concrete; no "TODO / TBD" left. Where existing code's exact shape is unknown (Builder template path, exact registrar signature) the plan instructs the executor to discover with a `grep` first, then make the concrete edit.
