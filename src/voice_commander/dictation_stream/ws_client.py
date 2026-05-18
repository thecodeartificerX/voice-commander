"""WebSocket client for the streaming whisper transcription endpoint.

Connects to the confirmed-live ``/ws/transcribe`` endpoint, sends a one-time
JSON ``config`` handshake, streams binary WAV chunks, and routes the server's
``partial`` / ``error`` JSON replies. The protocol is request/reply: one
binary chunk out, one reply in. The server carries ``initial_prompt`` context
across chunks itself — the client sends no prompt.

See `docs/references/websockets.md` for the library API and spec section 8 for
the frame contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

# Sentinel placed on the chunk queue to signal end-of-session.
END: None = None


async def stream_transcribe(
    ws_url: str,
    language: str,
    chunk_q: "asyncio.Queue[bytes | None]",
    on_partial: Callable[[str], None],
    idle_timeout_s: float,
) -> None:
    """Stream WAV chunks to the server; route partials to ``on_partial``.

    Returns when the ``END`` sentinel is dequeued, the idle timeout elapses, or
    the server sends an ``error`` frame. Propagates any exception raised by
    ``connect()`` itself (``OSError`` / ``InvalidURI`` / ``InvalidHandshake``)
    if the initial connection fails.
    """
    async with connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "config", "language": language}))
        while True:
            try:
                chunk = await asyncio.wait_for(chunk_q.get(), timeout=idle_timeout_s)
            except asyncio.TimeoutError:
                logger.info("stream_transcribe: idle %.1fs — ending session", idle_timeout_s)
                break
            if chunk is END:
                break
            try:
                await ws.send(chunk)
            except ConnectionClosed:
                logger.warning("stream_transcribe: connection closed before chunk send")
                return
            try:
                reply = json.loads(await ws.recv())
            except ConnectionClosed:
                logger.warning("stream_transcribe: connection closed by server")
                return
            kind = reply.get("type")
            if kind == "partial":
                try:
                    on_partial(reply.get("text", ""))
                except Exception:  # noqa: BLE001 - a bad callback must not kill the stream
                    logger.exception("stream_transcribe: on_partial callback raised — continuing")
            elif kind == "error":
                logger.error("stream_transcribe: server error — %s", reply.get("detail"))
                break
            else:
                logger.warning("stream_transcribe: unexpected reply type %r", kind)
        try:
            await ws.send(json.dumps({"type": "end"}))
        except ConnectionClosed:
            logger.warning("stream_transcribe: connection closed before end frame")
