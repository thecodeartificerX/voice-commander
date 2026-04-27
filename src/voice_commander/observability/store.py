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
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            backup = self._db_path.with_name(f"{self._db_path.name}.corrupt-{int(time.time())}")
            logger.warning(
                "observability db unreadable (%s); backing up to %s and recreating",
                exc,
                backup,
            )
            self._db_path.rename(backup)
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
        except Exception:
            conn.close()
            raise
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
            logger.info("observability queue full; dropped %d record(s) total", self._dropped)

    # ------------------------------------------------------------------
    # writer thread
    # ------------------------------------------------------------------

    def _writer_loop(self) -> None:
        conn = self._connect()
        try:
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
                        if self._inserts_since_prune >= _PRUNE_EVERY:
                            self._prune(conn)
                            self._inserts_since_prune = 0
                    elif kind == "span":
                        self._insert_span(conn, payload)
                    elif kind == "run_transcript":
                        rid, txt = payload
                        conn.execute(
                            "UPDATE runs SET transcript=? WHERE run_id=?",
                            (txt, rid),
                        )
                    elif kind == "_flush":
                        payload.set()  # payload is threading.Event
                except Exception:
                    logger.exception("observability writer error")
        finally:
            conn.close()

    def _insert_run_start(self, conn: sqlite3.Connection, rec: RunRecord) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(run_id, started_at, transcript, status, daemon_pid, schema_version) "
            "VALUES (?, ?, ?, 'running', ?, 1)",
            (rec.run_id, rec.started_at, rec.transcript, rec.daemon_pid),
        )

    def _insert_run_end(self, conn: sqlite3.Connection, upd: RunUpdate) -> None:
        conn.execute(
            "UPDATE runs SET ended_at=?, status=?, error_msg=?, duration_ms=? WHERE run_id=?",
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

    # ------------------------------------------------------------------
    # readers (any thread; WAL allows concurrent reads)
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def get_spans(self, run_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM spans WHERE run_id=? ORDER BY started_at",
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
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
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def get_last_run(self) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def write_run_transcript_update(self, run_id: str, transcript: str) -> None:
        self._enqueue(("run_transcript", (run_id, transcript)))

    def recover_stale_runs(self) -> int:
        """Mark prior 'running' rows from older daemons as crashed.

        Called once at daemon startup. Returns the number of rows fixed.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE runs SET status='error', error_msg='daemon crash', "
                "ended_at=? WHERE status='running' AND daemon_pid != ?",
                (time.time(), self._daemon_pid),
            )
            return cur.rowcount or 0
        finally:
            conn.close()

    def flush(self, timeout: float = 2.0) -> bool:
        """Block until all currently-queued writes have been committed to SQLite.

        Enqueues a synchronisation sentinel; the writer thread signals it when
        processed. Returns True if flushed within *timeout* seconds, False if
        the writer did not respond in time.
        """
        done = threading.Event()
        self._enqueue(("_flush", done))
        return done.wait(timeout=timeout)


_SENTINEL = object()

# Prune old runs every N run_end writes. Set to 1 so the store
# never exceeds keep_runs + 1 rows; raise if the write cost matters.
_PRUNE_EVERY = 1


def _json_default(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return obj.hex()
    return repr(obj)
