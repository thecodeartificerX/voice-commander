"""Shared in-process mock WebSocket server for dictation integration tests.

Implements the ADR 0096 server contract exactly:

- Accept binary WebSocket frames (raw float32 PCM bytes) — accumulate them.
- Ignore JSON frames other than ``{"type":"end"}``.
- On ``{"type":"end"}``: send ONE reply frame and close:
  - ``{"type":"done","text":<done_text>,"raw":<raw_text>}`` — happy path.
  - ``{"type":"error","message":<error_message>}`` — error path.
  - Nothing (drop) if ``drop_after_end=True`` — tests the None-return path.
- Configurable via constructor:
  - ``done_text``      — text field in the done frame (default ``""``)
  - ``raw_text``       — raw field in the done frame (default: same as done_text)
  - ``error_message``  — if set, replies with an error frame instead of done
  - ``drop_after_end`` — if True, closes without replying (tests timeout/disconnect path)
  - ``delay_done_s``   — wait this many seconds before sending the done reply

NO partial frames. NO segments. NO config-frame handling. NO raw_transcript_fn.
"""

from __future__ import annotations

import asyncio
import json
import threading


class MockWsServer:
    """An in-process websockets server on a background asyncio loop thread.

    Use as a context manager (``with MockWsServer(...) as srv:``) or call
    ``.start()`` / ``.stop()`` directly.  ``.ws_url`` is the bound ws:// URL
    once started; ``.port`` is the bound TCP port.

    Per-connection state (reset on ``start()``)::

        chunks_received : list[bytes]  — all binary frames received, in order
        end_received    : bool         — True once {"type":"end"} was received

    Configurable constructor arguments::

        done_text       : str           — text field in the done frame (default "")
        raw_text        : str | None    — raw field in the done frame; defaults to done_text
        error_message   : str | None    — if set, send error instead of done
        drop_after_end  : bool          — if True, close without replying
        delay_done_s    : float         — seconds to wait before sending the reply
    """

    def __init__(
        self,
        done_text: str = "",
        raw_text: str | None = None,
        error_message: str | None = None,
        drop_after_end: bool = False,
        delay_done_s: float = 0.0,
        timings: "dict | None" = None,
    ) -> None:
        self._done_text = done_text
        self._raw_text = raw_text if raw_text is not None else done_text
        self._error_message = error_message
        self._drop_after_end = drop_after_end
        self._delay_done_s = delay_done_s
        # Optional per-phase timing dict to include in the done frame (ADR 0101).
        # When None (default), the 'timings' key is omitted — simulates an older
        # server that does not report timing data.
        self._timings = timings

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self._server = None
        self.ws_url = ""
        self.port: int = 0

        # Per-connection state — reset in _reset_state()
        self.chunks_received: list[bytes] = []
        self.end_received: bool = False

    def _reset_state(self) -> None:
        self.chunks_received = []
        self.end_received = False

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.run_forever()

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve

        async def handler(conn):
            async for message in conn:
                if isinstance(message, (bytes, bytearray)):
                    # Accumulate raw PCM bytes — opaque to the mock
                    self.chunks_received.append(bytes(message))
                elif isinstance(message, str):
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue  # silently ignore malformed JSON

                    if data.get("type") == "end":
                        self.end_received = True

                        if self._delay_done_s > 0.0:
                            await asyncio.sleep(self._delay_done_s)

                        if self._drop_after_end:
                            # Close without replying — tests the None-return path
                            break

                        if self._error_message is not None:
                            reply = json.dumps(
                                {"type": "error", "message": self._error_message}
                            )
                        else:
                            done_frame: dict = {
                                "type": "done",
                                "text": self._done_text,
                                "raw": self._raw_text,
                            }
                            if self._timings is not None:
                                done_frame["timings"] = self._timings
                            reply = json.dumps(done_frame)
                        await conn.send(reply)
                        # Server closes after sending the single reply
                        break
                    # Any other JSON frame (e.g. unexpected) — silently ignore;
                    # the new daemon does NOT send config frames.

        self._server = await serve(handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        self.ws_url = f"ws://127.0.0.1:{self.port}"
        self._ready.set()

    def start(self) -> "MockWsServer":
        """Start the server; return self so it can be used in a ``with`` statement."""
        self._reset_state()
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "MockWsServer did not start within 5 s"
        return self

    async def _shutdown(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def stop(self) -> None:
        """Close the server cleanly."""
        fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        fut.result(timeout=10.0)
        self._loop.call_soon_threadsafe(self._loop.stop)

    def __enter__(self) -> "MockWsServer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def url(self) -> str:
        """Alias for ws_url — kept for backward compat with existing callers."""
        return self.ws_url

    @property
    def chunk_count(self) -> int:
        """Number of binary frames received (one per handle_utterance call)."""
        return len(self.chunks_received)

    @property
    def bytes_received(self) -> int:
        """Total bytes received across all binary frames."""
        return sum(len(c) for c in self.chunks_received)
