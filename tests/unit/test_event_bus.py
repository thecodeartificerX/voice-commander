from __future__ import annotations

import asyncio

from voice_commander.event_bus import Event, EventBus


def _drain(q: asyncio.Queue[Event]) -> list[Event]:
    """Drain all events from an asyncio.Queue synchronously."""
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


def test_publish_and_subscribe():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("session_started")
    events = _drain(q)
    assert len(events) == 1
    assert events[0].type == "session_started"
    assert events[0].id == 1


def test_publish_with_data():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("tool_fired", {"name": "copy"})
    events = _drain(q)
    assert events[0].data == {"name": "copy"}


def test_multiple_subscribers():
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    bus.publish("miss")
    assert _drain(q1)[0].type == "miss"
    assert _drain(q2)[0].type == "miss"


def test_unsubscribe():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish("miss")
    assert _drain(q) == []


def test_monotonic_ids():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("a")
    bus.publish("b")
    bus.publish("c")
    events = _drain(q)
    assert [e.id for e in events] == [1, 2, 3]


def test_ring_buffer_replay():
    bus = EventBus(max_buffer=5)
    q = bus.subscribe()
    for i in range(10):
        bus.publish(f"e{i}")
    _drain(q)  # flush subscriber queue
    replayed = bus.replay_after(7)
    assert [e.id for e in replayed] == [8, 9, 10]


def test_subscriber_queue_overflow_drops_oldest():
    bus = EventBus(max_subscriber_queue=2)
    q = bus.subscribe()
    bus.publish("a")
    bus.publish("b")
    bus.publish("c")  # should drop "a"
    events = _drain(q)
    assert len(events) == 2
    assert events[0].type == "b"
    assert events[1].type == "c"
    assert bus.events_dropped == 1


def test_no_subscribers_no_error():
    bus = EventBus()
    bus.publish("orphan")  # should not raise


def test_replay_after_zero_returns_all_buffered():
    bus = EventBus(max_buffer=100)
    bus.publish("x")
    bus.publish("y")
    replayed = bus.replay_after(0)
    assert len(replayed) == 2
