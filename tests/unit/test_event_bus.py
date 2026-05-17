from __future__ import annotations

import queue

from voice_commander.event_bus import Event, EventBus, TRANSIENT_EVENT_TYPES


def _drain(q: queue.Queue[Event]) -> list[Event]:
    """Drain all events from a queue.Queue synchronously."""
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


def test_subscribe_with_replay_atomic():
    bus = EventBus(max_buffer=100)
    bus.publish("a")
    bus.publish("b")
    bus.publish("c")
    q, replay = bus.subscribe_with_replay(1)
    # Should get events 2 and 3 in replay
    assert [e.id for e in replay] == [2, 3]
    # New events after subscribe should arrive in queue
    bus.publish("d")
    events = _drain(q)
    assert len(events) == 1
    assert events[0].type == "d"


# ---------------------------------------------------------------------------
# Transient-event replay exclusion (Bug 2 fix)
# ---------------------------------------------------------------------------


def test_transient_events_not_in_replay_buffer():
    """elements.show and elements.hide must not enter the replay ring buffer."""
    bus = EventBus(max_buffer=100)

    # Publish a mix of durable and transient events.
    bus.publish("session_started")
    bus.publish("elements.show", {"monitor": [0, 0, 1920, 1080], "elements": []})
    bus.publish("elements.hide", {})
    bus.publish("muted")

    # A late subscriber replaying from id=0 should only see the durable events.
    _q, replay = bus.subscribe_with_replay(0)
    replay_types = [e.type for e in replay]
    assert "elements.show" not in replay_types
    assert "elements.hide" not in replay_types
    assert "session_started" in replay_types
    assert "muted" in replay_types


def test_transient_events_delivered_live():
    """elements.show / elements.hide must still reach currently-connected
    subscribers even though they are excluded from the replay buffer."""
    bus = EventBus(max_buffer=100)

    # Subscribe BEFORE publishing so we are a live subscriber.
    q = bus.subscribe()

    bus.publish("elements.show", {"monitor": [0, 0, 1920, 1080], "elements": []})
    bus.publish("elements.hide", {})

    events = _drain(q)
    live_types = [e.type for e in events]
    assert "elements.show" in live_types
    assert "elements.hide" in live_types


def test_transient_event_types_set_contains_expected():
    """Sanity-check the exported constant used by callers."""
    assert "elements.show" in TRANSIENT_EVENT_TYPES
    assert "elements.hide" in TRANSIENT_EVENT_TYPES
