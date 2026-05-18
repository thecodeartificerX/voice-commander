# `websockets` (Python) — API Reference

**Library:** `websockets` — async WebSocket client/server (RFC 6455).
**Version targeted:** `>= 14.0` (current stable 16.x, Jan 2026).
**Used by:** `src/voice_commander/dictation_stream/ws_client.py` (streaming dictation experiment).

Vendored per `CLAUDE.md` — research lands here before code depends on it.

---

## Import paths (v14+ asyncio API)

The modern asyncio implementation became the default in v14.0. The legacy
`websockets.client` / `websockets.legacy` API is deprecated (removed ~2030).
**Always use the `websockets.asyncio.*` paths:**

```python
from websockets.asyncio.client import connect          # client
from websockets.asyncio.server import serve            # server (used by tests)
from websockets.exceptions import (
    ConnectionClosed, ConnectionClosedOK, ConnectionClosedError,
    InvalidURI, InvalidHandshake,
)
```

## Client — `connect()`

`connect(uri, *, open_timeout=10, ping_interval=20, ping_timeout=20, close_timeout=10, max_size=1048576, ...)`
→ async context manager yielding a `ClientConnection`.

```python
async with connect("ws://host:8765/ws/transcribe") as ws:
    await ws.send(...)
    reply = await ws.recv()
```

**Raises at connect time:**
- `InvalidURI` — URI is not `ws://` / `wss://`.
- `OSError` / `ConnectionRefusedError` — TCP connect failed (server unreachable).
- `InvalidHandshake` (and subclasses `InvalidStatus`, `InvalidMessage`) — server rejected the upgrade.
- `asyncio.TimeoutError` — opening handshake exceeded `open_timeout`.

## Sending — `ws.send(message)`

Auto-detects frame type by Python type:
- `str` → **TEXT** frame. Use for JSON: `await ws.send(json.dumps({...}))`.
- `bytes` / `bytearray` / `memoryview` → **BINARY** frame. Use for WAV chunks: `await ws.send(wav_bytes)`.

`send()` raises `ConnectionClosed` if the peer has closed. Concurrent `send()`
calls on one connection raise `ConcurrencyError` — serialize sends.

## Receiving — `ws.recv()`

Returns `str` for a TEXT frame, `bytes` for a BINARY frame. Use
`isinstance(msg, str)` to discriminate. Fragmented messages are reassembled
transparently.

Raises on a closed connection — this is the **normal** disconnect signal:
- `ConnectionClosedOK` — clean close (code 1000/1001).
- `ConnectionClosedError` — abnormal close.
- Both subclass `ConnectionClosed` — catch that to cover either.

## Timeouts

`recv()` has no built-in timeout. Wrap it:

```python
import asyncio
try:
    msg = await asyncio.wait_for(ws.recv(), timeout=30.0)   # Python 3.10+
except asyncio.TimeoutError:
    ...   # no message within 30 s
```

Cancelling a `recv()` is safe — no message is lost; the next `recv()` returns it.

## Closing

`await ws.close(code=1000, reason="")`. The `async with connect(...)` block
closes automatically on exit. Default code 1000 = normal closure.

## Server (test-only) — `serve()`

`serve(handler, host, port)` → async context manager yielding a `Server`.
Pass `port=0` to bind a random free port; read it back from the socket.

```python
from websockets.asyncio.server import serve

async def handler(conn):                      # one coroutine per connection
    async for message in conn:                # str or bytes
        await conn.send(...)

async with serve(handler, "localhost", 0) as server:
    port = server.sockets[0].getsockname()[1]   # actual bound port
    ...
```

The handler signature in v14+ is `async def handler(connection)` — a single
argument (the legacy `(websocket, path)` two-arg form is gone).

## pytest fixture pattern (in-process mock server)

```python
import pytest
from websockets.asyncio.server import serve

@pytest.fixture
async def mock_server():
    async def handler(conn):
        async for message in conn:
            await conn.send("...")
    async with serve(handler, "localhost", 0) as server:
        yield f"ws://localhost:{server.sockets[0].getsockname()[1]}"
```

Requires `pytest-asyncio` with `asyncio_mode = "auto"` — already configured in
this repo's `pyproject.toml`.

## Install

```
websockets>=14.0
```

Pure-Python wheels; no native build. Pin added to `pyproject.toml` `dependencies`.

## Sources

- https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html
- https://websockets.readthedocs.io/en/stable/reference/asyncio/server.html
- https://websockets.readthedocs.io/en/stable/reference/exceptions.html
- https://websockets.readthedocs.io/en/stable/howto/upgrade.html
