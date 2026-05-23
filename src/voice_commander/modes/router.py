from __future__ import annotations

from ..plan import Plan
from ..verb_router import VerbRouter, _normalize_spoken
from .types import ModeDefinition


def _spoken(s: str) -> str:
    return _normalize_spoken(s.replace("_", " "))


class ModeRouter:
    """Routes an utterance against one mode: phrases first, then primitives."""

    def __init__(self, definition: ModeDefinition, base_router: VerbRouter) -> None:
        self._def = definition
        self._base = base_router
        # (normalized phrase, plan) sorted longest-first so specific wins.
        pairs: list[tuple[str, Plan]] = []
        for cmd in definition.commands:
            for phrase in cmd.phrases:
                norm = _spoken(phrase)
                if norm:
                    pairs.append((norm, cmd.plan))
        pairs.sort(key=lambda p: len(p[0].split()), reverse=True)
        self._pairs = pairs

    def route(self, transcript: str) -> Plan | None:
        normalized = _normalize_spoken(transcript)
        if not normalized:
            return None
        for phrase, plan in self._pairs:
            if phrase == normalized:
                return plan
        # Primitive / chain / repeat fallthrough (mode + primitives).
        fallthrough = self._base.route(transcript)
        if fallthrough is None:
            return None
        if any(step.name.startswith("__") for step in fallthrough.steps):
            return None  # synthetic intercepts (dictate/picker) unsupported in-mode
        return fallthrough
