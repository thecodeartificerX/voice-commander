# FastAPI Server-Sent Events via StreamingResponse

**Source:** 
- https://fastapi.tiangolo.com/advanced/custom-response/
- https://www.starlette.io/responses/
- https://html.spec.whatwg.org/multipage/server-sent-events.html

**Date fetched:** 2026-04-27

---

## Overview

`StreamingResponse` streams response bodies using async generators or normal generators/iterators. For SSE (Server-Sent Events), you pair it with the `text/event-stream` MIME type and yield SSE-formatted frames from an async generator function.

**Key insight:** `StreamingResponse` is re-exported by `fastapi.responses` from `starlette.responses.StreamingResponse`. The Starlette implementation handles ASGI transport; you only write the generator.

---

## API: StreamingResponse

### Signature

```python
StreamingResponse(content, status_code=200, headers=None, media_type=None)
```

**Parameters:**
- `content` — An async generator, normal generator, or iterator yielding bytes or strings.
- `status_code` — HTTP status (default 200).
- `headers` — Optional dict of custom headers.
- `media_type` — Content-Type (e.g., `"text/event-stream"` for SSE).

### Usage in FastAPI

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.get("/stream")
async def stream_endpoint():
    async def generator():
        # yield bytes or strings here
        yield b"some data\n"
    
    return StreamingResponse(generator(), media_type="text/event-stream")
```

**Generator lifecycle:**
1. `StreamingResponse` receives the generator object.
2. ASGI iterates over it (via `async for` internally).
3. Each yielded chunk is written to the HTTP response body.
4. When the generator returns, the response closes.

---

## SSE Protocol Format

Each SSE event is a series of colon-delimited fields followed by a **double newline** (`\n\n`).

```
id: <event_id>
event: <event_type>
data: <json_payload>

```

**Field specs:**
- `id: <int>` — Unique event ID; client echoes this in `Last-Event-ID` header on reconnect.
- `event: <event_type>` — Custom event type. Sent to `EventSource.addEventListener(eventType, ...)`. Omit to default to `"message"`.
- `data: <payload>` — Event payload. If multi-line, repeat `data:` for each line; they're concatenated with `\n`.
- `: <comment>` — Comment line (ignored by clients); use for keepalive.
- **Double newline** — Ends the event. **Critical:** always end with `\n\n`.

### Example Frame

```
id: 42
event: heartbeat
data: {"timestamp": 1234567890, "status": "ok"}

```

### Keepalive Pattern

To prevent proxies or browsers from timing out a long-held SSE connection, emit a comment line periodically:

```python
yield b": keepalive\n\n"
```

This is a valid SSE frame (ignored by clients) that resets idle timeouts.

---

## Client Disconnection Detection

Use **`request.is_disconnected()`** inside the generator loop to detect when the client closes the connection. Without this, the generator continues running forever, leaking resources.

```python
from fastapi import Request

@app.get("/stream")
async def stream_endpoint(request: Request):
    async def generator():
        while True:
            if await request.is_disconnected():
                break  # Client disconnected
            # yield events...
    
    return StreamingResponse(generator(), media_type="text/event-stream")
```

**Why it matters:**
- `await request.is_disconnected()` is async; it integrates into your event loop without blocking.
- Call it frequently in long-running generators to exit cleanly.
- If you don't call it, a closed connection leaves the generator (and its threads/resources) running forever.

---

## Sync Queue → Async Generator Bridge

A common pattern: drain a **sync, blocking** `queue.Queue` inside an **async** generator. Calling `q.get()` directly would block the event loop. Instead, use `asyncio.get_event_loop().run_in_executor()`:

```python
import asyncio
import queue

async def generator(q: queue.Queue):
    loop = asyncio.get_event_loop()
    while True:
        if await request.is_disconnected():
            break
        try:
            # Run q.get() in a thread pool; don't block the event loop
            event = await loop.run_in_executor(None, q.get, True, 5.0)
        except queue.Empty:
            # Timeout; emit keepalive instead of busy-looping
            yield b": keepalive\n\n"
            continue
        
        yield format_event(event)
```

**Key points:**
- `loop.run_in_executor(None, q.get, True, 5.0)` — runs `q.get(True, 5.0)` (blocking get with 5-second timeout) in a thread pool.
- The `None` argument uses the default executor (ThreadPoolExecutor).
- Catches `queue.Empty` on timeout; you can then emit a keepalive or sleep briefly.
- Never call `q.get()` directly in an async context—it will block all other tasks on the event loop.

---

## Last-Event-ID Reconnection Header

When a client reconnects after a network interruption, it sends a `Last-Event-ID` header containing the last event ID it received. Use this to resume streaming from that point:

```python
from fastapi import Header

@app.get("/stream")
async def stream_endpoint(
    request: Request,
    last_event_id: int = Header(default=0, alias="Last-Event-ID")
):
    async def generator():
        # Start streaming from last_event_id + 1
        for event in bus.events_since(last_event_id):
            if await request.is_disconnected():
                break
            yield format_event(event)
    
    return StreamingResponse(generator(), media_type="text/event-stream")
```

**Header extraction:**
- `Header(default=0, alias="Last-Event-ID")` — extracts the `Last-Event-ID` HTTP header.
- The alias maps the HTTP header name (case-insensitive) to the Python parameter.
- Default to 0 (or the earliest event ID) if missing.

---

## Generator Function Signature: Async vs. Sync

`StreamingResponse` accepts **both** async generators and sync generators.

### Async Generator (Recommended)

```python
async def my_generator():
    yield b"data\n"
    await asyncio.sleep(1)
    yield b"more\n"

return StreamingResponse(my_generator(), media_type="text/event-stream")
```

**Advantages:**
- Can `await` other async operations (e.g., `await request.is_disconnected()`, database queries).
- Doesn't block the event loop.
- Essential for SSE because you need to check `request.is_disconnected()`.

### Sync Generator (Rarely Used)

```python
def my_generator():
    yield b"data\n"
    time.sleep(1)
    yield b"more\n"

return StreamingResponse(my_generator(), media_type="text/event-stream")
```

**Disadvantages:**
- `time.sleep()` blocks the event loop.
- Cannot `await request.is_disconnected()` (not an async function).
- Leaks resources on disconnection.

**Recommendation:** Always use `async def` for SSE.

---

## MIME Type and Headers

### Media Type

```python
StreamingResponse(gen(), media_type="text/event-stream")
```

This sets the `Content-Type: text/event-stream` header (the SSE MIME type). Browsers recognize this and parse the stream as SSE.

### Additional Headers (Optional)

Set these explicitly if needed:

```python
headers = {
    "Cache-Control": "no-cache",           # Don't cache the stream
    "X-Accel-Buffering": "no",            # Nginx: don't buffer before sending
}
StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
```

**Defaults set by Starlette/FastAPI:**
- `Content-Type: text/event-stream` (from `media_type`).
- `Connection: keep-alive` (not always; depends on HTTP version).

**When to set extras:**
- `Cache-Control: no-cache` — Tell browsers not to cache the SSE stream (good practice for real-time data).
- `X-Accel-Buffering: no` — If behind Nginx, prevent it from buffering frames (causes latency).

---

## Complete Minimal Example

This mirrors the intended usage pattern: sync queue (from a pub/sub bus) → async generator → SSE response.

```python
import asyncio
import json
import queue
from fastapi import FastAPI, Request, Header
from fastapi.responses import StreamingResponse

app = FastAPI()

# Mock event bus (in practice, a real pub/sub)
class EventBus:
    def __init__(self):
        self._subscribers = []
    
    def subscribe(self):
        """Return a queue for this subscriber."""
        q = queue.Queue()
        self._subscribers.append(q)
        return q
    
    def unsubscribe(self, q):
        """Remove a subscriber."""
        if q in self._subscribers:
            self._subscribers.remove(q)
    
    def publish(self, event_id, event_type, data):
        """Emit an event to all subscribers."""
        for q in self._subscribers:
            q.put({"id": event_id, "type": event_type, "data": data})

bus = EventBus()

@app.get("/events")
async def stream_events(
    request: Request,
    last_event_id: int = Header(default=0, alias="Last-Event-ID")
):
    """Stream SSE events from the bus."""
    
    async def event_generator():
        q = bus.subscribe()
        try:
            loop = asyncio.get_event_loop()
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                
                try:
                    # Get next event from queue (blocking, with 15-second timeout)
                    event = await loop.run_in_executor(None, q.get, True, 15.0)
                except queue.Empty:
                    # Timeout: emit keepalive to prevent proxy timeout
                    yield b": keepalive\n\n"
                    continue
                
                # Skip events before last_event_id (reconnect case)
                if event["id"] <= last_event_id:
                    continue
                
                # Format and yield SSE frame
                frame = (
                    f"id: {event['id']}\n"
                    f"event: {event['type']}\n"
                    f"data: {json.dumps(event['data'])}\n\n"
                ).encode()
                yield frame
        
        finally:
            # Cleanup: unsubscribe from bus
            bus.unsubscribe(q)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"}
    )

# Test publish (curl http://localhost:8000/events)
@app.post("/publish")
async def publish_event(event_type: str, data: dict):
    bus.publish(1, event_type, data)
    return {"ok": True}
```

**Key patterns in this example:**
1. **Subscription:** `q = bus.subscribe()` returns a new queue.
2. **Executor bridge:** `await loop.run_in_executor(None, q.get, True, 15.0)` avoids blocking the event loop.
3. **Disconnection check:** `if await request.is_disconnected()` exits the loop when the client closes.
4. **Keepalive:** On timeout, emit `b": keepalive\n\n"` instead of blocking.
5. **Reconnect logic:** Skip events `<= last_event_id` (the client already received them).
6. **Cleanup:** `finally` block unsubscribes from the bus.
7. **SSE frame format:** Three fields (`id`, `event`, `data`) + double newline.

---

## Common Failure Modes

### 1. Forgetting `await request.is_disconnected()`

**Problem:**
```python
async def generator():
    while True:
        yield b"data\n"  # Never exits; generator runs forever
```

**Result:** Closed connections leave the generator (and any spawned threads) running indefinitely. Memory leak.

**Fix:** Call `await request.is_disconnected()` in every loop iteration.

### 2. Calling `q.get()` Directly in Async Generator

**Problem:**
```python
async def generator(q):
    while True:
        event = q.get()  # BLOCKS the event loop for 5+ seconds
        yield format_event(event)
```

**Result:** Blocks the entire event loop; other requests hang.

**Fix:** Use `await loop.run_in_executor(None, q.get, True, timeout)`.

### 3. Forgetting `finally` / Bus Unsubscription

**Problem:**
```python
async def generator():
    q = bus.subscribe()
    # ... if exception or disconnect, never calls bus.unsubscribe(q)
    # Queue piles up in memory forever
```

**Result:** Memory leak; the queue is never removed from subscribers.

**Fix:** Wrap in `try/finally`:
```python
try:
    # ... generator logic
finally:
    bus.unsubscribe(q)
```

### 4. Returning Raw String Instead of Bytes

**Problem:**
```python
yield "id: 1\nevent: test\ndata: {}\n\n"  # String, not bytes
```

**Result:** TypeError or encoding mismatch.

**Fix:** Call `.encode()` or yield bytes:
```python
yield "id: 1\nevent: test\ndata: {}\n\n".encode()
# or
yield b"id: 1\nevent: test\ndata: {}\n\n"
```

### 5. Missing Double Newline in SSE Frame

**Problem:**
```python
yield f"id: {ev_id}\nevent: test\ndata: {json.dumps(data)}\n"
# Only ONE newline at the end; frame not terminated
```

**Result:** Client bundles frames together; event boundaries lost.

**Fix:** Always end with `\n\n`:
```python
yield f"id: {ev_id}\nevent: test\ndata: {json.dumps(data)}\n\n".encode()
```

---

## Pinned Versions

Documented for **FastAPI 0.115.x** and **Starlette** (the ASGI framework underlying FastAPI).

- **FastAPI:** 0.115.x
- **Starlette:** Version pinned via FastAPI's dependencies (typically 0.38.x+).
- **Date:** 2026-04-27

For the most current Starlette version bundled with your FastAPI, check `pip show fastapi` → `Requires: starlette` in the output.
