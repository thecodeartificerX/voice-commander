from __future__ import annotations

import logging
import time as _time
from pathlib import Path

import pytest

from voice_commander.event_bus import EventBus
from voice_commander.observability.store import Store
from voice_commander.observability.tracer import Tracer


def _drain(store: Store) -> None:
    store.flush()


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


def test_tracer_nested_spans_have_correct_parent_ids(store: Store, bus: EventBus):
    """3-level nesting: run → transcribe → tool_call. Each child must reference its parent."""
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("nested test") as run:
        with tracer.span("transcribe", name="transcribe") as transcribe_span:
            with tracer.span("tool_call", name="focus") as tool_span:
                pass
    _drain(store)

    spans = store.get_spans(run.run_id)
    by_type = {s["type"]: s for s in spans}

    assert "run" in by_type, "missing root run span"
    assert "transcribe" in by_type, "missing transcribe span"
    assert "tool_call" in by_type, "missing tool_call span"

    root_span = by_type["run"]
    transcribe = by_type["transcribe"]
    tool = by_type["tool_call"]

    # transcribe must parent under root run span
    assert transcribe["parent_span_id"] == root_span["span_id"], (
        f"transcribe.parent_span_id={transcribe['parent_span_id']!r} "
        f"!= root.span_id={root_span['span_id']!r}"
    )
    # tool_call must parent under transcribe
    assert tool["parent_span_id"] == transcribe_span.span_id, (
        f"tool_call.parent_span_id={tool['parent_span_id']!r} "
        f"!= transcribe.span_id={transcribe_span.span_id!r}"
    )


def test_tracer_context_var_reset_after_exception_in_nested_span(store: Store, bus: EventBus):
    """After an exception in a nested span, the parent span_id context var is restored."""
    tracer = Tracer(store=store, bus=bus, enabled=True)
    outer_span_id_after: list[str] = []

    with tracer.run("exception test") as run:
        with tracer.span("outer", name="outer") as outer:
            try:
                with tracer.span("inner", name="inner"):
                    raise ValueError("boom")
            except ValueError:
                pass
            # After inner span's context exits, current span_id should be outer
            from voice_commander.observability.tracer import _current_span_id
            outer_span_id_after.append(_current_span_id.get())
    _drain(store)

    assert outer_span_id_after[0] == outer.span_id, (
        f"expected outer.span_id={outer.span_id!r} after inner exception, "
        f"got {outer_span_id_after[0]!r}"
    )


def test_tracer_step_counter_increments_per_tool_call(store: Store, bus: EventBus):
    """_step_counters increments once per tool_call span and is cleaned up after run ends."""
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("count steps") as run:
        with tracer.span("tool_call", name="press"):
            pass
        with tracer.span("tool_call", name="type"):
            pass
        # step counters exist during the run
        assert tracer._step_counters.get(run.run_id, 0) == 2, (
            f"expected 2 steps during run, got {tracer._step_counters.get(run.run_id, 0)}"
        )

    # after run exits, step_counters entry must be cleaned up to prevent memory leak
    assert run.run_id not in tracer._step_counters, (
        f"_step_counters still has entry for {run.run_id!r} after run ended"
    )
