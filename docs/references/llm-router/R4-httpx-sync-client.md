# R4: HTTPX Synchronous Client for LM Studio Integration

**Reference:** httpx sync client patterns for local LLM tool-calling router  
**Version:** 0.1  
**Date:** 2026-04-21

## Context

Voice-commander will route natural utterances ("open readme in projects folder") to a local LM Studio instance for intent parsing and argument extraction. The router lives in a worker thread of the pipeline, issuing one POST per utterance to `http://localhost:1234/v1/chat/completions`. Requirements:

- **Total timeout budget:** 600ms (aggressively short to keep voice latency low)
- **Connection reuse:** keepalive across many rapid requests (10–50 per minute during a session)
- **Error taxonomy:** distinct exception types for connect failure, read timeout, malformed JSON—so the caller can log exactly why and fall back to a fallback chime
- **Sync only:** worker thread cannot use async/await; no `asyncio` event loop

## Library Choice: `httpx.Client` vs `requests`

### Why HTTPX is Preferable

| Feature | httpx | requests |
|---------|-------|----------|
| **HTTP/2** | Native support | No |
| **Async support** | Both sync + async | Sync only (third-party libs needed) |
| **Connection pooling** | Fine-grained control (`Limits`) | Fixed defaults |
| **Timeout config** | Granular (connect, read, write, pool) | Single value + `connect_timeout` |
| **Type hints** | Full, strict | Minimal |
| **API similarity** | Drop-in replacement for `requests` | — |

**Decision:** Use `httpx.Client` for sync. It's a modern, actively maintained drop-in for `requests` with better timeout control and built-in connection pooling tuned for exactly this use case (low-latency local RPC).

---

## Timeout Configuration

### Single Global Timeout (Simple Case)

```python
client = httpx.Client(timeout=0.6)  # 600ms total
```

This enforces a 600ms budget across all phases: DNS, connect, TLS handshake, request send, response headers, response body.

### Granular Timeout (Recommended for LM Studio)

For fine-grained control, use `httpx.Timeout`:

```python
client = httpx.Client(
    timeout=httpx.Timeout(
        connect=0.1,      # 100ms to connect (local loopback, should be instant)
        read=0.45,        # 450ms to receive response (LM inference time)
        write=0.05,       # 50ms to send request (tiny payload)
        pool=None,        # No timeout for acquiring from pool
    )
)
```

**Rationale:**
- **Connect:** LM Studio is local (`localhost:1234`), so 100ms is plenty; loopback is µs-scale.
- **Read:** Most of the 600ms budget goes here—LM inference (even `phi-2` on CPU) takes 200–500ms.
- **Write:** Request is small (< 2 KB); 50ms is generous.
- **Pool:** No timeout—connection acquisition from pool is synchronous and should never block.

### Default Timeout Behavior

If no timeout is specified:
- **httpx default:** 5 seconds (per operation, not total)
- **For this use case:** You **must** set explicit timeouts. A 5s default would defeat voice interactivity.

### Timeout Inheritance

```python
client = httpx.Client(timeout=0.6)
response = client.post("http://localhost:1234/v1/chat/completions", json=payload)
# Uses client's 600ms timeout

response = client.post("...", json=payload, timeout=0.3)
# Per-request override: 300ms for this call only
```

---

## Connection Pooling & Keepalive

### Configuration

```python
client = httpx.Client(
    base_url="http://localhost:1234",
    limits=httpx.Limits(
        max_connections=2,          # Only 1–2 concurrent connections needed
        max_keepalive_connections=2,  # Keep 2 connections alive in pool
        keepalive_expiry=30.0,      # Expire idle connections after 30s
    ),
    timeout=httpx.Timeout(connect=0.1, read=0.45, write=0.05),
)
```

### Default Limits

If not specified, httpx uses:
- `max_connections=100`
- `max_keepalive_connections=20`
- `keepalive_expiry=5.0`

For a local daemon, these defaults are overkill. Use the tighter config above.

### HTTP/2 Caveat

LM Studio's OpenAI-compatible API likely runs HTTP/1.1 (not HTTP/2). HTTP/2 would actually complicate connection reuse:

- HTTP/1.1 + keepalive: One persistent TCP connection, pipelined requests ✓
- HTTP/2: Multiplexes streams over a single connection (more overhead for small payloads)

httpx detects and uses the best protocol automatically; you need not configure it. For `localhost:1234`, HTTP/1.1 with keepalive is optimal.

---

## Exception Taxonomy

httpx provides a structured exception hierarchy. All inherit from `httpx.HTTPError`:

```
httpx.HTTPError (base)
├── httpx.RequestError (network/I/O errors)
│   ├── httpx.ConnectError          # Failed to establish socket connection
│   ├── httpx.RemoteProtocolError   # Server sent invalid HTTP (e.g., non-200 + bad JSON)
│   └── httpx.TimeoutException (base)
│       ├── httpx.ConnectTimeout    # Timeout during TCP handshake
│       ├── httpx.ReadTimeout       # Timeout waiting for response body
│       ├── httpx.WriteTimeout      # Timeout sending request
│       └── httpx.PoolTimeout       # Timeout acquiring connection from pool
├── httpx.HTTPStatusError            # 4xx/5xx responses (not an error by default)
└── httpx.DecodingError              # Invalid response encoding (rare)
```

### When Each Exception Fires

| Exception | When | Cause |
|-----------|------|-------|
| `ConnectError` | TCP handshake fails | LM Studio not running, port closed, firewall |
| `ConnectTimeout` | Timeout during `connect=` | Network is very slow; loopback should never fire this |
| `ReadTimeout` | Timeout during `read=` | LM inference slow, or network latency (unlikely for local) |
| `WriteTimeout` | Timeout during `write=` | Network very slow sending (unlikely); very small payloads |
| `PoolTimeout` | Timeout acquiring connection | Pool exhausted; unlikely with `max_connections=2` |
| `RemoteProtocolError` | Server sends invalid HTTP | Unlikely for LM Studio; may occur if port is wrong |
| `DecodingError` | Response body not UTF-8 (rare) | LM Studio returns binary or non-UTF-8 data |

### JSON Parse Error (Not httpx)

If LM Studio returns 200 OK but invalid JSON in the body, `response.json()` raises `json.JSONDecodeError`, **not an httpx exception**. Catch separately:

```python
try:
    response = client.post(..., json=payload)
    data = response.json()
except httpx.ReadTimeout:
    log.error("LM Studio timed out")
except httpx.ConnectError:
    log.error("LM Studio not running")
except json.JSONDecodeError:
    log.error("LM Studio returned invalid JSON")
```

---

## Dependency Status in voice-commander

**httpx version in `pyproject.toml`:**

```toml
[dependency-groups.dev]
httpx = ">=0.27.0"
```

**Status:** httpx is in the **dev group** (test dependencies), not main `dependencies`. When LM Studio router is implemented (Phase 6+), move httpx to main dependencies:

```toml
[project]
dependencies = [
    ...
    "httpx>=0.27.0",  # LM Studio router
]
```

**Version 0.27.0+** (current) includes:
- Full `Timeout` granularity (connect, read, write, pool)
- Proper exception hierarchy
- Stable connection pooling

---

## Minimal Code Snippet: Singleton Router Client

For the LLM router module (`src/voice_commander/router.py`), this pattern:

```python
import httpx
import json
import logging
from typing import Optional

log = logging.getLogger(__name__)

# Singleton client, reused across utterances in a session
_ROUTER_CLIENT: Optional[httpx.Client] = None

def get_router_client() -> httpx.Client:
    """Return or create the singleton LM Studio client with keepalive."""
    global _ROUTER_CLIENT
    if _ROUTER_CLIENT is None:
        _ROUTER_CLIENT = httpx.Client(
            base_url="http://localhost:1234",
            limits=httpx.Limits(
                max_connections=2,
                max_keepalive_connections=2,
                keepalive_expiry=30.0,
            ),
            timeout=httpx.Timeout(
                connect=0.1,
                read=0.45,
                write=0.05,
            ),
        )
    return _ROUTER_CLIENT

def close_router_client() -> None:
    """Close and reset the singleton client (call on daemon shutdown)."""
    global _ROUTER_CLIENT
    if _ROUTER_CLIENT is not None:
        _ROUTER_CLIENT.close()
        _ROUTER_CLIENT = None

def route_utterance(text: str) -> Optional[dict]:
    """
    Send utterance to LM Studio for intent + args.
    
    Returns:
        Parsed JSON response, or None if any error (fallback to miss chime).
    
    Logs exact failure mode (timeout, connect, JSON) so ops can debug.
    """
    client = get_router_client()
    payload = {
        "model": "local-model",
        "messages": [{"role": "user", "content": text}],
        "temperature": 0.0,
    }
    
    try:
        response = client.post("/v1/chat/completions", json=payload)
        data = response.json()
        return data
    
    except httpx.ConnectError as e:
        log.error(f"Router: LM Studio not running or port closed: {e}")
        return None
    
    except httpx.ConnectTimeout as e:
        log.error(f"Router: TCP handshake timeout: {e}")
        return None
    
    except httpx.ReadTimeout as e:
        log.error(f"Router: LM inference timed out (600ms budget exceeded): {e}")
        return None
    
    except httpx.WriteTimeout as e:
        log.error(f"Router: Failed to send request: {e}")
        return None
    
    except httpx.RemoteProtocolError as e:
        log.error(f"Router: LM Studio sent invalid HTTP: {e}")
        return None
    
    except json.JSONDecodeError as e:
        log.error(f"Router: LM Studio returned invalid JSON: {e}")
        return None
    
    except httpx.HTTPError as e:
        log.error(f"Router: Unexpected httpx error: {e}")
        return None
```

---

## References

- [HTTPX Timeouts](https://www.python-httpx.org/advanced/timeouts/)
- [HTTPX Exceptions](https://www.python-httpx.org/exceptions/)
- [HTTPX Resource Limits](https://www.python-httpx.org/advanced/resource-limits/)
- [HTTPX vs Requests Comparison](https://oxylabs.io/blog/httpx-vs-requests-vs-aiohttp)
- [HTTPX QuickStart](https://www.python-httpx.org/quickstart/)

---

## Decision Log

**ADR:** LLM router phase (Phase 6+) will use `httpx.Client` (sync) for local LM Studio calls.

**Rationale:**
1. Modern HTTP library with granular timeout control (required for 600ms budget)
2. Built-in connection pooling + keepalive (efficient for rapid consecutive requests)
3. Structured exception types (connect vs. timeout vs. decode—caller can log and handle distinctly)
4. Already in dev dependencies; move to main when router is implemented
5. Drop-in replacement for `requests` (low migration risk for future microservice calls)

**Not chosen:** `requests` (timeout granularity poor; connection pooling requires manual setup; no clear error hierarchy).

---

**Last updated:** 2026-04-21  
**Next step:** When Phase 6 begins, promote httpx to main dependencies and implement `src/voice_commander/router.py`.
