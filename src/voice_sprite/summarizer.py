"""Rule-table summarizer for HUD entries.

No LLM calls — the router already proved comprehension by selecting a
tool. The HUD just describes which tool ran.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from voice_commander.plan import PlanOutcome, ToolCall


class Summarizer:
    """Turns a PlanOutcome into a short one-line HUD summary.

    Order:

    1. ``status="miss"`` -> ``"no match"``.
    2. ``status="ok"``:
       a. Any chain detector matches -> its phrase.
       b. Single step / multi-step where ALL steps in rule table -> last-step rule.
       c. Otherwise -> default rule (humanised step name).
    3. ``status="error"`` -> ``f"{failed_step.name} failed"``.
    """

    def __init__(
        self,
        rules: dict[str, Callable[[dict[str, Any], PlanOutcome], str]],
        chain_detectors: list[Callable[[tuple[ToolCall, ...]], str | None]],
    ) -> None:
        self._rules = rules
        self._chain_detectors = chain_detectors

    def summarize(self, outcome: PlanOutcome) -> str:
        if outcome.status == "miss":
            return "no match"
        if outcome.status == "error":
            return self._summarize_error(outcome)
        return self._summarize_ok(outcome)

    def _summarize_ok(self, outcome: PlanOutcome) -> str:
        steps = outcome.steps
        for detector in self._chain_detectors:
            phrase = detector(steps)
            if phrase is not None:
                return phrase
        if not steps:
            return ""  # defensive — ok with zero steps should not happen
        last = steps[-1]
        rule = self._rules.get(last.name, _default_rule)
        return rule(last.kwargs, outcome)

    def _summarize_error(self, outcome: PlanOutcome) -> str:
        idx = outcome.failed_step_index
        if idx is not None and 0 <= idx < len(outcome.steps):
            return f"{outcome.steps[idx].name} failed"
        return "command failed"


def _default_rule(_kw: dict[str, Any], outcome: PlanOutcome) -> str:
    """Humanise an unknown verb: ``new_tab`` -> ``new tab``."""
    return outcome.steps[-1].name.replace("_", " ")
