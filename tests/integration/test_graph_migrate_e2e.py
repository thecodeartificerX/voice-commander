"""Integration test: migrate_legacy_to_graphs end-to-end.

Covers:
- Legacy commands.json / workflows.json are backed up (.bak)
- Migrated files have schema_version=1 and contain the expected graphs
- The migrated files are loadable by GraphStore and parse into correct Graph objects
- Re-running migration on already-migrated files is a no-op (idempotent)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from voice_commander.commands.graph_migrate import migrate_legacy_to_graphs
from voice_commander.commands.graph_schema import CURRENT_SCHEMA_VERSION
from voice_commander.commands.store import GraphStore

# ---------------------------------------------------------------------------
# Paths to the legacy fixtures bundled with the test suite
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "legacy"
_LEGACY_COMMANDS = _FIXTURES_DIR / "commands.json"
_LEGACY_WORKFLOWS = _FIXTURES_DIR / "workflows.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _copy_fixtures(dest: Path) -> tuple[Path, Path]:
    """Copy legacy fixture files into *dest* and return (commands_path, workflows_path)."""
    dest.mkdir(parents=True, exist_ok=True)
    cmd_dst = dest / "commands.json"
    wf_dst = dest / "workflows.json"
    shutil.copy2(_LEGACY_COMMANDS, cmd_dst)
    shutil.copy2(_LEGACY_WORKFLOWS, wf_dst)
    return cmd_dst, wf_dst


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_migrate_creates_bak_files(tmp_path: Path) -> None:
    """migrate_legacy_to_graphs creates .bak backup files for both inputs."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    migrate_legacy_to_graphs(cmd_path, wf_path)

    assert cmd_path.with_suffix(".json.bak").exists(), "commands.json.bak not created"
    assert wf_path.with_suffix(".json.bak").exists(), "workflows.json.bak not created"


@pytest.mark.integration
def test_migrate_bak_preserves_original_content(tmp_path: Path) -> None:
    """The .bak file is byte-for-byte identical to the original legacy file."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)
    original_cmd = cmd_path.read_bytes()
    original_wf = wf_path.read_bytes()

    migrate_legacy_to_graphs(cmd_path, wf_path)

    assert cmd_path.with_suffix(".json.bak").read_bytes() == original_cmd
    assert wf_path.with_suffix(".json.bak").read_bytes() == original_wf


@pytest.mark.integration
def test_migrate_commands_have_schema_version_1(tmp_path: Path) -> None:
    """Migrated commands.json has schema_version: 1 at the top level."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    migrate_legacy_to_graphs(cmd_path, wf_path)

    data = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert data.get("schema_version") == CURRENT_SCHEMA_VERSION
    assert "graphs" in data


@pytest.mark.integration
def test_migrate_workflows_have_schema_version_1(tmp_path: Path) -> None:
    """Migrated workflows.json has schema_version: 1 at the top level."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    migrate_legacy_to_graphs(cmd_path, wf_path)

    data = json.loads(wf_path.read_text(encoding="utf-8"))
    assert data.get("schema_version") == CURRENT_SCHEMA_VERSION
    assert "graphs" in data


@pytest.mark.integration
def test_migrate_commands_contains_expected_graph(tmp_path: Path) -> None:
    """Legacy 'new_tab' command is present in migrated graphs with correct node."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    migrate_legacy_to_graphs(cmd_path, wf_path)

    data = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert "new_tab" in data["graphs"], "Expected 'new_tab' graph in migrated commands"

    g = data["graphs"]["new_tab"]
    assert g["kind"] == "command"
    assert g["enabled"] is True
    assert len(g["nodes"]) == 1
    node = g["nodes"][0]
    assert node["ref"] == "pipeline.press"
    assert node["kwargs"].get("combo") == "ctrl+t"


@pytest.mark.integration
def test_migrate_workflows_contains_expected_graph(tmp_path: Path) -> None:
    """Legacy 'search_web' workflow is present in migrated graphs with 3 nodes."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    migrate_legacy_to_graphs(cmd_path, wf_path)

    data = json.loads(wf_path.read_text(encoding="utf-8"))
    assert "search_web" in data["graphs"], "Expected 'search_web' graph in migrated workflows"

    g = data["graphs"]["search_web"]
    assert g["kind"] == "workflow"
    # 3 steps + 1 input_node = 4 nodes total (value.input + focus + press + type)
    node_refs = [n["ref"] for n in g["nodes"]]
    assert "pipeline.focus" in node_refs
    assert "pipeline.press" in node_refs
    assert "pipeline.type" in node_refs


@pytest.mark.integration
def test_migrate_graphs_loadable_by_graphstore(tmp_path: Path) -> None:
    """GraphStore can parse and load all graphs from both migrated files."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    migrate_legacy_to_graphs(cmd_path, wf_path)

    # Must not raise
    cs = GraphStore(cmd_path, kind="command")
    cmd_graphs = cs.load_all()
    assert "new_tab" in cmd_graphs

    ws = GraphStore(wf_path, kind="workflow")
    wf_graphs = ws.load_all()
    assert "search_web" in wf_graphs


@pytest.mark.integration
def test_migrate_command_graph_parses_correctly(tmp_path: Path) -> None:
    """The migrated 'new_tab' Graph object has the expected field values."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    migrate_legacy_to_graphs(cmd_path, wf_path)

    cs = GraphStore(cmd_path, kind="command")
    g = cs.load_all()["new_tab"]

    assert g.name == "new_tab"
    assert g.kind == "command"
    assert g.enabled is True
    assert len(g.nodes) == 1
    node = g.nodes[0]
    assert node.ref == "pipeline.press"
    assert dict(node.kwargs) == {"combo": "ctrl+t"}


@pytest.mark.integration
def test_migrate_workflow_graph_parses_correctly(tmp_path: Path) -> None:
    """The migrated 'search_web' Graph has inputs and nodes wired correctly."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    migrate_legacy_to_graphs(cmd_path, wf_path)

    ws = GraphStore(wf_path, kind="workflow")
    g = ws.load_all()["search_web"]

    assert g.name == "search_web"
    assert g.kind == "workflow"
    assert len(g.inputs) == 1
    assert g.inputs[0].name == "query"

    # There should be pipeline nodes for focus, press, and type
    refs = [n.ref for n in g.nodes]
    assert "pipeline.focus" in refs
    assert "pipeline.press" in refs
    assert "pipeline.type" in refs


@pytest.mark.integration
def test_migrate_idempotent(tmp_path: Path) -> None:
    """Running migration twice is a no-op; the second call returns (0, 0)."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    cmd_n1, wf_n1 = migrate_legacy_to_graphs(cmd_path, wf_path)
    assert cmd_n1 == 1
    assert wf_n1 == 1

    # Second call: files already have schema_version + graphs keys
    cmd_n2, wf_n2 = migrate_legacy_to_graphs(cmd_path, wf_path)
    assert cmd_n2 == 0, "Second migration of commands should be no-op (0)"
    assert wf_n2 == 0, "Second migration of workflows should be no-op (0)"


@pytest.mark.integration
def test_migrate_returns_counts(tmp_path: Path) -> None:
    """Return value is (commands_migrated, workflows_migrated)."""
    cmd_path, wf_path = _copy_fixtures(tmp_path)

    result = migrate_legacy_to_graphs(cmd_path, wf_path)

    assert isinstance(result, tuple) and len(result) == 2
    cmd_count, wf_count = result
    # Legacy fixtures each have exactly 1 entry
    assert cmd_count == 1
    assert wf_count == 1
