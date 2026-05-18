"""Bridge a synchronous queue to an asyncio queue.

The audio stack (sounddevice callback, chunker worker thread) is synchronous;
the WebSocket client is async. ``pump`` moves WAV chunks across the boundary
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
    """
    loop = asyncio.get_running_loop()
    while True:
        item = await loop.run_in_executor(None, sync_q.get)
        await async_q.put(item)
        if item is None:
            return
