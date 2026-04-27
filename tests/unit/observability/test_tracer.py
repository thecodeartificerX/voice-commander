from __future__ import annotations

import logging
import time as _time
from pathlib import Path

import pytest

from voice_commander.event_bus import EventBus
from voice_commander.observability.store import Store
from voice_commander.observability.tracer import Tracer


def _drain(store: Store) -> None:
    while not store._q.empty():
        _time.sleep(0.01)
    _time.sleep(0.05)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=99)
    s.start()
    yield s
    s.stop()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def test_tracer_run_and_one_span(store: Store, bus: EventBus):
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with (
        tracer.run("hello world") as run,
        tracer.span("transcribe", name="transcribe", confidence=0.9),
    ):
        pass
    _drain(store)

    persisted = store.get_run(run.run_id)
    assert persisted["status"] == "ok"
    spans = store.get_spans(run.run_id)
    types = [s["type"] for s in spans]
    assert "run" in types and "transcribe" in types


def test_tracer_captures_exception_in_span(store: Store, bus: EventBus):
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with (
        tracer.run("blow up") as run,
        pytest.raises(ValueError),
        tracer.span("tool_call", name="focus", target="chrome"),
    ):
        raise ValueError("no such window")
    _drain(store)

    spans = store.get_spans(run.run_id)
    err_spans = [s for s in spans if s["status"] == "error"]
    assert len(err_spans) == 1
    assert err_spans[0]["error_type"] == "ValueError"
    assert err_spans[0]["error_msg"] == "no such window"
    assert err_spans[0]["traceback"]
    run_row = store.get_run(run.run_id)
    assert run_row["status"] == "error"


def test_tracer_noop_when_disabled(store: Store, bus: EventBus):
    tracer = Tracer(store=store, bus=bus, enabled=False)
    with tracer.run("test") as run, tracer.span("tool_call", name="click"):
        pass
    # No writes should occur
    _drain(store)
    assert store.get_run(run.run_id) is None


def test_tracer_publishes_events(store: Store, bus: EventBus):
    q = bus.subscribe()
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("test") as run, tracer.span("tool_call", name="click"):
        pass
    _drain(store)

    # Drain all events from the queue
    events = []
    while not q.empty():
        events.append(q.get_nowait())

    span_end_events = [e for e in events if e.type == "trace.span_ended"]
    assert any(e.data.get("run_id") == run.run_id for e in span_end_events)


def test_tracer_emits_oneline_summary_at_end_of_run(store, bus, caplog):
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with (
        caplog.at_level(logging.INFO, logger="voice_commander.observability.tracer"),
        tracer.run("open chrome") as run,
        tracer.span("plan", name="plan"),
        tracer.span("tool_call", name="focus"),
    ):
        pass
    summaries = [r for r in caplog.records if "run " in r.message]
    assert summaries, "expected one-line run summary in INFO log"
    msg = summaries[-1].message
    assert run.run_id[:6] in msg, f"run_id prefix not in log message: {msg!r}"
    assert "ok" in msg, f"'ok' not in log message: {msg!r}"
    assert "open chrome" in msg, f"transcript not in log message: {msg!r}"
