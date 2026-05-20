"""WebSocket client for the server-side dictation endpoint (ADR 0096).

Protocol (raw PCM, server-side accumulation):

1. Connect to ``ws_url``. No config frame is sent.
2. For each audio chunk dequeued from ``chunk_q``: send as a binary WebSocket
   frame. Chunks are raw 16 kHz mono float32 PCM bytes — no WAV headers.
3. On the END sentinel (``None``) or when ``cap_timeout_s`` expires: send
   ``{"type":"end"}`` as a JSON text frame.
4. Await exactly one reply frame within ``done_timeout_s``:
   - ``{"type":"done","text":"<cleaned>"}`` — return ``done.text`` (str, may be "").
   - ``{"type":"error","message":"..."}`` — log and return ``None``.
   - Any other frame type — log and discard; keep waiting.
   - ``ConnectionClosed`` or timeout — log and return ``None``.

There are no partial frames, no config handshake, no segments, no
``on_partial`` callback, no ``prompt``, and no ``raw_transcript_fn``.

See `docs/decisions/0096-server-side-dictation.md` for the full protocol spec.
"""

from __future__ import annotations

import asyncio
import json
import logging

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

# Sentinel placed on the chunk queue to signal end-of-dictation.
END: None = None


async def stream_transcribe(
    ws_url: str,
    chunk_q: "asyncio.Queue[bytes | None]",
    cap_timeout_s: float = 300.0,
    done_timeout_s: float = 60.0,
) -> str | None:
    """Stream raw PCM chunks to the server; return the server's cleaned transcript.

    Opens a WebSocket connection to ``ws_url`` and enters the upload loop.
    Each item dequeued from ``chunk_q`` is sent as a binary frame unless it is
    the ``END`` sentinel (``None``), which triggers the end-frame send. If the
    daemon-side cap ``cap_timeout_s`` fires before the sentinel arrives, the
    upload loop also breaks and the end frame is sent.

    After the end frame is sent, the function waits up to ``done_timeout_s``
    for the server's single ``done`` reply.

    Parameters
    ----------
    ws_url:
        WebSocket URL of the transcription endpoint (e.g. ``ws://host:port/ws/transcribe``).
    chunk_q:
        Async queue of raw float32 PCM byte chunks. Push ``None`` to signal
        end-of-dictation. Must be pre-populated or written concurrently.
    cap_timeout_s:
        Daemon-side hard cap on the upload phase. When ``chunk_q.get()`` has
        not returned within this many seconds, the upload loop exits and the
        end frame is sent. Default: 300 s (5 minutes). The server has its own
        600 s cap.
    done_timeout_s:
        How long to wait for the server's ``done`` reply after sending the end
        frame. Covers Whisper + LLM latency. Default: 60 s.

    Returns
    -------
    str
        ``done.text`` from the server's done frame. May be an empty string
        when the server transcribed silence — this is a valid, non-error result.
    None
        Any failure path: connect error propagated by the caller, send error,
        recv timeout, ``ConnectionClosed``, server error frame, or unexpected
        frame type.

    Raises
    ------
    OSError, websockets.exceptions.InvalidURI, websockets.exceptions.InvalidHandshake
        Raised by ``connect()`` when the initial connection fails. The caller
        is responsible for handling these (e.g. ``DictationSession._async_main``
        catches ``OSError`` and records ``error="endpoint"``).
    """
    async with connect(ws_url) as ws:
        # --- upload loop ---
        # Read chunks from the queue and forward as binary frames.
        # Exit when: (a) END sentinel arrives, or (b) cap_timeout_s fires.
        while True:
            try:
                chunk = await asyncio.wait_for(chunk_q.get(), timeout=cap_timeout_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "stream_transcribe: daemon cap %.1fs reached — sending end frame",
                    cap_timeout_s,
                )
                break
            if chunk is END:
                break
            try:
                await ws.send(chunk)
            except ConnectionClosed:
                logger.warning("stream_transcribe: connection closed during chunk send")
                return None

        # --- end frame ---
        try:
            await ws.send(json.dumps({"type": "end"}))
        except ConnectionClosed:
            logger.warning("stream_transcribe: connection closed before end frame could be sent")
            return None

        # --- done-wait loop ---
        # The server sends exactly ONE reply frame: done or error.
        # Any unexpected frame type is logged and discarded; we keep waiting.
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=done_timeout_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "stream_transcribe: timed out waiting for done frame (%.1fs)",
                    done_timeout_s,
                )
                return None
            except ConnectionClosed:
                logger.warning("stream_transcribe: connection closed while waiting for done frame")
                return None

            try:
                frame = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("stream_transcribe: non-JSON frame received while waiting for done")
                return None

            kind = frame.get("type")
            if kind == "done":
                return frame.get("text", "")
            elif kind == "error":
                logger.error(
                    "stream_transcribe: server error frame — %s",
                    frame.get("message", frame.get("detail", "<no message>")),
                )
                return None
            else:
                logger.warning(
                    "stream_transcribe: unexpected frame type %r while waiting for done — discarding",
                    kind,
                )
                # Keep waiting; the server may still send done after an unexpected frame.
