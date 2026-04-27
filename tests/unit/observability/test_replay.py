from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from voice_commander.observability.replay import ReplayResult, replay_llm
from voice_commander.observability.store import (
    RunRecord,
    RunUpdate,
    SpanRecord,
    Store,
)


def _drain(s: Store) -> None:
    while not s._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)


def _seed_run_with_llm(tmp_path: Path) -> Store:
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("aaa", 1000.0, "open chrome", 1))
    s.write_span(SpanRecord(
        span_id="root", run_id="aaa", parent_span_id=None,
        type="run", name="run", started_at=1000.0, ended_at=1000.5,
        duration_ms=500, status="ok",
    ))
    s.write_span(SpanRecord(
        span_id="llm", run_id="aaa", parent_span_id="root",
        type="llm_call", name="llm_call", started_at=1000.0, ended_at=1000.3,
        duration_ms=300, status="ok",
        attrs={
            "model": "old-model", "endpoint_url": "http://x",
            "prompt_full": "[ system prompt ]", "transcript": "open chrome",
            "raw_response": {"choices": [{"message": {"tool_calls": []}}]},
        },
        output={"steps": [{"name": "focus", "kwargs": {"target": "chrome"}}]},
    ))
    s.write_run_end(RunUpdate("aaa", 1000.5, "ok", None, 500))
    _drain(s)
    return s


def test_replay_llm_returns_diff(tmp_path: Path) -> None:
    s = _seed_run_with_llm(tmp_path)
    try:
        from voice_commander.plan import Plan, ToolCall
        router = MagicMock()
        router.route.return_value = Plan(
            steps=(ToolCall(name="press", kwargs={"key": "enter"}),),
            raw_response={},
        )
        result = replay_llm(s, "aaa", router)
        assert isinstance(result, ReplayResult)
        assert result.old_plan == [{"name": "focus", "kwargs": {"target": "chrome"}}]
        assert result.new_plan == [{"name": "press", "kwargs": {"key": "enter"}}]
        assert result.changed is True
    finally:
        s.stop()


def test_replay_llm_no_llm_span_returns_error(tmp_path: Path) -> None:
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("zzz", 1000.0, "no llm", 1))
    s.write_run_end(RunUpdate("zzz", 1000.1, "ok", None, 100))
    _drain(s)
    try:
        router = MagicMock()
        result = replay_llm(s, "zzz", router)
        assert result.error is not None
        assert result.changed is False
    finally:
        s.stop()


def test_replay_llm_unchanged_plan(tmp_path: Path) -> None:
    """When new plan equals old plan, changed should be False."""
    s = _seed_run_with_llm(tmp_path)
    try:
        from voice_commander.plan import Plan, ToolCall
        router = MagicMock()
        # Same plan as what was stored
        router.route.return_value = Plan(
            steps=(ToolCall(name="focus", kwargs={"target": "chrome"}),),
            raw_response={},
        )
        result = replay_llm(s, "aaa", router)
        assert result.changed is False
        assert result.error is None
    finally:
        s.stop()
