from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .feedback import FeedbackSink
from .plan import Plan, PlanOutcome, PlanStatus
from .registry import ToolRegistry

if TYPE_CHECKING:
    from .event_bus import EventBus

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        feedback: FeedbackSink,
        event_bus: EventBus | None = None,
    ) -> None:
        self._feedback = feedback
        self._event_bus = event_bus

    def _publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data)

    def run_plan(self, transcript: str, plan: Plan, registry: ToolRegistry) -> None:
        """Execute a multi-step plan from the LLM router.

        If ``plan.strict`` is True (default), execution halts on the first failed
        step. If False, errors are recorded but remaining steps continue to run;
        only the first failure is captured in the published ``plan_outcome`` event.
        """
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
                self._publish("tool_error", {"name": step.name, "msg": "unknown tool"})
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
            try:
                tool.func(**step.kwargs)
                self._publish("tool_fired", {"name": step.name})
            except Exception as e:
                self._feedback.on_error(f"plan:step:{step.name}", e)
                self._publish("tool_error", {"name": step.name, "msg": str(e)})
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
        self._publish("plan_outcome", outcome.to_event_dict())
