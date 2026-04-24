"""Synthesise ``ToolEntry`` objects from ``CommandDef`` / ``WorkflowDef``.

Commands and workflows are user-defined data, not Python code. The registrar
builds a ``ToolEntry`` for each definition with a closure ``func`` that runs
the corresponding pre-baked ``Plan`` through the dispatcher. Primitives
referenced by commands/workflows stay dispatchable because the registry
retains them with ``internal=True``.

Public entry points:

- :func:`register_commands` — idempotent; safe to call on each hot-reload.
- :func:`register_workflows` — same semantics; re-runs to pick up edits.
- :func:`reload_all` — convenience wrapper used by the daemon startup path
  and by the web UI after a save.

All functions acquire the daemon's ``reload_lock`` externally — registrar
code itself is not thread-safe against concurrent discover() runs.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from voice_commander.commands.store import (
    CommandDef,
    CommandStore,
    WorkflowDef,
    WorkflowStep,
    WorkflowStore,
)
from voice_commander.commands.template import TemplateError, substitute
from voice_commander.dispatcher import Dispatcher
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON Schema helpers
# ---------------------------------------------------------------------------


_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "str": "string",
    "int": "integer",
    "integer": "integer",
    "bool": "boolean",
    "boolean": "boolean",
    "float": "number",
    "number": "number",
}


def _build_command_schema(cmd: CommandDef) -> dict[str, Any]:
    """Build the OpenAI-style tool schema for a command. Commands are atomic —
    no user-visible args. The ``description`` ships the synonyms so the LLM
    can match phrasing variants."""
    description = cmd.description
    if cmd.synonyms:
        description = f"{description}\nPhrases: {', '.join(cmd.synonyms)}"
    return {
        "type": "function",
        "function": {
            "name": cmd.name,
            "description": description.strip(),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }


def _build_workflow_schema(wf: WorkflowDef) -> dict[str, Any]:
    """Build the OpenAI-style tool schema for a workflow, exposing each
    declared arg as a JSON Schema property."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for a in wf.args:
        json_type = _TYPE_MAP.get(a.type_str.lower(), "string")
        props[a.name] = {"type": json_type, "description": a.description or a.name}
        if a.required:
            required.append(a.name)

    description = wf.description
    if wf.synonyms:
        description = f"{description}\nPhrases: {', '.join(wf.synonyms)}"

    return {
        "type": "function",
        "function": {
            "name": wf.name,
            "description": description.strip(),
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


def _make_command_func(
    cmd: CommandDef,
    registry: ToolRegistry,
    dispatcher: Dispatcher,
    context: Mapping[str, Any],
) -> Any:
    """Return a closure that dispatches a command's single-step pre-baked plan."""

    def _run(**call_kwargs: Any) -> None:  # LLM may or may not pass kwargs
        resolved_kwargs = substitute(cmd.kwargs, context)
        plan = Plan(
            steps=(ToolCall(name=cmd.primitive, kwargs=resolved_kwargs),),
            raw_response={"synthetic": True, "command": cmd.name},
            strict=True,
        )
        dispatcher.run_plan(f"command:{cmd.name}", plan, registry)

    _run.__name__ = f"command__{cmd.name}"
    _run.__doc__ = cmd.description or None
    return _run


def register_commands(
    registry: ToolRegistry,
    store: CommandStore,
    dispatcher: Dispatcher,
    context: Mapping[str, Any],
) -> list[str]:
    """Load all commands from *store* and (re)register them on *registry*.

    Previous command entries are removed first so renames / deletes take
    effect. Returns the list of command names that were registered.
    """
    _drop_origin(registry, "command")

    commands = store.load_all()
    names: list[str] = []
    for cmd in commands.values():
        if not cmd.enabled:
            continue
        existing = registry.by_name(cmd.name)
        if existing is not None:
            # Disabled legacy primitives can be transparently replaced by a
            # user command of the same name (e.g. close_window, minimize).
            # Only block when the existing entry is live (enabled primitive).
            if existing.enabled:
                logger.warning(
                    "Command %r clashes with enabled registry entry — skipping",
                    cmd.name,
                )
                continue
            registry.remove(cmd.name)
        entry = ToolEntry(
            name=cmd.name,
            phrases=cmd.synonyms,
            func=_make_command_func(cmd, registry, dispatcher, context),
            module="voice_commander.commands",
            docstring=cmd.description or None,
            description=cmd.description,
            category="command",
            enabled=True,
            params_schema=_build_command_schema(cmd),
            settle_ms=0,
            llm_only=True,
            internal=False,
            origin="command",
        )
        registry.register(entry)
        names.append(cmd.name)
    logger.info("Registered %d commands: %s", len(names), names)
    return names


# ---------------------------------------------------------------------------
# Workflow registration
# ---------------------------------------------------------------------------


def _build_workflow_plan(
    wf: WorkflowDef,
    user_kwargs: Mapping[str, Any],
    registry: ToolRegistry,
    context: Mapping[str, Any],
) -> Plan:
    """Turn a workflow definition + runtime kwargs into a dispatchable Plan.

    Each ``WorkflowStep.ref`` is resolved to a concrete ``(primitive_name,
    kwargs)`` tuple. ``primitive:X`` refs map straight through; ``command:Y``
    refs delegate to the referenced command's pre-baked step. Placeholders in
    step kwargs are substituted using the combined user-supplied kwargs +
    *context*.
    """
    merged_ctx: dict[str, Any] = {**context, **user_kwargs}
    steps: list[ToolCall] = []
    for i, step in enumerate(wf.steps):
        try:
            resolved = substitute(step.kwargs, merged_ctx)
        except TemplateError as exc:
            raise TemplateError(f"workflow {wf.name!r} step {i} ({step.ref}): {exc}") from exc
        target_name = _resolve_step_ref(step, registry)
        steps.append(ToolCall(name=target_name, kwargs=resolved))
    return Plan(
        steps=tuple(steps),
        raw_response={"synthetic": True, "workflow": wf.name},
        strict=True,
    )


def _resolve_step_ref(step: WorkflowStep, registry: ToolRegistry) -> str:
    """Return the primitive name to call for *step*.

    ``primitive:<name>`` → returned as-is.
    ``command:<name>`` → resolved by looking up the command's pre-baked step
    in the registry metadata. This gives workflows a cheap way to compose
    existing commands without duplicating their kwargs.
    """
    kind = step.ref_kind
    name = step.ref_name
    if kind == "primitive":
        return name
    if kind == "command":
        entry = registry.by_name(name)
        if entry is None or entry.origin != "command":
            raise ValueError(f"Workflow references unknown command: {name!r}")
        # Commands are atomic — their synthetic func runs a one-step plan.
        # We can't unwrap that plan here without re-parsing, so we invoke
        # the command's func directly via a same-name dispatch.
        return name
    raise ValueError(f"Unknown workflow step ref kind {kind!r} (expected 'primitive' or 'command')")


def _make_workflow_func(
    wf: WorkflowDef,
    registry: ToolRegistry,
    dispatcher: Dispatcher,
    context: Mapping[str, Any],
) -> Any:
    def _run(**call_kwargs: Any) -> None:
        plan = _build_workflow_plan(wf, call_kwargs, registry, context)
        dispatcher.run_plan(f"workflow:{wf.name}", plan, registry)

    _run.__name__ = f"workflow__{wf.name}"
    _run.__doc__ = wf.description or None
    return _run


def register_workflows(
    registry: ToolRegistry,
    store: WorkflowStore,
    dispatcher: Dispatcher,
    context: Mapping[str, Any],
) -> list[str]:
    """Load all workflows and (re)register them on *registry*.

    Must run AFTER :func:`register_commands` so that ``command:<name>``
    step refs can be resolved at dispatch time.
    """
    _drop_origin(registry, "workflow")

    workflows = store.load_all()
    names: list[str] = []
    for wf in workflows.values():
        if not wf.enabled:
            continue
        existing = registry.by_name(wf.name)
        if existing is not None:
            if existing.enabled:
                logger.warning(
                    "Workflow %r clashes with enabled registry entry — skipping",
                    wf.name,
                )
                continue
            registry.remove(wf.name)
        entry = ToolEntry(
            name=wf.name,
            phrases=wf.synonyms,
            func=_make_workflow_func(wf, registry, dispatcher, context),
            module="voice_commander.commands",
            docstring=wf.description or None,
            description=wf.description,
            category="workflow",
            enabled=True,
            params_schema=_build_workflow_schema(wf),
            settle_ms=0,
            llm_only=True,
            internal=False,
            origin="workflow",
        )
        registry.register(entry)
        names.append(wf.name)
    logger.info("Registered %d workflows: %s", len(names), names)
    return names


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drop_origin(registry: ToolRegistry, origin: str) -> None:
    """Remove every registry entry whose ``origin`` matches *origin*."""
    for entry in list(registry.by_origin(origin)):  # type: ignore[arg-type]
        registry.remove(entry.name)


def reload_all(
    registry: ToolRegistry,
    command_store: CommandStore,
    workflow_store: WorkflowStore,
    dispatcher: Dispatcher,
    context: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """Reload both commands and workflows. Returns (command_names, workflow_names)."""
    cmd_names = register_commands(registry, command_store, dispatcher, context)
    wf_names = register_workflows(registry, workflow_store, dispatcher, context)
    return cmd_names, wf_names
