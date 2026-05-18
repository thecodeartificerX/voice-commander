"""Integration tests for the sync-to-async queue bridge."""

from __future__ import annotations

import asyncio
import queue

from voice_commander.dictation_stream.bridge import pump


async def test_pump_moves_items_then_forwards_sentinel():
    sync_q: queue.Queue = queue.Queue()
    async_q: asyncio.Queue = asyncio.Queue()
    sync_q.put(b"one")
    sync_q.put(b"two")
    sync_q.put(None)

    await pump(sync_q, async_q)

    assert await async_q.get() == b"one"
    assert await async_q.get() == b"two"
    assert await async_q.get() is None
    assert async_q.empty()


async def test_pump_waits_for_late_items():
    sync_q: queue.Queue = queue.Queue()
    async_q: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(pump(sync_q, async_q))

    await asyncio.sleep(0.05)  # pump is blocked waiting on an empty sync queue
    assert not task.done()

    sync_q.put(b"late")
    sync_q.put(None)
    await task
    assert await async_q.get() == b"late"
    assert await async_q.get() is None
