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
        normalized = _normalize_spoken(tail)
        if not normalized:
            return None
        words = normalized.split()

        candidates = self._build_candidates()
        if not candidates:
            return None

        user_steps: list[ToolCall] = []
        cursor = 0
        while cursor < len(words):
            match = self._match_at(words, cursor, candidates)
            if match is None:
                return None
            call, consumed = match
            user_steps.append(call)
            cursor += consumed

        if len(user_steps) < 2:
            return None

        steps: list[ToolCall] = []
        for i, s in enumerate(user_steps):
            if i > 0:
                steps.append(
                    ToolCall(
                        name="wait",
                        kwargs={"ms": INTER_STEP_MS},
                        internal=True,
                    )
                )
            steps.append(s)

        return Plan(
            steps=tuple(steps),
            raw_response={
                "router": "chain",
                "tokens": [s.name for s in user_steps],
            },
            strict=True,
        )

    def _build_candidates(self) -> list[tuple[tuple[str, ...], ToolCall, int]]:
        """Return (spoken_tokens, ToolCall, priority) candidates.

        ``priority`` is 1 for registered command/workflow entries, 0 for
        primitive verb rules. Ties at equal token count go to the higher
        priority so registered commands beat nullary primitives.
        """
        out: list[tuple[tuple[str, ...], ToolCall, int]] = []

        # Registered commands + workflows. Lazy-import to avoid touching the
        # registry surface area in tests that don't need entries.
        for entry in self.registry.all():
            if not entry.enabled:
                continue
            if entry.origin not in ("command", "workflow"):
                continue
            name_tokens = tuple(_spoken(entry.name).split())
            if name_tokens:
                out.append((name_tokens, ToolCall(name=entry.name, kwargs={}), 1))
            for phrase in entry.phrases:  # synonyms
                p = tuple(_spoken(phrase).split())
                if p:
                    out.append((p, ToolCall(name=entry.name, kwargs={}), 1))

        # Nullary primitive verbs (default_target present, name allowed).
        for rule in self.verb_rules:
            if rule.default_target is None:
                continue
            if rule.name in _FORBIDDEN:
                continue
            for alias in rule.aliases:
                a = tuple(_spoken(alias).split())
                if a:
                    out.append(
                        (
                            a,
                            ToolCall(
                                name=rule.default_target.tool,
                                kwargs=dict(rule.default_target.kwargs),
                            ),
                            0,
                        )
                    )

        # Sort: longest token-count first, then higher priority first.
        out.sort(key=lambda c: (len(c[0]), c[2]), reverse=True)
        return out

    def _match_at(
        self,
        words: list[str],
        cursor: int,
        candidates: list[tuple[tuple[str, ...], ToolCall, int]],
    ) -> tuple[ToolCall, int] | None:
        for spoken_tokens, call, _prio in candidates:
            n = len(spoken_tokens)
            if cursor + n > len(words):
                continue
            if tuple(words[cursor : cursor + n]) == spoken_tokens:
                # Reject if this match resolves to a forbidden primitive
                # name. (Defense-in-depth — candidates list already excludes
                # forbidden verbs, but registered commands could shadow them
                # at the same token, and we want chain explicitly to refuse.)
                if call.name in _FORBIDDEN:
                    return None
                return call, n
        return None
