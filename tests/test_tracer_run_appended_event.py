"""Regression test for B-C3: Tracer emits ``run.appended`` SSE event.

The SPA's runs panel subscribes to ``run.appended`` for live tailing.
Without this event, the panel only refreshes on a manual reload.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_commander.event_bus import EventBus
from voice_commander.observability.store import Store
from voice_commander.observability.tracer import Tracer


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=99)
    s.start()
    yield s
    s.stop()


def _drain(bus: EventBus, q) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    while True:
        try:
            evt = q.get_nowait()
        except Exception:
            break
        out.append((evt.type, evt.data))
    return out


def test_run_appended_emitted_on_run_end(store: Store) -> None:
    bus = EventBus()
    q = bus.subscribe()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    with tracer.run("hello") as run:
        pass
    store.flush()

    events = _drain(bus, q)
    event_names = [name for name, _ in events]
    assert "trace.run_completed" in event_names
    assert "run.appended" in event_names

    appended = next(d for name, d in events if name == "run.appended")
    assert appended["run_id"] == run.run_id
    assert appended["transcript"] == "hello"
    assert appended["status"] == "ok"
    assert "started_at" in appended
    assert "ended_at" in appended
    assert "duration_ms" in appended
    assert appended["error_category"] is None
    assert appended["error_summary"] is None


def test_run_appended_carries_error_category(store: Store) -> None:
    bus = EventBus()
    q = bus.subscribe()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    with tracer.run("fail") as run:
        with pytest.raises(ValueError):
            with tracer.span("tool_call", name="x") as span:
                span.set_error_category("program")
                raise ValueError("nope")
    store.flush()

    events = _drain(bus, q)
    appended = next(d for name, d in events if name == "run.appended")
    assert appended["run_id"] == run.run_id
    assert appended["status"] == "error"
    assert appended["error_category"] == "program"
    assert appended["error_summary"] == "nope"
