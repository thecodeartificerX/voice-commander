"""Chain meta-verb parser.

A ``chain`` utterance is a router-level construct, NOT a tool. It expands
``"chain <tok1> <tok2> ... <tokN>"`` into a multi-step ``Plan`` whose user
steps are interleaved with synthetic ``wait(ms=255)`` steps flagged
``internal=True`` so the Dispatcher executes them but does not surface them
in HUD / FeedbackSink events.

Allowed tokens:
  * registered command / workflow spoken-names + synonyms
  * nullary primitive verb aliases with a ``default_target`` whose verb name
    is not in ``_FORBIDDEN``

Forbidden tokens (whole utterance rejected, miss-chime):
  * ``open``, ``type``, ``press``, ``wait``, ``focus`` — arg-taking and/or
    picker-triggering when bare
  * ``tabs`` — picker-only
  * ``chain`` — no recursion

See ``docs/superpowers/specs/2026-05-15-chain-primitive-design.md`` for the
full design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .plan import Plan, ToolCall
from .verb_router import VerbRule, _normalize_spoken

if TYPE_CHECKING:
    from .registry import ToolRegistry

INTER_STEP_MS: int = 255

_FORBIDDEN: frozenset[str] = frozenset(
    {"open", "type", "press", "wait", "focus", "tabs", "chain"}
)
_HEAD_ALIASES: frozenset[str] = frozenset({"chain", "chained", "chains"})


def _spoken(name: str) -> str:
    """Underscore-to-space + normalized form, mirroring the main router."""
    return _normalize_spoken(name.replace("_", " "))


@dataclass(frozen=True)
class ChainParser:
    """Greedy longest-match parser for the ``chain`` meta-verb."""

    registry: "ToolRegistry"
    verb_rules: tuple[VerbRule, ...]

    def parse(self, tail: str) -> Plan | None:
        raise NotImplementedError
