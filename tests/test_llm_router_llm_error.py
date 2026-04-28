"""Test LLMPlanError is defined and classifies as 'llm'."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest


def test_llm_plan_error_defined():
    from voice_commander.llm_router import LLMPlanError
    exc = LLMPlanError("bad JSON")
    assert str(exc) == "bad JSON"


def test_llm_plan_error_classifies_as_llm():
    from voice_commander.llm_router import LLMPlanError
    from voice_commander.observability.errors import classify
    exc = LLMPlanError("malformed plan")
    assert classify(exc, where="llm_router") == "llm"


def _make_router_with_response(response_data: Any, monkeypatch):
    """Helper: build an LLMRouter with a stubbed httpx client returning *response_data*."""
    from voice_commander.config import LLMConfig
    from voice_commander.llm_router import LLMRouter
    from voice_commander.registry import ToolEntry, ToolRegistry

    cfg = LLMConfig(
        endpoint_url="http://127.0.0.1:9999/v1",
        model_id="test",
        timeout_ms=5000,
        warmup_on_startup=False,
    )
    registry = ToolRegistry()
    # Need at least one llm-visible tool, otherwise route() short-circuits at
    # the empty-tools guard before reaching _parse_response.
    registry.register(
        ToolEntry(
            name="dummy",
            phrases=("dummy",),
            func=lambda: None,
            module="test",
            docstring="dummy",
            llm_only=True,
            params_schema={
                "type": "function",
                "function": {"name": "dummy", "description": "dummy"},
            },
        )
    )
    router = LLMRouter(cfg, registry, threading.Lock())

    class _Resp:
        status_code = 200
        text = "<stubbed>"

        def raise_for_status(self) -> None:
            pass

        def json(self) -> Any:
            return response_data

    class _Client:
        def post(self, *_a, **_kw):
            return _Resp()

        def close(self) -> None:
            pass

    router._client = _Client()  # type: ignore[assignment]
    return router


def test_route_raises_llm_plan_error_on_malformed_response(monkeypatch, tmp_path: Path):
    """B-H5: structurally malformed plans must propagate as LLMPlanError."""
    from voice_commander.llm_router import LLMPlanError

    # ``message`` is an int — triggers AttributeError inside _parse_response,
    # which raises LLMPlanError.
    bad_response = {"choices": [{"message": 42}]}
    router = _make_router_with_response(bad_response, monkeypatch)
    with pytest.raises(LLMPlanError):
        router.route("do something")


def test_run_row_has_llm_category_after_llm_error(tmp_path: Path, monkeypatch):
    """B-H5 daemon-integration: LLMPlanError → run.error_category == 'llm'.

    Drives the tracer's ``run`` context the same way the daemon does, lets
    the LLMPlanError propagate, and verifies the run row's error_category
    is set to ``llm`` via the deepest-span rollup.
    """
    from voice_commander.event_bus import EventBus
    from voice_commander.llm_router import LLMPlanError
    from voice_commander.observability.errors import classify
    from voice_commander.observability.store import Store
    from voice_commander.observability.tracer import Tracer

    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    store.start()
    try:
        bus = EventBus()
        tracer = Tracer(store=store, bus=bus, enabled=True)
        # ``message`` of int type triggers _parse_response's LLMPlanError path.
        bad_response = {"choices": [{"message": 42}]}
        router = _make_router_with_response(bad_response, monkeypatch)
        router.set_tracer(tracer)

        captured_exc: Exception | None = None
        with tracer.run("trigger llm error") as run:
            try:
                router.route("trigger llm error")
            except LLMPlanError as exc:
                # Daemon's _pipeline_loop catches and classifies — mimic that.
                captured_exc = exc
        store.flush()

        assert captured_exc is not None
        assert classify(captured_exc, where="daemon") == "llm"

        row = store.get_run(run.run_id)
        assert row is not None, "run row must exist"
        assert row["error_category"] == "llm", (
            f"expected error_category='llm', got {row['error_category']!r}"
        )
    finally:
        store.stop()
