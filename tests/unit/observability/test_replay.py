from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from voice_commander.event_bus import EventBus
from voice_commander.observability.replay import ReplayResult, replay_llm, replay_full
from voice_commander.observability.tracer import Tracer
from voice_commander.observability.store import (
    RunRecord,
    RunUpdate,
    SpanRecord,
    Store,
)


def _drain(s: Store) -> None:
    s.flush()


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


# ---------------------------------------------------------------------------
# replay_full() tests — DESTRUCTIVE: re-fires plan via dispatcher
# ---------------------------------------------------------------------------


def _make_tracer(store: Store) -> Tracer:
    return Tracer(store=store, bus=EventBus(), enabled=True)


def test_replay_full_happy_path(tmp_path: Path) -> None:
    """replay_full returns a new run_id when run and llm_call span exist."""
    from voice_commander.plan import Plan, ToolCall
    s = _seed_run_with_llm(tmp_path)
    try:
        tracer = _make_tracer(s)
        router = MagicMock()
        router.route.return_value = Plan(
            steps=(ToolCall(name="focus", kwargs={"target": "chrome"}),),
            raw_response={},
        )
        dispatcher = MagicMock()
        registry = MagicMock()

        new_id = replay_full(s, "aaa", router, dispatcher, registry, tracer)

        assert isinstance(new_id, str) and new_id  # non-empty run_id
        assert new_id != "aaa"  # distinct from original
        router.route.assert_called_once()
        dispatcher.run_plan.assert_called_once()
    finally:
        s.stop()


def test_replay_full_missing_run_raises(tmp_path: Path) -> None:
    """replay_full raises ValueError when run_id does not exist in the store."""
    import pytest
    s = _seed_run_with_llm(tmp_path)
    try:
        tracer = _make_tracer(s)
        router = MagicMock()
        dispatcher = MagicMock()
        registry = MagicMock()

        with pytest.raises(ValueError, match="not found"):
            replay_full(s, "no-such-run", router, dispatcher, registry, tracer)
    finally:
        s.stop()


def test_replay_full_no_llm_span_raises(tmp_path: Path) -> None:
    """replay_full raises ValueError when the run has no llm_call span."""
    import pytest
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("bbb", 2000.0, "hello", 1))
    s.write_run_end(RunUpdate("bbb", 2000.1, "ok", None, 100))
    _drain(s)
    try:
        tracer = _make_tracer(s)
        router = MagicMock()
        dispatcher = MagicMock()
        registry = MagicMock()

        with pytest.raises(ValueError, match="no llm_call span"):
            replay_full(s, "bbb", router, dispatcher, registry, tracer)
    finally:
        s.stop()


def test_replay_full_none_plan_skips_dispatch(tmp_path: Path) -> None:
    """replay_full does not call dispatcher when router returns None."""
    s = _seed_run_with_llm(tmp_path)
    try:
        tracer = _make_tracer(s)
        router = MagicMock()
        router.route.return_value = None  # LLM miss
        dispatcher = MagicMock()
        registry = MagicMock()

        new_id = replay_full(s, "aaa", router, dispatcher, registry, tracer)

        assert isinstance(new_id, str) and new_id
        dispatcher.run_plan.assert_not_called()  # no plan → no dispatch
    finally:
        s.stop()
