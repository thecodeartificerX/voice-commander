"""Bridge a synchronous queue to an asyncio queue.

The daemon's dictation pipeline pushes WAV chunks from synchronous threads
(the VAD pipeline worker, via ``DictationSession.handle_utterance``); the
WebSocket client is async. ``pump`` moves WAV chunks across the boundary
without blocking the event loop, running the blocking ``queue.Queue.get`` in
the default executor.
"""

from __future__ import annotations

import asyncio
import queue


async def pump(
    sync_q: "queue.Queue[bytes | None]",
    async_q: "asyncio.Queue[bytes | None]",
) -> None:
    """Move items from ``sync_q`` to ``async_q`` until a ``None`` is seen.

    The terminating ``None`` is forwarded onto ``async_q`` so the consumer also
    observes end-of-stream.

    Cancellation: if this coroutine is cancelled while blocked in
    ``run_in_executor``, the underlying ``sync_q.get`` call cannot be
    interrupted — the executor thread keeps blocking until a ``None`` or any
    item is placed on ``sync_q``. Callers are responsible for ensuring a
    sentinel arrives promptly after cancellation (``DictationSession.finish``
    and ``DictationSession.cancel`` guarantee this by pushing the ``None``
    end sentinel onto the chunk queue). ``async_q`` must be unbounded (the
    default ``maxsize=0``) so ``async_q.put`` never blocks the pump.
    """
    loop = asyncio.get_running_loop()
    while True:
        item = await loop.run_in_executor(None, sync_q.get)
        await async_q.put(item)
        if item is None:
            return
