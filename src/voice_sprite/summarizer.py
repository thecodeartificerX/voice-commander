"""Hybrid rule-table + LLM-fallback summarizer for HUD entries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from voice_commander.plan import PlanOutcome, ToolCall

from .llm_summary_client import LLMSummaryClient


class Summarizer:
    """Turns a PlanOutcome into a short one-line HUD summary.

    Order:

    1. ``status="miss"`` -> ``"no match"`` (rules only, never LLM).
    2. ``status="ok"``:
       a. Any chain detector matches -> its phrase.
       b. Single step in rule table -> rule(kwargs, outcome).
       c. Multi-step where all steps in rule table -> last-step rule.
       d. Any step outside rule table -> LLM fallback (or raw fallback
          when ``llm_fallback_enabled=False``).
    3. ``status="error"``:
       a. LLM fallback with full outcome.
       b. LLM unavailable / disabled / returns None -> ``f"{failed_step.name} failed"``.
    4. LLM response >80 chars -> already truncated by ``LLMSummaryClient``.
    """

    def __init__(
        self,
        rules: dict[str, Callable[[dict[str, Any], PlanOutcome], str]],
        chain_detectors: list[Callable[[tuple[ToolCall, ...]], str | None]],
        llm_client: LLMSummaryClient | None,
        llm_fallback_enabled: bool = True,
    ) -> None:
        self._rules = rules
        self._chain_detectors = chain_detectors
        self._llm_client = llm_client
        self._llm_enabled = llm_fallback_enabled

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
        if all(s.name in self._rules for s in steps):
            last = steps[-1]
            return self._rules[last.name](last.kwargs, outcome)
        # Unknown verb — LLM fallback
        if self._llm_enabled and self._llm_client is not None:
            out = self._llm_client.summarize(outcome)
            if out:
                return out
        # Raw fallback for unknown verb: last step name
        return steps[-1].name

    def _summarize_error(self, outcome: PlanOutcome) -> str:
        if self._llm_enabled and self._llm_client is not None:
            out = self._llm_client.summarize(outcome)
            if out:
                return out
        # Raw fallback
        idx = outcome.failed_step_index
        if idx is not None and 0 <= idx < len(outcome.steps):
            return f"{outcome.steps[idx].name} failed"
        return "command failed"
