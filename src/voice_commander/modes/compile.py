from __future__ import annotations

from ..chain import ChainParser
from ..plan import Plan
from ..registry import ToolRegistry
from ..verb_router import VerbRouter, build_default_rules


def build_base_primitive_router() -> VerbRouter:
    """A VerbRouter that resolves ONLY primitives + chain + repeat.

    No command registry (user commands never match) and no picker
    (bare focus/open/tabs cannot be actions). Used both to compile mode
    actions at load time and as the in-mode primitive fallthrough.
    """
    rules = build_default_rules()
    empty = ToolRegistry()
    return VerbRouter(
        rules,
        registry=None,
        picker_registry=None,
        chain_parser=ChainParser(registry=empty, verb_rules=rules),
    )


def compile_action(router: VerbRouter, action: str) -> Plan:
    """Route *action* through the base router into a cached Plan.

    Raises ValueError if the action does not resolve, or resolves to a
    synthetic intercept step (``__dictation.start`` / ``__picker.open``)
    which cannot be executed as a mode action.
    """
    plan = router.route(action)
    if plan is None:
        raise ValueError(f"action could not be routed: {action!r}")
    if plan.steps and plan.steps[0].name.startswith("__"):
        raise ValueError(f"action resolves to a synthetic intercept (not allowed): {action!r}")
    return plan
