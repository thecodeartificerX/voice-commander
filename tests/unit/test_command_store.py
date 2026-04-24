"""Unit tests for the CommandStore and WorkflowStore JSON persistence layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_commander.commands.store import (
    CommandDef,
    CommandStore,
    CommandStoreError,
    WorkflowArg,
    WorkflowDef,
    WorkflowStep,
    WorkflowStore,
    seed_if_missing,
)


# ---------------------------------------------------------------------------
# CommandStore
# ---------------------------------------------------------------------------


def test_command_load_all_empty_file(tmp_path: Path) -> None:
    store = CommandStore(tmp_path / "commands.json")
    assert store.load_all() == {}


def test_command_load_all_parses_definitions(tmp_path: Path) -> None:
    path = tmp_path / "commands.json"
    path.write_text(
        json.dumps(
            {
                "commands": {
                    "new_tab": {
                        "description": "Open a new tab",
                        "synonyms": ["new tab", "open new tab"],
                        "primitive": "press",
                        "kwargs": {"combo": "ctrl+t"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = CommandStore(path)
    loaded = store.load_all()
    assert "new_tab" in loaded
    cmd = loaded["new_tab"]
    assert cmd.synonyms == ("new tab", "open new tab")
    assert cmd.primitive == "press"
    assert cmd.kwargs == {"combo": "ctrl+t"}
    assert cmd.enabled is True


def test_command_save_one_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "commands.json"
    store = CommandStore(path)
    store.save_one(
        CommandDef(
            name="copy",
            description="Copy",
            synonyms=("copy",),
            primitive="press",
            kwargs={"combo": "ctrl+c"},
        )
    )
    reloaded = store.load_all()
    assert "copy" in reloaded
    assert reloaded["copy"].kwargs == {"combo": "ctrl+c"}


def test_command_delete_removes_entry(tmp_path: Path) -> None:
    path = tmp_path / "commands.json"
    store = CommandStore(path)
    store.save_one(
        CommandDef(
            name="select_all",
            description="Select all",
            synonyms=("select all",),
            primitive="press",
            kwargs={"combo": "ctrl+a"},
        )
    )
    assert store.delete("select_all") is True
    assert store.load_all() == {}
    # Second delete is a no-op.
    assert store.delete("select_all") is False


def test_command_missing_primitive_raises(tmp_path: Path) -> None:
    path = tmp_path / "commands.json"
    path.write_text(
        json.dumps({"commands": {"bad": {"description": "no primitive"}}}),
        encoding="utf-8",
    )
    store = CommandStore(path)
    with pytest.raises(CommandStoreError, match="primitive is required"):
        store.load_all()


def test_command_invalid_name_raises(tmp_path: Path) -> None:
    store = CommandStore(tmp_path / "commands.json")
    with pytest.raises(CommandStoreError, match="only lowercase"):
        store.save_one(
            CommandDef(
                name="New-Tab",
                description="x",
                synonyms=("x",),
                primitive="press",
                kwargs={"combo": "ctrl+t"},
            )
        )


# ---------------------------------------------------------------------------
# WorkflowStore
# ---------------------------------------------------------------------------


def test_workflow_load_all_empty(tmp_path: Path) -> None:
    assert WorkflowStore(tmp_path / "workflows.json").load_all() == {}


def test_workflow_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "workflows.json"
    store = WorkflowStore(path)
    store.save_one(
        WorkflowDef(
            name="search_web",
            description="Search the web",
            synonyms=("search {query}",),
            args=(WorkflowArg(name="query", type_str="string", required=True),),
            steps=(
                WorkflowStep(ref="primitive:press", kwargs={"combo": "ctrl+t"}),
                WorkflowStep(ref="primitive:type", kwargs={"text": "{query}"}),
                WorkflowStep(ref="primitive:press", kwargs={"combo": "enter"}),
            ),
        )
    )
    loaded = store.load_all()
    assert "search_web" in loaded
    wf = loaded["search_web"]
    assert len(wf.steps) == 3
    assert wf.steps[0].ref == "primitive:press"
    assert wf.steps[0].ref_kind == "primitive"
    assert wf.steps[0].ref_name == "press"
    assert wf.args[0].name == "query"


def test_workflow_step_missing_ref_raises(tmp_path: Path) -> None:
    path = tmp_path / "workflows.json"
    path.write_text(
        json.dumps(
            {
                "workflows": {
                    "broken": {
                        "description": "x",
                        "synonyms": [],
                        "args": [],
                        "steps": [{"kwargs": {}}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CommandStoreError, match="ref is required"):
        WorkflowStore(path).load_all()


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seed_copies_default_when_missing(tmp_path: Path) -> None:
    default = tmp_path / "commands.default.json"
    default.write_text('{"commands": {}}', encoding="utf-8")
    target = tmp_path / "commands.json"

    assert seed_if_missing(target, default) is True
    assert target.read_text(encoding="utf-8") == '{"commands": {}}'


def test_seed_noop_when_target_exists(tmp_path: Path) -> None:
    default = tmp_path / "commands.default.json"
    default.write_text('{"commands": {"a": {}}}', encoding="utf-8")
    target = tmp_path / "commands.json"
    target.write_text('{"commands": {"user_owned": {}}}', encoding="utf-8")

    assert seed_if_missing(target, default) is False
    # User file untouched.
    assert '"user_owned"' in target.read_text(encoding="utf-8")


def test_seed_raises_when_default_missing(tmp_path: Path) -> None:
    with pytest.raises(CommandStoreError, match="Starter pack file missing"):
        seed_if_missing(tmp_path / "commands.json", tmp_path / "no.json")
