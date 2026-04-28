from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from .feedback import FeedbackSink
from .observability.errors import Category, classify as _classify_error
from .plan import Plan, PlanOutcome, PlanStatus
from .registry import ToolRegistry

if TYPE_CHECKING:
    from .event_bus import EventBus
    from .observability import Tracer

logger = logging.getLogger(__name__)


class Dispatcher:
    """Execute a Plan of ToolCalls sequentially against the tool registry.

    Runs exclusively on the pipeline-worker thread (called from
    ``StreamingDaemon._process_utterance``). Each plan step is looked up
    in the :class:`ToolRegistry`, invoked with kwargs, and its outcome
    published to the :class:`EventBus` as ``tool_fired`` / ``tool_error``.

    When ``plan.strict`` is True (default), execution halts on the first
    failed step and the plan outcome records the failure index. When False,
    errors are recorded but remaining steps continue (best-effort mode).
    The :class:`FeedbackSink` is notified on plan start, completion, and
    per-step errors for audio/visual feedback.
    """

    def __init__(
        self,
        feedback: FeedbackSink,
        event_bus: EventBus | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._feedback = feedback
        self._event_bus = event_bus
        self._tracer = tracer

    def _publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data)

    def run_plan(self, transcript: str, plan: Plan, registry: ToolRegistry) -> PlanOutcome:
        """Execute a multi-step plan from the LLM router.

        If ``plan.strict`` is True (default), execution halts on the first failed
        step. If False, errors are recorded but remaining steps continue to run;
        only the first failure is captured in the published ``plan_outcome`` event.
        """
        tracer = self._tracer
        _plan_ctx = (
            tracer.span("plan", name="plan", n_steps=len(plan.steps))
            if tracer is not None and getattr(tracer, "enabled", False)
            else contextlib.nullcontext()
        )
        with _plan_ctx as plan_span:
            self._feedback.on_plan_start(transcript, len(plan.steps))
            start_s = time.perf_counter()
            executed = 0
            total = len(plan.steps)
            status: PlanStatus = "ok"
            failed_index: int | None = None
            error_msg: str | None = None

            for i, step in enumerate(plan.steps):
                tool = registry.by_name(step.name)
                if tool is None:
                    self._feedback.on_error(
                        f"plan:unknown_tool:{step.name}",
                        ValueError(f"Tool '{step.name}' not found in registry"),
                    )
                    self._publish(
                        "tool_error",
                        {
                            "name": step.name,
                            "msg": "unknown tool",
                            "error_category": Category.WIRING,
                        },
                    )
                    if failed_index is None:
                        status = "error"
                        failed_index = i
                        error_msg = f"unknown tool: {step.name}"
                    if plan.strict:
                        break
                    continue
                logger.info(
                    "plan step %d/%d: %s(%s)",
                    i + 1,
                    total,
                    step.name,
                    ", ".join(f"{k}={v!r}" for k, v in step.kwargs.items()),
                )
                _step_ctx = (
                    tracer.span("tool_call", name=step.name, **step.kwargs)
                    if tracer is not None and getattr(tracer, "enabled", False)
                    else contextlib.nullcontext()
                )
                with _step_ctx as step_span:
                    try:
                        ret = tool.func(**step.kwargs)
                        if step_span is not None and hasattr(step_span, "set_output"):
                            step_span.set_output(ret)
                        self._publish("tool_fired", {"name": step.name})
                    except Exception as e:
                        cat = _classify_error(e, where="dispatcher")
                        if step_span is not None and hasattr(step_span, "set_error_category"):
                            step_span.set_error_category(cat)
                        self._feedback.on_error(f"plan:step:{step.name}", e)
                        self._publish("tool_error", {"name": step.name, "msg": str(e), "error_category": cat})
                        if failed_index is None:
                            status = "error"
                            failed_index = i
                            error_msg = f"{type(e).__name__}: {e}"
                        if plan.strict:
                            break
                        continue
                executed += 1
                if tool.settle_ms > 0:
                    time.sleep(tool.settle_ms / 1000.0)

            self._feedback.on_plan_complete(transcript, executed)

            outcome = PlanOutcome(
                transcript=transcript,
                steps=plan.steps,
                status=status,
                failed_step_index=failed_index,
                error_msg=error_msg,
                duration_ms=int((time.perf_counter() - start_s) * 1000),
            )
            if plan_span is not None and hasattr(plan_span, "set_output"):
                plan_span.set_output({"status": outcome.status, "executed": executed})
            self._publish("plan_outcome", outcome.to_event_dict())
            return outcome
