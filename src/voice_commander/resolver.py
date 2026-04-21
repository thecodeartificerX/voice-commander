"""Resolver: routes every transcript through the LLM router and returns a Plan."""
from __future__ import annotations

import logging

from .feedback import FeedbackSink
from .llm_router import LLMRouter
from .plan import Plan

logger = logging.getLogger(__name__)


class Resolver:
    """Unconditional LLM-based command resolver.

    Replaces the former hybrid routing (rapidfuzz → LLM escalation).
    Every transcript is sent to the LLM router; the result is either
    a :class:`Plan` (one or more tool calls) or ``None`` (miss).
    """

    def __init__(self, llm_router: LLMRouter, feedback: FeedbackSink) -> None:
        self._llm_router = llm_router
        self._feedback = feedback

    def resolve(self, transcript: str) -> Plan | None:
        """Route *transcript* through the LLM and return a Plan or None.

        On failure or no_match, fires ``feedback.on_miss`` and returns None.
        """
        plan = self._llm_router.route(transcript)
        if plan is None:
            self._feedback.on_miss(transcript, ())
            return None
        return plan
