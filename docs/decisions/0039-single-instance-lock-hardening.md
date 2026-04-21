# ADR 0039: Single-Instance Lock Hardening with OS-Level Locking

**Status:** Accepted
**Date:** 2026-04-21

## Context

The original `_pid_alive()` used `ctypes.windll.kernel32.OpenProcess` with the default `restype` of `c_int` (32-bit). On 64-bit Windows, `HANDLE` is a 64-bit pointer. Truncation to 32-bit produced bogus truthy values for dead PIDs, so `_stale()` never reclaimed the lock file after a crash.

The original lock used `O_CREAT | O_EXCL` (exclusive create) with PID written to file. Stale-lock detection relied solely on `_pid_alive()` — which was broken due to the ctypes bug. Several compounding failure modes existed:

1. A daemon crash left the lock file behind with a stale PID. `_pid_alive()` returned truthy (due to truncated HANDLE) for PIDs that no longer existed. The daemon could not be restarted without manually deleting the lock file.
2. No OS-level file locking was in place — the lock was purely convention-based (file existence + PID check). Any process that deleted the lock file while the daemon was running would allow a second instance to start.
3. No cleanup handlers existed — if the daemon crashed without reaching the `finally` block in `__main__.py`, the lock file persisted with a stale PID indefinitely.
4. Zombie processes (process table entry exists, process is dead) caused `OpenProcess` to return a valid handle, making `_pid_alive()` return `True` for dead processes. `GetExitCodeProcess` was never consulted.

## Decision

1. Fix all ctypes bindings: explicit `restype` and `argtypes` on `OpenProcess`, `GetExitCodeProcess`, and `CloseHandle`. Use `c_void_p` for HANDLE to correctly represent a 64-bit pointer on 64-bit Windows.
2. Use `PROCESS_QUERY_LIMITED_INFORMATION` (0x1000) instead of `PROCESS_QUERY_INFORMATION` (0x0400) — lower privilege, works for cross-session queries, and is sufficient for the exit-code check.
3. After `OpenProcess` succeeds, call `GetExitCodeProcess` and treat any exit code other than `STILL_ACTIVE` (259) as dead. This detects zombie processes that `OpenProcess` alone cannot distinguish from live ones.
4. Primary locking via OS-level file locks: `msvcrt.locking(LK_NBLCK)` on Windows, `fcntl.flock(LOCK_EX | LOCK_NB)` on POSIX. These locks are held by the OS and automatically released when the process dies, regardless of whether the `finally` block executes.
5. Write `PID:GUID` to the lock file — the GUID mitigates PID reuse for diagnostic clarity (a new daemon with a recycled PID will have a different GUID).
6. Register an `atexit` handler plus signal handlers (SIGINT, SIGTERM, and SIGBREAK on Windows) inside `acquire()` for clean lock-file removal on normal exit.
7. The `AlreadyRunning` exception message includes the offending PID so the operator knows exactly which process to investigate.

## Consequences

### Positive

- The OS releases the file lock on crash or SIGKILL — no more stale locks requiring manual intervention after an unclean shutdown.
- The ctypes 32-bit truncation bug is eliminated; `_pid_alive()` now returns correct results on 64-bit Windows for all PID values.
- Zombie processes are detected via `GetExitCodeProcess`; `_stale()` correctly reclaims locks left by dead-but-not-reaped processes.
- Clean shutdown (normal exit, SIGINT, SIGTERM, SIGBREAK) removes the lock file via the registered handlers, keeping `outputs/` tidy.

### Negative

- `msvcrt.locking` is advisory on Windows (cooperative, not mandatory enforcement). If a non-daemon process were to open the lock file and lock the same byte range, it could interfere with lock acquisition. This is acceptable because lock files reside in `outputs/`, which is daemon-only territory.

### Neutral

- The POSIX path (`fcntl.flock`) is unchanged in observable behaviour; it is now formally specified rather than implicitly assumed.
- The existing `__main__.py` try/finally block continues to call `release()` explicitly as defence-in-depth. The OS-level lock and the explicit release are complementary — the OS lock is the safety net, the explicit release is the clean path.

## Alternatives considered

### `psutil.pid_exists()`
Rejected. Adds an external dependency (`psutil`) for a single function. The stdlib path via `ctypes` is sufficient once the bindings are fixed correctly.

### `filelock` package
Rejected for the same reason — external dependency when stdlib `msvcrt` / `fcntl` covers the required semantics without additional installation cost.

### Named mutex (`win32event.CreateMutex`)
Rejected. Requires the `pywin32` dependency. A file lock achieves identical "one instance only" semantics using stdlib alone. Named mutexes also do not survive across interactive sessions consistently without additional session-isolation flags.

### Fix ctypes only, no OS-level locking
Rejected. Fixing the ctypes truncation bug is necessary but not sufficient. Without OS-level locking, a crash between `_stale()` returning `True` and the new lock being written still allows a race. It also leaves the stale-lock-on-crash problem open for the edge case where the lock file's PID field is corrupted and `_pid_alive()` cannot be called at all.
