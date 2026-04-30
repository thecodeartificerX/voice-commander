"""Deterministic first-word verb router for normal-mode voice commands."""

from __future__ import annotations

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


class VerbRouter:
    def __init__(self, rules: tuple[VerbRule, ...]) -> None:
        self._rules = {rule.name: rule for rule in rules}
        self._alias_map: dict[str, str] = {}
        for rule in rules:
            for alias in rule.aliases:
                self._alias_map[alias] = rule.name

    def route(self, transcript: str) -> Plan | None:
        text = transcript.strip()
        if not text:
            return None
        head, _, tail = text.partition(" ")
        head = head.strip().lower()
        tail = tail.strip()

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
            return Plan(
                steps=(ToolCall(name=verb.raw_tail_tool, kwargs={verb.raw_tail_arg: tail}),),
                raw_response={"router": "verb", "verb": verb.name, "tail": tail},
            )

        return None

    def _plan_for(self, target: RouteTarget, verb_name: str, tail: str) -> Plan:
        return Plan(
            steps=(ToolCall(name=target.tool, kwargs=dict(target.kwargs)),),
            raw_response={"router": "verb", "verb": verb_name, "tail": tail},
        )


def build_default_rules() -> tuple[VerbRule, ...]:
    return (
        VerbRule("copy", ("copy",), default_target=RouteTarget("press", {"combo": "ctrl+c"})),
        VerbRule("cut", ("cut",), default_target=RouteTarget("press", {"combo": "ctrl+x"})),
        VerbRule("paste", ("paste",), default_target=RouteTarget("press", {"combo": "ctrl+v"})),
        VerbRule("undo", ("undo",), default_target=RouteTarget("press", {"combo": "ctrl+z"})),
        VerbRule("redo", ("redo",), default_target=RouteTarget("press", {"combo": "ctrl+y"})),
        VerbRule("save", ("save",), default_target=RouteTarget("press", {"combo": "ctrl+s"})),
        VerbRule("refresh", ("refresh", "reload"), default_target=RouteTarget("press", {"combo": "ctrl+r"})),
        VerbRule("minimize", ("minimize",), default_target=RouteTarget("press", {"combo": "win+down"})),
        VerbRule("maximize", ("maximize",), default_target=RouteTarget("press", {"combo": "win+up"})),
        VerbRule("close", ("close",), default_target=RouteTarget("press", {"combo": "ctrl+w"}), subcommands=(SubcommandRule(("window",), RouteTarget("press", {"combo": "alt+f4"})),)),
        VerbRule("new", ("new",), subcommands=(SubcommandRule(("tab",), RouteTarget("press", {"combo": "ctrl+t"})), SubcommandRule(("window",), RouteTarget("press", {"combo": "ctrl+n"})))),
        VerbRule("type", ("type",), raw_tail_tool="type", raw_tail_arg="text"),
        VerbRule("open", ("open",), raw_tail_tool="open", raw_tail_arg="target"),
        VerbRule("focus", ("focus",), raw_tail_tool="focus", raw_tail_arg="target"),
    )
