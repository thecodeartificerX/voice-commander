"""Unit tests for the command/workflow registrar."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from voice_commander.commands.registrar import (
    register_commands,
    register_workflows,
    reload_all,
)
from voice_commander.commands.store import (
    CommandDef,
    CommandStore,
    WorkflowArg,
    WorkflowDef,
    WorkflowStep,
    WorkflowStore,
)
from voice_commander.commands.template import TemplateError
from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.registry import ToolEntry, ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _registry_with_primitives() -> ToolRegistry:
    """Return a registry seeded with internal primitives (press, focus, type, open)."""
    reg = ToolRegistry()
    for name in ("press", "focus", "type", "open"):
        reg.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=MagicMock(),
                module="test",
                docstring=None,
                enabled=True,
                llm_only=True,
                internal=True,
                origin="primitive",
            )
        )
    return reg


def _command_store(tmp_path: Path, cmds: list[CommandDef]) -> CommandStore:
    store = CommandStore(tmp_path / "commands.json")
    for c in cmds:
        store.save_one(c)
    return store


def _workflow_store(tmp_path: Path, wfs: list[WorkflowDef]) -> WorkflowStore:
    store = WorkflowStore(tmp_path / "workflows.json")
    for w in wfs:
        store.save_one(w)
    return store


def _dispatcher() -> Dispatcher:
    return Dispatcher(feedback=CapturingFeedbackSink())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def test_register_commands_creates_entries(tmp_path: Path) -> None:
    reg = _registry_with_primitives()
    store = _command_store(
        tmp_path,
        [
            CommandDef(
                name="new_tab",
                description="New tab",
                synonyms=("new tab", "open new tab"),
                primitive="press",
                kwargs={"combo": "ctrl+t"},
            )
        ],
    )

    names = register_commands(reg, store, _dispatcher(), {})

    assert names == ["new_tab"]
    entry = reg.by_name("new_tab")
    assert entry is not None
    assert entry.origin == "command"
    assert entry.internal is False
    assert entry.llm_only is True
    assert "new tab" in entry.params_schema["function"]["description"]


def test_register_commands_dispatches_primitive(tmp_path: Path) -> None:
    """Invoking the command entry's func runs the pre-baked plan and hits press()."""
    reg = _registry_with_primitives()
    press_mock = reg.by_name("press").func  # type: ignore[union-attr]

    store = _command_store(
        tmp_path,
        [
            CommandDef(
                name="copy",
                description="Copy",
                synonyms=("copy",),
                primitive="press",
                kwargs={"combo": "ctrl+c"},
            )
        ],
    )
    register_commands(reg, store, _dispatcher(), {})

    reg.by_name("copy").func()  # type: ignore[union-attr]
    press_mock.assert_called_once_with(combo="ctrl+c")


def test_register_commands_replaces_previous_entries(tmp_path: Path) -> None:
    """Re-running register_commands drops previously registered commands first."""
    reg = _registry_with_primitives()
    store = _command_store(
        tmp_path,
        [
            CommandDef(
                name="old_cmd",
                description="Old",
                synonyms=(),
                primitive="press",
                kwargs={"combo": "ctrl+a"},
            )
        ],
    )
    register_commands(reg, store, _dispatcher(), {})
    assert reg.by_name("old_cmd") is not None

    # Rewrite store with a different command.
    store.delete("old_cmd")
    store.save_one(
        CommandDef(
            name="new_cmd",
            description="New",
            synonyms=(),
            primitive="press",
            kwargs={"combo": "ctrl+b"},
        )
    )
    register_commands(reg, store, _dispatcher(), {})
    assert reg.by_name("old_cmd") is None
    assert reg.by_name("new_cmd") is not None


def test_register_commands_skips_disabled(tmp_path: Path) -> None:
    reg = _registry_with_primitives()
    store = _command_store(
        tmp_path,
        [
            CommandDef(
                name="off",
                description="",
                synonyms=(),
                primitive="press",
                kwargs={"combo": "ctrl+a"},
                enabled=False,
            )
        ],
    )
    register_commands(reg, store, _dispatcher(), {})
    assert reg.by_name("off") is None


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


def test_register_workflows_builds_schema_with_args(tmp_path: Path) -> None:
    reg = _registry_with_primitives()
    store = _workflow_store(
        tmp_path,
        [
            WorkflowDef(
                name="search_web",
                description="Search the web",
                synonyms=("search {query}",),
                args=(WorkflowArg(name="query", required=True),),
                steps=(
                    WorkflowStep(ref="primitive:press", kwargs={"combo": "ctrl+t"}),
                    WorkflowStep(ref="primitive:type", kwargs={"text": "{query}"}),
                    WorkflowStep(ref="primitive:press", kwargs={"combo": "enter"}),
                ),
            )
        ],
    )

    register_workflows(reg, store, _dispatcher(), {"default_browser": "comet"})

    entry = reg.by_name("search_web")
    assert entry is not None
    schema = entry.params_schema["function"]["parameters"]
    assert "query" in schema["properties"]
    assert schema["required"] == ["query"]


def test_workflow_execution_substitutes_user_kwargs(tmp_path: Path) -> None:
    reg = _registry_with_primitives()
    press_mock = reg.by_name("press").func  # type: ignore[union-attr]
    type_mock = reg.by_name("type").func  # type: ignore[union-attr]

    store = _workflow_store(
        tmp_path,
        [
            WorkflowDef(
                name="type_query",
                description="",
                synonyms=(),
                args=(WorkflowArg(name="query", required=True),),
                steps=(
                    WorkflowStep(ref="primitive:type", kwargs={"text": "{query}"}),
                    WorkflowStep(ref="primitive:press", kwargs={"combo": "enter"}),
                ),
            )
        ],
    )
    register_workflows(reg, store, _dispatcher(), {})

    reg.by_name("type_query").func(query="hello world")  # type: ignore[union-attr]

    type_mock.assert_called_once_with(text="hello world")
    press_mock.assert_called_once_with(combo="enter")


def test_workflow_unknown_placeholder_raises(tmp_path: Path) -> None:
    reg = _registry_with_primitives()
    store = _workflow_store(
        tmp_path,
        [
            WorkflowDef(
                name="broken",
                description="",
                synonyms=(),
                args=(),
                steps=(WorkflowStep(ref="primitive:type", kwargs={"text": "{missing}"}),),
            )
        ],
    )
    register_workflows(reg, store, _dispatcher(), {})

    with pytest.raises(TemplateError, match="Unknown placeholder"):
        reg.by_name("broken").func()  # type: ignore[union-attr]


def test_workflow_uses_context_for_default_browser(tmp_path: Path) -> None:
    reg = _registry_with_primitives()
    focus_mock = reg.by_name("focus").func  # type: ignore[union-attr]

    store = _workflow_store(
        tmp_path,
        [
            WorkflowDef(
                name="focus_browser",
                description="",
                synonyms=(),
                args=(),
                steps=(
                    WorkflowStep(
                        ref="primitive:focus", kwargs={"target": "{default_browser}"}
                    ),
                ),
            )
        ],
    )
    register_workflows(reg, store, _dispatcher(), {"default_browser": "comet"})

    reg.by_name("focus_browser").func()  # type: ignore[union-attr]
    focus_mock.assert_called_once_with(target="comet")


# ---------------------------------------------------------------------------
# reload_all
# ---------------------------------------------------------------------------


def test_reload_all_returns_both_sets(tmp_path: Path) -> None:
    reg = _registry_with_primitives()
    cs = _command_store(
        tmp_path,
        [
            CommandDef(
                name="cmd1",
                description="",
                synonyms=(),
                primitive="press",
                kwargs={"combo": "ctrl+a"},
            )
        ],
    )
    ws = _workflow_store(
        tmp_path,
        [
            WorkflowDef(
                name="wf1",
                description="",
                synonyms=(),
                args=(),
                steps=(WorkflowStep(ref="primitive:press", kwargs={"combo": "ctrl+b"}),),
            )
        ],
    )
    cmds, wfs = reload_all(reg, cs, ws, _dispatcher(), {})
    assert cmds == ["cmd1"]
    assert wfs == ["wf1"]
    assert reg.by_name("cmd1") is not None
    assert reg.by_name("wf1") is not None
