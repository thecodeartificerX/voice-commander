"""Deterministic first-word verb router for normal-mode voice commands."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .plan import Plan, ToolCall

# Whisper occasionally renders short spoken words as initialisms with
# embedded periods (e.g. "paste" → "P.A.C.T."). Normalise transcripts by
# dropping every non-alphanumeric run so command lookup is tolerant of
# punctuation noise.
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")


def _normalize_spoken(text: str) -> str:
    cleaned = _PUNCT_RE.sub(" ", text.lower())
    return " ".join(cleaned.split())

if TYPE_CHECKING:
    from .picker.registry import BarePickerRegistry
    from .registry import ToolRegistry


@dataclass(frozen=True)
class RouteTarget:
    tool: str
    kwargs: dict[str, object]


@dataclass(frozen=True)
class SubcommandRule:
    aliases: tuple[str, ...]
    target: RouteTarget


@dataclass(frozen=True)
class VerbRule:
    name: str
    aliases: tuple[str, ...]
    default_target: RouteTarget | None = None
    subcommands: tuple[SubcommandRule, ...] = ()
    raw_tail_tool: str | None = None
    raw_tail_arg: str | None = None
    tail_coerce: Callable[[str], object] | None = None


class VerbRouter:
    def __init__(
        self,
        rules: tuple[VerbRule, ...],
        registry: "ToolRegistry | None" = None,
        picker_registry: "BarePickerRegistry | None" = None,
    ) -> None:
        self._rules = {rule.name: rule for rule in rules}
        self._alias_map: dict[str, str] = {}
        for rule in rules:
            for alias in rule.aliases:
                self._alias_map[alias] = rule.name
        # Optional registry lookup so user-authored commands and workflows
        # are routable by their first word without going through the LLM.
        # The registry is mutable (hot-reload), so we hold a reference and
        # query lazily on each utterance rather than snapshotting at init.
        self._registry = registry
        self._picker_registry = picker_registry

    def route(self, transcript: str) -> Plan | None:
        text = transcript.strip().rstrip(".,!?")
        if not text:
            return None

        # 1. Try registered command / workflow names (multi-word allowed,
        #    longest match wins). Lets the user author a "close" command
        #    plus a "close window" variant and have voice route to the
        #    correct graph deterministically.
        registered = self._match_registered_command(text)
        if registered is not None:
            return registered

        head, _, tail = text.partition(" ")
        head = head.strip().lower()
        tail = tail.strip().rstrip(".,!?")

        verb_name = self._alias_map.get(head)
        if verb_name is None:
            return None
        verb = self._rules[verb_name]

        if not tail:
            # Bare verb with a default target (e.g. "click", "scroll") wins
            # over the picker so existing behaviour is preserved.
            if verb.default_target is not None:
                return self._plan_for(verb.default_target, verb.name, tail)
            # No default target, no tail — try the bare-primitive picker.
            if self._picker_registry is not None and self._picker_registry.has(verb.name):
                return Plan(
                    steps=(ToolCall(name="__picker.open", kwargs={"verb": verb.name}),),
                    raw_response={
                        "router": "verb",
                        "verb": verb.name,
                        "tail": "",
                        "bare_picker": True,
                    },
                )

        if tail:
            for sub in verb.subcommands:
                if tail.lower() in sub.aliases:
                    return self._plan_for(sub.target, verb.name, tail)

        if tail and verb.raw_tail_tool and verb.raw_tail_arg:
            arg_value: object = tail
            if verb.tail_coerce is not None:
                try:
                    arg_value = verb.tail_coerce(tail)
                except (ValueError, TypeError):
                    return None
            return Plan(
                steps=(ToolCall(name=verb.raw_tail_tool, kwargs={verb.raw_tail_arg: arg_value}),),
                raw_response={"router": "verb", "verb": verb.name, "tail": tail},
            )

        return None

    def _plan_for(self, target: RouteTarget, verb_name: str, tail: str) -> Plan:
        return Plan(
            steps=(ToolCall(name=target.tool, kwargs=dict(target.kwargs)),),
            raw_response={"router": "verb", "verb": verb_name, "tail": tail},
        )

    def _match_registered_command(self, text: str) -> Plan | None:
        """Exact-match *text* against user-authored command / workflow names
        (and their synonyms / phrases).

        Names may contain spaces (``"close window"``). We try longest names
        first so a more specific variant ("close window") wins over the bare
        verb ("close") when the user said the longer form. Comparison is
        case-insensitive on whitespace-collapsed, punctuation-stripped
        tokens, so noisy Whisper outputs like "Copy." still match.

        ``entry.phrases`` (graph synonyms) are matched too so authors can
        register alternative spellings for words Whisper consistently
        mistranscribes (e.g. ``synonyms = ["paste", "P.A.C.T."]``).

        Returns ``None`` when no candidate matches — the caller falls
        through to primitive verb routing.
        """
        if self._registry is None or not text:
            return None
        normalized = _normalize_spoken(text)
        if not normalized:
            return None
        candidates = [
            e
            for e in self._registry.all()
            if e.enabled and e.origin in ("command", "workflow")
        ]
        # Command names persist with underscores ("close_window") because the
        # store enforces a-z0-9_ identifiers, but voice transcripts arrive as
        # whitespace-separated words ("close window"). Treat underscores as
        # word separators so authors don't have to choose between voice
        # ergonomics and a valid storage name.
        def _spoken(s: str) -> str:
            return _normalize_spoken(s.replace("_", " "))

        # (entry, spoken_phrase) pairs across name + synonyms.
        pairs: list[tuple[object, str]] = []
        for entry in candidates:
            pairs.append((entry, _spoken(entry.name)))
            for phrase in entry.phrases:
                spoken = _spoken(phrase)
                if spoken:
                    pairs.append((entry, spoken))

        pairs.sort(key=lambda p: len(p[1].split()), reverse=True)
        for entry, spoken in pairs:
            if spoken == normalized:
                return Plan(
                    steps=(ToolCall(name=entry.name, kwargs={}),),  # type: ignore[attr-defined]
                    raw_response={"router": "verb", "verb": entry.name, "tail": ""},  # type: ignore[attr-defined]
                )
        return None


def _coerce_wait_ms(tail: str) -> int:
    """Permissive int parser: '500' / '500 ms' / '500 milliseconds' → 500."""
    head_token = tail.split()[0]
    return int(head_token)


def build_default_rules() -> tuple[VerbRule, ...]:
    return (
        VerbRule("click", ("click",), default_target=RouteTarget("click", {})),
        VerbRule(
            "scroll",
            ("scroll",),
            default_target=RouteTarget("scroll", {"direction": "down"}),
            subcommands=(
                SubcommandRule(("up",), RouteTarget("scroll", {"direction": "up"})),
                SubcommandRule(("down",), RouteTarget("scroll", {"direction": "down"})),
            ),
        ),
        VerbRule("focus", ("focus",), raw_tail_tool="focus", raw_tail_arg="target"),
        VerbRule("open", ("open",), raw_tail_tool="open", raw_tail_arg="target"),
        VerbRule("type", ("type",), raw_tail_tool="type", raw_tail_arg="text"),
        VerbRule("press", ("press",), raw_tail_tool="press", raw_tail_arg="combo"),
        VerbRule(
            "wait",
            ("wait",),
            raw_tail_tool="wait",
            raw_tail_arg="ms",
            tail_coerce=_coerce_wait_ms,
        ),
    )
