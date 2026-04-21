from __future__ import annotations

import logging
import time

from .feedback import FeedbackSink
from .plan import Plan
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, feedback: FeedbackSink) -> None:
        self._feedback = feedback

    def run_plan(self, transcript: str, plan: Plan, registry: ToolRegistry) -> None:
        """Execute a multi-step plan from the LLM router."""
        self._feedback.on_plan_start(transcript, len(plan.steps))
        executed = 0
        for step in plan.steps:
            tool = registry.by_name(step.name)
            if tool is None:
                self._feedback.on_error(
                    f"plan:unknown_tool:{step.name}",
                    ValueError(f"Tool '{step.name}' not found in registry"),
                )
                break
            try:
                tool.func(**step.kwargs)
            except Exception as e:
                self._feedback.on_error(f"plan:step:{step.name}", e)
                break
            executed += 1
            if tool.settle_ms > 0:
                time.sleep(tool.settle_ms / 1000.0)
        self._feedback.on_plan_complete(transcript, executed)
