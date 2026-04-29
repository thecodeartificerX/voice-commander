"""Regression test for B-C1: Tracer.write_run_end populates error_category.

When a tool span sets ``error_category`` (via ``set_error_category``), the
run row's ``error_category`` and ``error_summary`` columns must reflect the
deepest categorized span — so the SPA's runs panel can show why a run failed
without reading every span.
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


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def test_run_error_category_rolls_up_from_span(store: Store, bus: EventBus) -> None:
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("blow up") as run:
        with pytest.raises(ValueError):
            with tracer.span("tool_call", name="focus") as span:
                span.set_error_category("program")
                raise ValueError("no such window")
    store.flush()

    row = store.get_run(run.run_id)
    assert row is not None
    assert row["error_category"] == "program"
    # error_summary should equal the span's error message.
    assert row["error_summary"] == "no such window"
    assert row["status"] == "error"


def test_run_error_category_picks_deepest(store: Store, bus: EventBus) -> None:
    """When multiple spans set error_category, the deepest one wins."""
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("nested error") as run:
        with pytest.raises(RuntimeError):
            with tracer.span("dispatch") as outer:
                outer.set_error_category("wiring")
                with tracer.span("tool_call", name="click") as inner:
                    inner.set_error_category("program")
                    raise RuntimeError("boom")
    store.flush()

    row = store.get_run(run.run_id)
    assert row is not None
    # Inner span (program) is deeper than outer (wiring).
    assert row["error_category"] == "program"
    assert row["error_summary"] == "boom"


def test_run_error_category_none_when_no_categorized_span(
    store: Store, bus: EventBus
) -> None:
    """Successful run leaves error_category NULL."""
    tracer = Tracer(store=store, bus=bus, enabled=True)
    with tracer.run("fine"):
        with tracer.span("tool_call", name="click"):
            pass
    store.flush()

    last = store.get_last_run()
    assert last is not None
    assert last["error_category"] is None
    assert last["error_summary"] is None
