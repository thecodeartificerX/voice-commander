# ADR 0020: Embed FastAPI + uvicorn as a daemon thread inside the existing Voice Commander process

**Status:** Accepted
**Date:** 2026-04-20

## Context

Phase 6 requires a web UI for command management — viewing tools, editing phrases, toggling tools on/off. The web server needs access to the live `ToolRegistry` to reflect real-time state and apply mutations instantly.

Three deployment models were evaluated:

1. **Separate process** — a standalone server communicates with the daemon over IPC (socket, pipe, or HTTP).
2. **Electron** — a bundled desktop app with a Chromium renderer and a Node.js backend.
3. **Embedded server thread** — the HTTP server runs inside the existing daemon process, sharing memory directly with the registry.

## Decision

Embed FastAPI on a `uvicorn` thread inside the existing Voice Commander daemon process.

- `uvicorn.Server` is configured with `config.loop = "none"` and run via `threading.Thread(target=server.run, daemon=True)`. The thread starts after all subsystems are initialised and the registry is fully populated.
- The `ToolRegistry` singleton is imported directly by the FastAPI route handlers — no serialisation, no IPC.
- All mutations (phrase edits, enable/disable toggles) acquire a `threading.Lock` on the registry before writing. Reads are lock-free (Python GIL provides sufficient safety for attribute reads on stable objects).
- The server binds to `127.0.0.1` only, port configurable in `config.toml` under `[web]`.

## Consequences

### Positive
- Zero IPC complexity — route handlers call registry methods directly.
- Single process to launch, monitor, and kill. No process orchestration.
- Shared memory means the UI always sees the live registry state with no synchronisation lag.
- FastAPI provides automatic OpenAPI docs at `/docs` for free — useful for scripting and testing.

### Negative
- uvicorn's asyncio event loop runs on its own thread. Care is required not to call `asyncio.get_event_loop()` from the main thread expecting the uvicorn loop.
- A crash in a FastAPI route handler can surface tracebacks that intermix with daemon log output if logging is not separated by logger name.
- Adding async route handlers that call blocking tools (e.g. subprocess-based tools) requires `run_in_executor` to avoid stalling the uvicorn loop.

### Neutral
- Flask was considered and rejected: it has a weaker async story and no built-in OpenAPI generation. The migration cost from Flask to FastAPI grows over time; starting with FastAPI is the correct long-term choice.
- Electron was rejected as overkill for a utility with 6 routes and no offline-capable SPA requirements. It would add ~150 MB to the distribution and a Node.js build step.

## Alternatives considered

### Separate process with IPC
A dedicated server process communicates with the daemon over a Unix domain socket or named pipe. Rejected: two processes to manage, a serialisation protocol to maintain, and latency on every registry read. Adds substantial complexity with no user-visible benefit.

### Flask
Simpler than FastAPI but synchronous by default. Rejected: FastAPI's async support, dependency injection, and automatic OpenAPI docs make it the better long-term foundation. Switching later costs more than the marginal complexity of FastAPI today.

### Electron
A full Chromium + Node.js bundle. Rejected: no meaningful capability gained over a browser tab; adds ~150 MB and a Node build pipeline. The target user already has a browser.

## References
- ADR 0010: threading model
- FastAPI docs: https://fastapi.tiangolo.com/
- uvicorn programmatic usage: https://www.uvicorn.org/#usage
