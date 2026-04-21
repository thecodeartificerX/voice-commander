from __future__ import annotations

import logging
import time

from .feedback import FeedbackSink
from .matcher import MatchResult
from .plan import Plan
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, feedback: FeedbackSink) -> None:
        self._feedback = feedback

    def dispatch(self, transcript: str, match: MatchResult) -> None:
        if match.tool is None or match.phrase is None:
            self._feedback.on_miss(transcript, match.candidates)
            return
        self._feedback.on_match(match.tool.name, match.phrase, match.score)
        try:
            match.tool.func()
        except Exception as e:
            self._feedback.on_error(f"tool:{match.tool.name}", e)

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
