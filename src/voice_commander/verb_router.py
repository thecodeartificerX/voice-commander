"""Deterministic first-word verb router for normal-mode voice commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .plan import Plan, ToolCall


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
    def __init__(self, rules: tuple[VerbRule, ...]) -> None:
        self._rules = {rule.name: rule for rule in rules}
        self._alias_map: dict[str, str] = {}
        for rule in rules:
            for alias in rule.aliases:
                self._alias_map[alias] = rule.name

    def route(self, transcript: str) -> Plan | None:
        text = transcript.strip().rstrip(".,!?")
        if not text:
            return None
        head, _, tail = text.partition(" ")
        head = head.strip().lower()
        tail = tail.strip().rstrip(".,!?")

        verb_name = self._alias_map.get(head)
        if verb_name is None:
            return None
        verb = self._rules[verb_name]

        if not tail and verb.default_target is not None:
            return self._plan_for(verb.default_target, verb.name, tail)

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
