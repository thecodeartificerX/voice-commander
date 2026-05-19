"""WebSocket client for the streaming whisper transcription endpoint.

Connects to the confirmed-live ``/ws/transcribe`` endpoint, sends a one-time
JSON ``config`` handshake, streams binary WAV chunks, and routes the server's
``partial`` / ``error`` JSON replies. The protocol is request/reply: one
binary chunk out, one reply in. The config handshake carries an optional
``initial_prompt`` field that biases the whisper decoder toward the user's
custom vocabulary (built by ``postprocess.build_prompt``).

After the client sends the terminal ``{"type":"end"}`` frame, the proxy emits
**exactly one** ``done`` frame: ``{"type":"done","text":"<LLM-cleaned>","raw":"<raw-whisper>"}``.
``stream_transcribe`` reads that frame and returns ``done.text`` (the LLM-cleaned
transcript). The ``raw`` field is optional (the raw 8765 server omits it).
An empty ``done.text`` is valid — whisper heard nothing. If no ``done`` frame
arrives before *done_timeout_s* (e.g. timeout, connection closed, server error),
``stream_transcribe`` returns ``None`` and the caller falls back to
``LocalAgreement`` output.

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
    prompt: str = "",
    done_timeout_s: float = 15.0,
) -> str | None:
    """Stream WAV chunks to the server; route partials to ``on_partial``.

    Returns when the ``END`` sentinel is dequeued, the idle timeout elapses, or
    the server sends an ``error`` frame. Propagates any exception raised by
    ``connect()`` itself (``OSError`` / ``InvalidURI`` / ``InvalidHandshake``)
    if the initial connection fails.

    ``prompt`` (when non-empty) is sent in the config frame as the
    ``initial_prompt`` field so the server biases its decoder toward the
    user's custom vocabulary. An empty ``prompt`` omits the field entirely.

    After sending the terminal ``{"type":"end"}`` frame, this function reads
    frames until it receives the proxy's ``done`` frame, a server ``error``,
    or the *done_timeout_s* deadline expires. Any stray trailing ``partial``
    frames are forwarded through ``on_partial`` (defensive — the proxy promises
    only a single ``done`` follows ``end``, but a last partial must not break us).

    Return value:
    - ``str`` — the LLM-cleaned transcript from ``done.text`` when a ``done``
      frame arrived (may be an empty string if whisper heard nothing).
    - ``None`` — no ``done`` frame was received (timeout, connection closed,
      or server error); the caller should fall back to ``LocalAgreement`` output.
    """
    async with connect(ws_url) as ws:
        config: dict[str, str] = {"type": "config", "language": language}
        if prompt:
            config["initial_prompt"] = prompt
        await ws.send(json.dumps(config))
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
                return None
            try:
                reply = json.loads(await ws.recv())
            except ConnectionClosed:
                logger.warning("stream_transcribe: connection closed by server")
                return None
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
            return None

        # Read frames until the proxy's single ``done`` reply arrives.
        # Any stray trailing ``partial`` frames are forwarded defensively.
        # Timeout is bounded separately from idle_timeout_s to cover LLM latency.
        while True:
            try:
                frame = json.loads(
                    await asyncio.wait_for(ws.recv(), timeout=done_timeout_s)
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "stream_transcribe: timed out waiting for done frame (%.1fs)",
                    done_timeout_s,
                )
                return None
            except ConnectionClosed:
                logger.warning("stream_transcribe: connection closed while waiting for done frame")
                return None
            kind = frame.get("type")
            if kind == "done":
                return frame.get("text", "")
            elif kind == "partial":
                # Stray trailing partial — forward and keep waiting.
                try:
                    on_partial(frame.get("text", ""))
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "stream_transcribe: on_partial callback raised on trailing partial — continuing"
                    )
            elif kind == "error":
                logger.error(
                    "stream_transcribe: server error in done-read phase — %s",
                    frame.get("detail"),
                )
                return None
            else:
                logger.warning(
                    "stream_transcribe: unexpected frame type %r while waiting for done",
                    kind,
                )
