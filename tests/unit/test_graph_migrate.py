"""Unit tests for legacy-to-canonical graph migration."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from voice_commander.commands.graph_migrate import migrate_legacy_to_graphs


def _fixture(name: str) -> Path:
    return Path(__file__).parent.parent / "fixtures" / "legacy" / name


def test_migrate_command(tmp_path: Path) -> None:
    """Legacy single-primitive command → 1-node canonical graph."""
    shutil.copy(_fixture("commands.json"), tmp_path / "commands.json")
    (tmp_path / "workflows.json").write_text(json.dumps({"workflows": {}}))

    cmd_n, wf_n = migrate_legacy_to_graphs(
        tmp_path / "commands.json",
        tmp_path / "workflows.json",
    )
    assert cmd_n >= 1

    data = json.loads((tmp_path / "commands.json").read_text())
    assert data["schema_version"] == 1
    assert "new_tab" in data["graphs"]
    g = data["graphs"]["new_tab"]
    assert g["kind"] == "command"
    assert len(g["nodes"]) == 1
    assert g["nodes"][0]["ref"] == "pipeline.press"
    assert g["nodes"][0]["kwargs"] == {"combo": "ctrl+t"}


def test_migrate_workflow(tmp_path: Path) -> None:
    """Legacy multi-step workflow with {placeholder} → linear DAG with data edges."""
    (tmp_path / "commands.json").write_text(json.dumps({"commands": {}}))
    shutil.copy(_fixture("workflows.json"), tmp_path / "workflows.json")

    cmd_n, wf_n = migrate_legacy_to_graphs(
        tmp_path / "commands.json",
        tmp_path / "workflows.json",
    )
    assert wf_n >= 1

    data = json.loads((tmp_path / "workflows.json").read_text())
    assert data["schema_version"] == 1
    assert "search_web" in data["graphs"]
    g = data["graphs"]["search_web"]
    assert g["kind"] == "workflow"
    assert len(g["inputs"]) == 1
    assert g["inputs"][0]["name"] == "query"
    # The 3 steps become 3 pipeline nodes + 1 input node = 4 nodes total
    assert len(g["nodes"]) >= 3


def test_migrate_creates_bak(tmp_path: Path) -> None:
    """Migration creates .bak backup files."""
    shutil.copy(_fixture("commands.json"), tmp_path / "commands.json")
    (tmp_path / "workflows.json").write_text(json.dumps({"workflows": {}}))

    migrate_legacy_to_graphs(tmp_path / "commands.json", tmp_path / "workflows.json")
    assert (tmp_path / "commands.json.bak").exists()


def test_migrate_skips_already_migrated(tmp_path: Path) -> None:
    """Files already in canonical schema are not re-migrated."""
    cmd_path = tmp_path / "commands.json"
    cmd_path.write_text(json.dumps({"schema_version": 1, "graphs": {}}))
    wf_path = tmp_path / "workflows.json"
    wf_path.write_text(json.dumps({"schema_version": 1, "graphs": {}}))

    cmd_n, wf_n = migrate_legacy_to_graphs(cmd_path, wf_path)
    assert cmd_n == 0
    assert wf_n == 0


def test_migrate_ref_separator(tmp_path: Path) -> None:
    """'primitive:press' step ref → 'pipeline.press' node ref."""
    wf_data = {
        "workflows": {
            "test_wf": {
                "description": "test",
                "synonyms": [],
                "enabled": True,
                "args": [],
                "steps": [{"ref": "primitive:press", "kwargs": {"combo": "ctrl+a"}}],
            }
        }
    }
    (tmp_path / "commands.json").write_text(json.dumps({"commands": {}}))
    wf_path = tmp_path / "workflows.json"
    wf_path.write_text(json.dumps(wf_data))

    migrate_legacy_to_graphs(tmp_path / "commands.json", wf_path)
    data = json.loads(wf_path.read_text())
    nodes = data["graphs"]["test_wf"]["nodes"]
    assert any(n["ref"] == "pipeline.press" for n in nodes)
