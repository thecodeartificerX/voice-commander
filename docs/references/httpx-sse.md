# httpx-sse Reference

Server-Sent Event (SSE) consumption library for HTTPX. Provides sync and async context managers for streaming event data from SSE endpoints without managing low-level HTTP details.

**Version:** 0.4.x (pin version constraints in production—this is beta software)  
**Install:** `pip install httpx-sse`  
**License:** MIT  

## Installation

```bash
pip install "httpx-sse>=0.4.0,<0.5.0"
```

Requires:
- `httpx>=0.26.0` (tested with 0.26+)
- Python 3.8+

## Core API

### Sync: `connect_sse()`

Establishes a synchronous connection to an SSE endpoint.

```python
def connect_sse(
    client: httpx.Client,
    method: str,
    url: Union[str, httpx.URL],
    **kwargs,
) -> ContextManager[EventSource]
```

**Parameters:**
- `client` — Active `httpx.Client` instance (you manage its lifecycle)
- `method` — HTTP verb as string: `"GET"`, `"POST"`, etc.
- `url` — Endpoint URL
- `**kwargs` — Forwarded to `client.request()`: `params`, `headers`, `cookies`, `auth`, `timeout`, etc.

**Auto-applied headers** (per RFC 9110 SSE spec):
- `Cache-Control: no-store`
- `Accept: text/event-stream`

**Yields:** `EventSource` context manager

**Raises:**
- `SSEError` (httpx.TransportError) if Content-Type ≠ `text/event-stream` or stream is malformed

**Example:**
```python
import httpx
from httpx_sse import connect_sse

client = httpx.Client()
with connect_sse(client, "GET", "https://example.com/events") as event_source:
    for sse_event in event_source.iter_sse():
        print(f"Event: {sse_event.event}, Data: {sse_event.data}")
```

---

### Async: `aconnect_sse()`

Establishes an asynchronous connection to an SSE endpoint.

```python
async def aconnect_sse(
    client: httpx.AsyncClient,
    method: str,
    url: Union[str, httpx.URL],
    **kwargs,
) -> AsyncContextManager[EventSource]
```

**Parameters:** Identical to `connect_sse()`

**Yields:** `EventSource` context manager (async)

**Example:**
```python
import httpx
from httpx_sse import aconnect_sse

async with httpx.AsyncClient() as client:
    async with aconnect_sse(client, "GET", "https://example.com/events") as event_source:
        async for sse_event in event_source.aiter_sse():
            print(f"Event: {sse_event.event}, Data: {sse_event.data}")
```

---

## EventSource Object

The context manager yields an `EventSource` helper for managing the SSE stream.

### Properties

#### `response: httpx.Response`

The underlying HTTP response object. Use it to check status codes, headers, or access raw bytes:

```python
with connect_sse(client, "GET", url) as event_source:
    if event_source.response.status_code != 200:
        raise RuntimeError(f"HTTP {event_source.response.status_code}")
    print(event_source.response.headers["content-type"])
```

---

### Methods

#### `iter_sse() -> Iterator[ServerSentEvent]`

Synchronously decodes the response body as an SSE stream, yielding events one at a time.

**Blocks until:**
- A complete SSE message is available (terminated by blank line)
- Stream is closed
- Error is encountered

**Raises:**
- `httpx.ReadTimeout` — if read timeout fires during streaming (see [Timeout Configuration](#timeout-configuration-for-long-lived-streams))
- `httpx.RemoteProtocolError` — malformed SSE syntax (e.g., invalid field names)
- `httpx.ConnectError` — network I/O error

**Example:**
```python
with connect_sse(client, "GET", url) as event_source:
    for sse in event_source.iter_sse():
        if sse.event == "update":
            print(sse.data)
```

---

#### `aiter_sse() -> AsyncIterator[ServerSentEvent]`

Asynchronously decodes the response body as an SSE stream, yielding events one at a time.

**Behavior:** Identical to `iter_sse()` but async-safe.

**Raises:** Same error types as `iter_sse()`

**Example:**
```python
async with aconnect_sse(client, "GET", url) as event_source:
    async for sse in event_source.aiter_sse():
        if sse.event == "update":
            print(sse.data)
```

---

## ServerSentEvent Object

Represents a decoded SSE message. Fields are immutable and populated per RFC 9110.

### Attributes

#### `event: str`

Event type identifier. Set by the `event:` field in the SSE stream. Defaults to `"message"`.

```python
if sse.event == "ping":
    print("Server is alive")
```

---

#### `data: str`

Event payload. Set by one or more `data:` fields (concatenated with `\n` if multiple). Defaults to `""`.

```python
print(sse.data)  # Raw string, not JSON-decoded
```

---

#### `id: str`

Event identifier. Set by the `id:` field. Defaults to `""` (not set).

Use `id` to resume interrupted streams (see [Reconnect with Last-Event-ID](#reconnect-with-last-event-id)).

```python
last_id = sse.id if sse.id else last_id
```

---

#### `retry: int | None`

Server-recommended reconnection delay in **milliseconds**. Set by the `retry:` field. Defaults to `None`.

**Important:** Retry is a _suggestion_; the caller is responsible for implementing reconnect logic. httpx-sse does not auto-reconnect.

```python
delay_ms = sse.retry if sse.retry is not None else 1000
time.sleep(delay_ms / 1000.0)
```

---

### Methods

#### `json() -> Any`

Parses `data` field as JSON and returns the decoded object.

**Raises:**
- `json.JSONDecodeError` if data is not valid JSON

**Example:**
```python
sse = next(event_source.iter_sse())
parsed = sse.json()  # Raises if data is not JSON
```

---

## Error Types

### `SSEError`

Custom exception raised when the SSE endpoint returns an invalid response or stream is malformed.

**Parent class:** `httpx.TransportError`  
**When raised:**
- Response Content-Type ≠ `text/event-stream`
- SSE stream contains invalid syntax (e.g., malformed field names)

```python
from httpx_sse import SSEError
import httpx

try:
    with connect_sse(client, "GET", url) as event_source:
        for sse in event_source.iter_sse():
            print(sse.data)
except SSEError as e:
    print(f"SSE error: {e}")
```

---

### HTTP-Level Errors

httpx-sse propagates httpx exceptions directly:

| Exception | Raised by | Cause |
|-----------|-----------|-------|
| `httpx.ConnectError` | `aconnect_sse()` / `connect_sse()` | Cannot reach server (DNS, refused, unreachable) |
| `httpx.ReadTimeout` | `iter_sse()` / `aiter_sse()` | Read timeout exceeded during streaming |
| `httpx.WriteTimeout` | Initial request | Request body send timeout |
| `httpx.PoolTimeout` | Client connection pool | Cannot acquire connection within timeout |
| `httpx.RemoteProtocolError` | Stream iteration | Server sent invalid HTTP or SSE syntax |

---

## Timeout Configuration for Long-Lived Streams

**Critical:** By default, `httpx.Client(timeout=5.0)` sets a 5-second **read timeout** on the entire request. For SSE (which expects long periods of inactivity between events), this will prematurely raise `ReadTimeout`.

### Problem

A stream that goes silent for >5 seconds will trigger `ReadTimeout`, even if the server is still alive and will send an event in 10 seconds.

```python
# WRONG: Will timeout if events are sparse
client = httpx.Client(timeout=5.0)  # 5-second global timeout
with connect_sse(client, "GET", url) as event_source:
    for sse in event_source.iter_sse():  # Raises ReadTimeout if silent >5s
        print(sse.data)
```

### Solution

Use `httpx.Timeout()` with separate `read` and `pool` timeouts. Set `read` to either:

1. **`None`** (disable read timeout entirely, rely on keep-alive)
2. **Large value** (e.g., 300 seconds for events expected within 5 minutes)

Keep `connect` and `pool` timeouts tight to fail fast on dead servers:

```python
import httpx
from httpx_sse import connect_sse

# Recommended for SSE
timeout = httpx.Timeout(
    connect=10.0,   # Fail fast if server unreachable
    read=None,      # No timeout on read (rely on server keep-alive)
    write=10.0,     # Fail fast if we can't send request body
    pool=10.0,      # Fail fast if connection pool exhausted
)

client = httpx.Client(timeout=timeout)
with connect_sse(client, "GET", url) as event_source:
    for sse in event_source.iter_sse():
        print(sse.data)
```

Alternatively, if your server sends keep-alive comments (`:` line) at least every N seconds:

```python
# If server sends keep-alive every 30 seconds, allow 60-second read timeout
timeout = httpx.Timeout(
    connect=10.0,
    read=60.0,      # Events or keep-alive every 60 seconds
    write=10.0,
    pool=10.0,
)
```

### Per-Request Override

You can also override timeout per `connect_sse()` call:

```python
with connect_sse(
    client, 
    "GET", 
    url,
    timeout=httpx.Timeout(read=None, connect=10.0)
) as event_source:
    for sse in event_source.iter_sse():
        print(sse.data)
```

---

## Reconnect Pattern with Exponential Backoff

**Critical:** httpx-sse does not auto-reconnect. You must implement reconnect logic.

The SSE spec allows servers to include a `retry:` field (in milliseconds) that suggests a reconnect delay. However, best practice is to combine this with **exponential backoff with jitter** to avoid thundering herd on server failures.

### Using `stamina` Library

The recommended approach is the `stamina` library (by Hynek), which applies exponential backoff with jitter on top of server-provided retry values:

```bash
pip install stamina
```

**Example: Sync reconnect loop with stamina**

```python
import httpx
import stamina
from httpx_sse import connect_sse, SSEError

def subscribe_with_reconnect(url: str) -> None:
    """Connect and auto-reconnect on failure with exponential backoff."""
    client = httpx.Client(timeout=httpx.Timeout(connect=10.0, read=None))
    last_event_id = ""
    
    for attempt in stamina.retry(
        on=(SSEError, httpx.ConnectError, httpx.ReadTimeout),
        wait_initial=1.0,        # Start at 1 second
        wait_max=60.0,           # Cap at 60 seconds
        wait_jitter=stamina.random_jitter,
    ):
        with attempt:
            headers = {}
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id
            
            try:
                with connect_sse(
                    client, 
                    "GET", 
                    url, 
                    headers=headers
                ) as event_source:
                    for sse in event_source.iter_sse():
                        print(f"Event: {sse.event}, Data: {sse.data}")
                        if sse.id:
                            last_event_id = sse.id
            except (SSEError, httpx.ConnectError, httpx.ReadTimeout) as e:
                print(f"Connection lost: {e}. Reconnecting...")
                raise  # Let stamina handle retry logic
    
    client.close()

# Usage
subscribe_with_reconnect("https://example.com/events")
```

**Example: Async reconnect loop**

```python
import httpx
import stamina
from httpx_sse import aconnect_sse, SSEError

async def subscribe_with_reconnect_async(url: str) -> None:
    """Async version of reconnect loop."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=None)
    ) as client:
        last_event_id = ""
        
        for attempt in stamina.retry(
            on=(SSEError, httpx.ConnectError, httpx.ReadTimeout),
            wait_initial=1.0,
            wait_max=60.0,
            wait_jitter=stamina.random_jitter,
        ):
            with attempt:
                headers = {}
                if last_event_id:
                    headers["Last-Event-ID"] = last_event_id
                
                try:
                    async with aconnect_sse(
                        client, 
                        "GET", 
                        url, 
                        headers=headers
                    ) as event_source:
                        async for sse in event_source.aiter_sse():
                            print(f"Event: {sse.event}, Data: {sse.data}")
                            if sse.id:
                                last_event_id = sse.id
                except (SSEError, httpx.ConnectError, httpx.ReadTimeout) as e:
                    print(f"Connection lost: {e}. Reconnecting...")
                    raise  # Let stamina handle retry logic

# Usage
import asyncio
asyncio.run(subscribe_with_reconnect_async("https://example.com/events"))
```

### Manual Reconnect (No stamina)

If you prefer manual control:

```python
import time
import httpx
from httpx_sse import connect_sse, SSEError

def subscribe_manual_reconnect(url: str, max_retries: int = 10) -> None:
    """Manual reconnect with exponential backoff."""
    client = httpx.Client(timeout=httpx.Timeout(connect=10.0, read=None))
    last_event_id = ""
    attempt = 0
    
    while attempt < max_retries:
        try:
            headers = {}
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id
            
            with connect_sse(client, "GET", url, headers=headers) as event_source:
                attempt = 0  # Reset attempt counter on successful connection
                for sse in event_source.iter_sse():
                    print(f"Event: {sse.event}, Data: {sse.data}")
                    if sse.id:
                        last_event_id = sse.id
        except (SSEError, httpx.ConnectError, httpx.ReadTimeout) as e:
            attempt += 1
            if attempt >= max_retries:
                print(f"Max retries exceeded. Giving up.")
                raise
            
            # Exponential backoff: 1s, 2s, 4s, 8s, etc.
            wait_time = min(2 ** attempt, 60.0)
            print(f"Connection lost ({e}). Retrying in {wait_time}s...")
            time.sleep(wait_time)
    
    client.close()
```

---

## Per-Event Handling Patterns

### Dispatching by Event Type

```python
with connect_sse(client, "GET", url) as event_source:
    for sse in event_source.iter_sse():
        if sse.event == "message":
            handle_message(sse.data)
        elif sse.event == "status":
            handle_status(sse.json())
        elif sse.event == "ping":
            print("Keep-alive ping received")
        else:
            print(f"Unknown event: {sse.event}")
```

### JSON Event Handling

```python
with connect_sse(client, "GET", url) as event_source:
    for sse in event_source.iter_sse():
        try:
            payload = sse.json()
            process_payload(payload)
        except json.JSONDecodeError:
            print(f"Received non-JSON data: {sse.data}")
```

### Track Last Successful Event

```python
with connect_sse(client, "GET", url) as event_source:
    last_event_id = ""
    event_count = 0
    
    for sse in event_source.iter_sse():
        event_count += 1
        if sse.id:
            last_event_id = sse.id
        
        if event_count % 100 == 0:
            print(f"Processed {event_count} events. Last ID: {last_event_id}")
```

---

## Version Compatibility

| httpx-sse | httpx  | Python | Status |
|-----------|--------|--------|--------|
| 0.4.x     | ≥0.26  | ≥3.8   | Current |
| 0.3.x     | ≥0.16  | ≥3.7   | Deprecated |

Always pin to a major.minor range in `requirements.txt` or `pyproject.toml`:

```toml
[project]
dependencies = [
    "httpx-sse>=0.4.0,<0.5.0",
]
```

---

## Troubleshooting

### "ContentType … does not match 'text/event-stream'"

The server is not returning the correct Content-Type header. Verify the endpoint and server logs.

```python
with connect_sse(client, "GET", url) as event_source:
    print(event_source.response.headers.get("content-type"))
    # Should be: text/event-stream (or charset variant)
```

### ReadTimeout during sparse events

Set `read=None` in timeout config (see [Timeout Configuration](#timeout-configuration-for-long-lived-streams)).

### Missed events on reconnect

Use the `Last-Event-ID` header to resume from the last known event ID:

```python
headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
with connect_sse(client, "GET", url, headers=headers) as event_source:
    for sse in event_source.iter_sse():
        last_event_id = sse.id or last_event_id
```

---

## References

- **Official repository:** [github.com/florimondmanca/httpx-sse](https://github.com/florimondmanca/httpx-sse)
- **PyPI:** [pypi.org/project/httpx-sse/](https://pypi.org/project/httpx-sse/)
- **HTTPX docs:** [python-httpx.org](https://www.python-httpx.org/)
- **SSE spec (RFC 9110):** [tools.ietf.org/html/rfc9110](https://tools.ietf.org/html/rfc9110)
- **Stamina (retry library):** [github.com/hynek/stamina](https://github.com/hynek/stamina)
