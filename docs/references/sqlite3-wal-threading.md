# SQLite3 WAL Threading Reference for Span/Trace Store

**Sources:**
- [Python 3.11 sqlite3 module](https://docs.python.org/3.11/library/sqlite3.html)
- [SQLite Write-Ahead Logging (WAL)](https://www.sqlite.org/wal.html)
- [SQLite Foreign Key Support](https://www.sqlite.org/foreignkeys.html)
- [SQLite PRAGMA Statements](https://www.sqlite.org/pragma.html)

---

## Overview

This document covers Python's `sqlite3` stdlib module optimized for WAL mode with single-writer / multi-reader concurrency. Target use: building a span/trace store that handles concurrent reads from multiple threads while a dedicated writer thread flushes observations to disk.

**Key principle:** WAL mode + `check_same_thread=False` + manual serialization = safe, performant concurrent access.

---

## 1. Connection Setup

### Basic Pattern

```python
import sqlite3
import threading

def open_connection(db_path: str, for_writing: bool = False) -> sqlite3.Connection:
    """
    Open a database connection for WAL mode.
    
    Args:
        db_path: Path to SQLite database file
        for_writing: If True, enable exclusive locking hints for writer thread
    
    Returns:
        sqlite3.Connection with WAL mode, autocommit, row factory configured
    """
    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,      # Allow use across threads (we serialize manually)
        isolation_level=None,          # Autocommit mode (no implicit transactions)
        timeout=30.0                   # Retry for 30s if locked
    )
    
    # Configure connection
    conn.row_factory = sqlite3.Row     # Enable column-name access
    
    # Enable WAL mode (persistent, survives reconnect)
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Balance safety + performance in WAL mode
    conn.execute("PRAGMA synchronous=NORMAL")
    
    # Foreign keys OFF by default per connection—**MUST be set**
    conn.execute("PRAGMA foreign_keys=ON")
    
    # Auto-checkpoint at 1000 pages (~4MB)
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    
    return conn
```

### Connection Parameters

**`check_same_thread=False`**
- Default is `True`, which raises `ProgrammingError` if the connection is used from a different thread than the one that created it.
- Set to `False` only if you enforce serialization yourself (e.g., lock on every access, or single-writer pattern).
- WAL mode is safe to share across threads when `check_same_thread=False` **AND** your application enforces mutual exclusion.

**`isolation_level=None`**
- Enables autocommit mode: no implicit `BEGIN` before DML statements.
- Each `execute()` immediately commits (unless inside an explicit `WITH conn:` block).
- Cleaner for WAL-based readers and writers; avoids nested transaction confusion.
- Default is `"DEFERRED"` (transaction opens on first DML), which complicates WAL coordination.

**`timeout=30.0`**
- Retry for up to 30 seconds if the database is locked.
- WAL reduces lock contention, but long-running checkpoints or exclusive operations can still block.

---

## 2. WAL Mode Essentials

### Enable WAL

```python
conn.execute("PRAGMA journal_mode=WAL")  # Returns 'wal' on success
```

**Persistence:** WAL mode persists across reconnections. Once enabled, the database remains in WAL mode until explicitly changed back.

**File overhead:** WAL mode creates two sidecar files:
- `<db>-wal` — the actual write-ahead log (appended to on commits)
- `<db>-shm` — shared-memory index for fast WAL page lookups

Both files must move together with the main database file; they are auto-created and auto-deleted when the database is closed cleanly.

### Multi-Reader, Single-Writer Concurrency

WAL inverts the transaction model:
- **Original data** stays in the database file
- **Changes are appended** to the WAL file
- **Readers** query from the database, falling back to the WAL for uncommitted changes
- **Writers** append to the WAL; only one writer at a time (enforced by the lock on the WAL file)
- **Checkpoint** transfers WAL content back to the database file

**Reader safety:** Each transaction remembers the WAL's "end mark" at the time it opened, ensuring a consistent snapshot. Readers do not block writers, and writers do not block readers (except during checkpoint, which is brief and passive by default).

---

## 3. PRAGMA synchronous in WAL Mode

### Recommended: `PRAGMA synchronous=NORMAL`

```python
conn.execute("PRAGMA synchronous=NORMAL")
```

**Behavior:**
- The WAL file is synced to disk before each checkpoint (not after every write).
- The database file is synced after each checkpoint.
- Normal queries never block on fsync().

**Safety trade-off:** You lose durability across power loss with `NORMAL`—if power fails, the last transactions may be lost. **This is acceptable for most applications**, especially span/trace stores where losing the last few events is tolerable.

**Alternative: `PRAGMA synchronous=FULL`** (more durable but slower)
- Additional fsync() after each transaction.
- Ensures durability across power loss.
- Not recommended for WAL unless write throughput is not a bottleneck.

**Do NOT use: `PRAGMA synchronous=OFF`**
- Risks corruption on crash; not recommended.

---

## 4. Foreign Key Enforcement (Critical Gotcha)

### Always Enable Per Connection

```python
conn.execute("PRAGMA foreign_keys=ON")
```

**Critical gotcha:** Foreign keys are **OFF by default on every new connection**, regardless of whether the schema defines them. If you forget this line:
- `ON DELETE CASCADE` silently does nothing.
- `ON UPDATE CASCADE` silently does nothing.
- Orphaned rows accumulate silently.

**Why default to OFF?** SQLite maintains backward compatibility with databases created before foreign key support existed.

**Best practice:**
- Set `PRAGMA foreign_keys=ON` immediately after every `connect()`.
- Enforce this in your connection factory (see example above).

---

## 5. Row Access with `sqlite3.Row`

```python
conn.row_factory = sqlite3.Row

row = conn.execute("SELECT span_id, duration_ms FROM spans WHERE run_id=?", (123,)).fetchone()

# All three access patterns work:
print(row["span_id"])      # Column name access
print(row[0])              # Index access
print(row.keys())          # ['span_id', 'duration_ms']
```

`sqlite3.Row` supports both dict-like (`row["col"]`) and tuple-like (`row[0]`) access. Column names are case-insensitive.

---

## 6. Exception Hierarchy and Corruption Handling

### Key Exceptions

```python
sqlite3.Error
├── InterfaceError (low-level C API misuse)
├── DatabaseError
│   ├── OperationalError (DB operations, usually recoverable)
│   ├── IntegrityError (constraint violations)
│   ├── ProgrammingError (API misuse)
│   └── DataError (data processing errors)
└── Warning
```

### Corrupt Database Recovery

On initial `connect()`, a corrupt database file raises `sqlite3.DatabaseError`. Standard recovery pattern:

```python
import os
import time

def safe_open_or_reset(db_path: str) -> sqlite3.Connection:
    """Open database, or rename corrupt file and create fresh."""
    try:
        return open_connection(db_path)
    except sqlite3.DatabaseError as e:
        # Rename corrupt file with timestamp
        timestamp = int(time.time())
        corrupt_path = f"{db_path}.corrupt-{timestamp}"
        os.rename(db_path, corrupt_path)
        
        # Remove sidecar files too
        for suffix in ["-wal", "-shm"]:
            sidecar = db_path + suffix
            if os.path.exists(sidecar):
                os.remove(sidecar)
        
        # Create fresh database
        return open_connection(db_path)
```

---

## 7. Threading Patterns

### Pattern A: Single Connection, Single Thread (Simplest)

```python
conn = sqlite3.connect("spans.db", check_same_thread=True)  # Default
for row in conn.execute("SELECT * FROM spans LIMIT 10"):
    print(row)
conn.close()
```

Safe by default; no concurrency. Use only for single-threaded apps or CLI tools.

---

### Pattern B: Single Writer Thread, Multiple Reader Connections (Recommended)

This is the recommended pattern for a span/trace store:

```python
import queue
import threading

class SpanStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.write_queue = queue.Queue()  # Enqueue mutations
        self.writer_thread = None
        self.running = False
    
    def start(self):
        """Start the writer thread."""
        self.running = True
        self.writer_thread = threading.Thread(target=self._writer_loop, daemon=False)
        self.writer_thread.start()
    
    def stop(self):
        """Stop writer and close database."""
        self.running = False
        self.write_queue.put(None)  # Signal to exit
        self.writer_thread.join()
    
    def _writer_loop(self):
        """
        Single-threaded writer loop.
        Opens one connection, drains the queue, and commits.
        """
        conn = open_connection(self.db_path, for_writing=True)
        
        try:
            while self.running:
                # Block until item is available or timeout
                try:
                    item = self.write_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                if item is None:  # Stop signal
                    break
                
                # Execute the mutation
                sql, params = item
                conn.execute(sql, params)
                # With isolation_level=None, this auto-commits
        
        finally:
            conn.close()
    
    def write(self, sql: str, params: tuple):
        """Enqueue a write operation (thread-safe)."""
        self.write_queue.put((sql, params))
    
    def read_spans(self, run_id: int) -> list[dict]:
        """
        Read from a fresh, short-lived connection (thread-safe).
        WAL allows concurrent readers without blocking the writer.
        """
        conn = open_connection(self.db_path, for_writing=False)
        try:
            rows = conn.execute(
                "SELECT span_id, name, started_at, duration_ms FROM spans WHERE run_id=? ORDER BY started_at",
                (run_id,)
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

# Usage
store = SpanStore("spans.db")
store.start()

# Many reader threads can call read_spans() concurrently
# Writer thread drains mutations from the queue
store.write("INSERT INTO spans (run_id, name, started_at, duration_ms) VALUES (?, ?, ?, ?)",
            (1, "my_span", "2026-04-27T10:00:00", 150))

store.stop()
```

**Why this works:**
- Writer thread holds one connection, serializes mutations, and commits one at a time.
- Reader threads open short-lived connections on-demand; WAL allows them to read without blocking.
- No global lock on the entire database; WAL's per-page locks minimize contention.

---

### Pattern C: Connection Per Thread (Simple but wastes FDs)

```python
import threading

thread_local = threading.local()

def get_reader_connection() -> sqlite3.Connection:
    """Get or create a per-thread connection."""
    if not hasattr(thread_local, "conn"):
        thread_local.conn = open_connection("spans.db")
    return thread_local.conn

# In reader thread
rows = get_reader_connection().execute("SELECT * FROM spans").fetchall()
```

Simple and safe; each thread has its own connection. Downside: wastes one open file descriptor per reader thread. With WAL, Pattern B (short-lived connections) is preferred.

---

## 8. Transaction Control with Context Manager

```python
conn = open_connection("spans.db")

# Explicit transaction (context manager)
try:
    with conn:
        conn.execute("INSERT INTO spans VALUES (1, 'span1', '2026-04-27', 100)")
        conn.execute("INSERT INTO spans VALUES (2, 'span2', '2026-04-27', 200)")
        # Auto-commits on clean exit
except sqlite3.IntegrityError:
    # Auto-rolls back on exception
    print("Duplicate key, rolled back")
```

With `isolation_level=None` (autocommit mode), the context manager still works but has less effect—each statement commits immediately, and the `WITH` block only defers the final commit. For multi-statement atomicity, use explicit `BEGIN/COMMIT/ROLLBACK`:

```python
conn.execute("BEGIN")
try:
    conn.execute("INSERT INTO spans VALUES (...)")
    conn.execute("INSERT INTO spans VALUES (...)")
    conn.execute("COMMIT")
except:
    conn.execute("ROLLBACK")
```

---

## 9. `executescript()` Gotcha

```python
# WARNING: executescript() auto-commits any pending transaction BEFORE execution
conn = sqlite3.connect(":memory:", isolation_level=None)

# This DOES NOT work as expected:
try:
    with conn:
        conn.execute("CREATE TABLE t(x)")
        conn.executescript("INSERT INTO t VALUES(1); INSERT INTO t VALUES(2);")
        # ^ Implicitly committed here, before both inserts run
        raise Exception("Should roll back")
except:
    pass

# Result: 1 row inserted (from executescript)
#         2nd row also inserted (within executescript)
#         Neither rolled back because executescript committed first
```

**Best practice:** Use `execute()` for all normal operations. Use `executescript()` only for schema creation (DDL), isolated from transactions:

```python
def init_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE TABLE IF NOT EXISTS spans (
            span_id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            duration_ms INTEGER,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );
    """)
```

---

## 10. Schema Migrations

### Idempotent Creation with `IF NOT EXISTS`

```python
def init_schema(conn):
    """Create schema if not already present."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at TEXT
        );
        
        CREATE TABLE IF NOT EXISTS spans (
            span_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            parent_span_id INTEGER,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration_ms REAL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(parent_span_id) REFERENCES spans(span_id) ON DELETE CASCADE
        );
        
        CREATE INDEX IF NOT EXISTS idx_spans_run_id ON spans(run_id);
        CREATE INDEX IF NOT EXISTS idx_spans_started_at ON spans(started_at DESC);
    """)
```

### Schema Versioning with `PRAGMA user_version`

```python
def init_schema(conn):
    """Initialize or migrate schema based on user_version."""
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    
    if current_version == 0:
        # Initial schema creation
        conn.executescript("""
            CREATE TABLE runs (...);
            CREATE TABLE spans (...);
            PRAGMA user_version = 1;
        """)
    elif current_version == 1:
        # Migration from v1 to v2
        conn.execute("ALTER TABLE spans ADD COLUMN metadata TEXT")
        conn.execute("PRAGMA user_version = 2")
```

The `user_version` is stored at offset 60 in the database header and persists across connections. Cheap way to track schema evolution.

---

## 11. Retention and Pruning

### Limit-Keep Pattern with `OFFSET`

Keep only the last N spans, pruning oldest:

```python
def prune_old_spans(conn, max_spans: int = 100000):
    """Delete all but the N most recent spans."""
    conn.execute("""
        DELETE FROM spans WHERE span_id NOT IN (
            SELECT span_id FROM spans
            ORDER BY started_at DESC
            LIMIT -1 OFFSET ?
        )
    """, (max_spans,))
```

**SQLite-specific syntax:** `LIMIT -1 OFFSET N` means "no limit on rows, but skip the first N rows". This keeps the top N rows (ordered DESC) and deletes the rest.

### Prune Entire Runs

```python
def prune_old_runs(conn, max_runs: int = 1000):
    """Delete all but the N most recent runs (and their spans via CASCADE)."""
    conn.execute("""
        DELETE FROM runs WHERE run_id NOT IN (
            SELECT run_id FROM runs
            ORDER BY started_at DESC
            LIMIT -1 OFFSET ?
        )
    """, (max_runs,))
```

Because the `spans` table has `FOREIGN KEY(run_id) REFERENCES runs ON DELETE CASCADE`, deleting a run also deletes all its spans automatically (if `PRAGMA foreign_keys=ON`).

---

## 12. WAL Checkpoint Behavior

### Automatic Checkpoint

WAL automatically checkpoints (merges the WAL back into the database) when:
- The WAL reaches **1000 pages** (~4MB, configurable)
- The last connection closes cleanly

**Checkpoint is non-blocking by default** (PASSIVE mode); readers are not interrupted.

### Manual Checkpoint

```python
# Force a checkpoint
result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
# Returns (busy, pages_written, pages_checkpointed)
```

Checkpoint modes:
- **PASSIVE** — Do non-blocking work; may not complete if readers are active. Rows: (0, N, N) if complete, (K, 0, 0) if readers blocking.
- **FULL** — Tries harder; may wait for readers.
- **RESTART** — Runs to completion; ensures WAL reset.
- **TRUNCATE** — Like RESTART but also truncates the WAL file.

**General rule:** Let auto-checkpoint handle it. Manual checkpoint only if WAL grows unbounded (sign of long-lived read transactions).

### Long-Lived Read Transactions Block Checkpoint

If a reader holds a connection open mid-transaction for extended periods, it prevents checkpoint and WAL grows unbounded. **Solution:** Use short-lived connections and close promptly.

```python
# Good: short-lived connection
def read_spans(run_id):
    conn = open_connection(db_path)
    try:
        return conn.execute("SELECT * FROM spans WHERE run_id=?", (run_id,)).fetchall()
    finally:
        conn.close()  # Close immediately

# Bad: long-lived reader
conn = open_connection(db_path)
for row in conn.execute("SELECT * FROM spans"):
    time.sleep(1)  # Blocks checkpoint while sleeping!
```

---

## 13. Concrete Minimal Store Example

A complete, hold-in-head `SpanStore` class:

```python
import sqlite3
import threading
import queue
from typing import Optional, Dict, List
import time

class SpanStore:
    """Single-writer, multi-reader span store with WAL mode."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.write_queue = queue.Queue()
        self.writer_thread = None
        self.running = False
    
    def start(self):
        """Initialize schema and start writer thread."""
        # Create schema on main thread
        conn = self._open_conn()
        self._init_schema(conn)
        conn.close()
        
        # Start writer
        self.running = True
        self.writer_thread = threading.Thread(target=self._writer_loop, daemon=False)
        self.writer_thread.start()
    
    def stop(self):
        """Stop writer and clean up."""
        self.running = False
        self.write_queue.put(None)
        self.writer_thread.join(timeout=5)
    
    def _open_conn(self) -> sqlite3.Connection:
        """Open connection with WAL mode."""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,
            timeout=30.0
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    
    def _init_schema(self, conn):
        """Create schema if not present."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS spans (
                span_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                duration_ms REAL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_spans_run_id ON spans(run_id);
        """)
    
    def _writer_loop(self):
        """Single writer thread: drains queue and commits."""
        conn = self._open_conn()
        try:
            while self.running:
                try:
                    item = self.write_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                if item is None:
                    break
                
                sql, params = item
                conn.execute(sql, params)
        finally:
            conn.close()
    
    def write_span(self, run_id: int, name: str, duration_ms: float):
        """Enqueue a span write (thread-safe)."""
        self.write_queue.put((
            "INSERT INTO spans (run_id, name, duration_ms) VALUES (?, ?, ?)",
            (run_id, name, duration_ms)
        ))
    
    def get_spans(self, run_id: int) -> List[Dict]:
        """Read spans for a run (thread-safe, short-lived connection)."""
        conn = self._open_conn()
        try:
            rows = conn.execute(
                "SELECT span_id, name, duration_ms FROM spans WHERE run_id=?",
                (run_id,)
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
    
    def prune(self, max_spans: int = 100000):
        """Keep only the N most recent spans."""
        self.write_queue.put((
            """DELETE FROM spans WHERE span_id NOT IN (
                   SELECT span_id FROM spans
                   ORDER BY span_id DESC
                   LIMIT -1 OFFSET ?
               )""",
            (max_spans,)
        ))


# Usage
store = SpanStore("traces.db")
store.start()

# Writer thread (from any thread)
store.write_span(run_id=1, name="task_a", duration_ms=42.5)

# Readers (from any thread, any number of concurrent readers)
spans = store.get_spans(run_id=1)
print(f"Run 1 has {len(spans)} spans")

store.stop()
```

---

## 14. Common Failure Modes and Remedies

| Failure | Cause | Remedy |
|---------|-------|--------|
| `ProgrammingError: sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread` | Shared connection, `check_same_thread=True` (default) | Set `check_same_thread=False` OR use per-thread connections |
| `ON DELETE CASCADE` silently does nothing | Forgot `PRAGMA foreign_keys=ON` | Set immediately after every `connect()` |
| Rows with orphaned foreign keys accumulate | Same as above | Set `PRAGMA foreign_keys=ON` |
| `DatabaseError` on `connect()` | Corrupted database file | Rename `<db>.corrupt-<ts>` and recreate (see section 6) |
| WAL file grows unbounded | Long-lived read transactions block checkpoint | Use short-lived connections; close promptly |
| `OperationalError: database is locked` | Reader holds transaction while writer appends | WAL reduces this; increase `timeout` param if needed |
| Inserting `bytes` or `datetime` objects fails with `TypeError` | Not using parameter binding, or not `str()` serializing | Use `?` placeholders; serialize non-text types to strings |

---

## 15. Reference Configuration Snippet

Recommended one-time setup for span/trace store:

```python
import sqlite3

# Open and configure
conn = sqlite3.connect(
    "spans.db",
    check_same_thread=False,
    isolation_level=None,
    timeout=30.0
)

conn.row_factory = sqlite3.Row

# WAL mode (persistent)
conn.execute("PRAGMA journal_mode=WAL")

# Balance safety and performance
conn.execute("PRAGMA synchronous=NORMAL")

# Auto-checkpoint at 1000 pages
conn.execute("PRAGMA wal_autocheckpoint=1000")

# Foreign key enforcement (required per connection)
conn.execute("PRAGMA foreign_keys=ON")

# Create schema if needed
conn.executescript("""
    CREATE TABLE IF NOT EXISTS runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS spans (
        span_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        duration_ms REAL,
        FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_spans_run_id ON spans(run_id);
""")

conn.close()
```

---

## Pinned Versions

- **Python:** 3.11+ (sqlite3 stdlib module)
- **SQLite Engine:** 3.45+ (recommended for WAL robustness and checkpoint improvements)
- **Reference Date:** 2026-04-27

