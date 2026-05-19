"""Shared in-process mock WebSocket server for dictation integration tests.

Replaces the old mock /inference HTTP endpoint. The server replies one
``partial`` frame per binary chunk; the partial texts are supplied per
session via the ``replies`` constructor argument.
"""

from __future__ import annotations

import asyncio
import json
import threading


class MockWsServer:
    """A websockets.serve server on a background asyncio loop thread.

    Use as a context manager or call .start()/.stop(). ``url`` is the bound
    ws:// URL once started. ``replies`` is the list of partial texts returned
    one-per-chunk (the last entry repeats if more chunks arrive).
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self._server = None
        self.url = ""
        self.configs: list[dict] = []
        self.chunk_count = 0

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.run_forever()

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve

        state = {"i": 0}

        async def handler(conn):
            async for message in conn:
                if isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "config":
                        self.configs.append(data)
                else:
                    self.chunk_count += 1
                    text = self._replies[min(state["i"], len(self._replies) - 1)]
                    state["i"] += 1
                    await conn.send(json.dumps({"type": "partial", "text": text}))

        self._server = await serve(handler, "localhost", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        self._ready.set()

    def start(self) -> str:
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "mock WS server did not start"
        return self.url

    async def _shutdown(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def stop(self) -> None:
        fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        fut.result(timeout=10.0)
        self._loop.call_soon_threadsafe(self._loop.stop)

    def __enter__(self) -> "MockWsServer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
