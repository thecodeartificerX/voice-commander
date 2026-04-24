"""User-defined commands + workflows: data model, persistence, and registrar.

Overview
--------
A *command* is a named atomic action that invokes exactly one primitive tool
(e.g. ``new_tab`` → ``press(combo="ctrl+t")``). A *workflow* is a named
ordered sequence of primitive/command references with optional templated
``{placeholder}`` arguments (e.g. ``search_web(query)`` → focus browser,
ctrl+t, ctrl+l, type query, enter).

The LLM sees **only** commands + workflows — never raw primitives. Match →
instant dispatch of a pre-baked ``Plan``. No match → miss chime. No retry,
no agentic loop.

Storage
-------
Commands live in ``commands.json`` at the project root. Workflows live in
``workflows.json``. First-run seeding copies the bundled
``commands.default.json`` / ``workflows.default.json`` into place if no
user-owned file is present.

Module layout
-------------
- :mod:`voice_commander.commands.store` — dataclasses + JSON IO.
- :mod:`voice_commander.commands.template` — ``{placeholder}`` substitution.
- :mod:`voice_commander.commands.registrar` — synthesise ``ToolEntry``
  objects + hot-reload hook.
"""

from voice_commander.commands.store import (
    CommandDef,
    CommandStore,
    WorkflowArg,
    WorkflowDef,
    WorkflowStep,
    WorkflowStore,
)
from voice_commander.commands.template import substitute

__all__ = [
    "CommandDef",
    "CommandStore",
    "WorkflowArg",
    "WorkflowDef",
    "WorkflowStep",
    "WorkflowStore",
    "substitute",
]
