"""Migrate legacy commands.json / workflows.json to canonical Graph schema.

Legacy command format:
    {"commands": {"name": {"primitive": "press", "kwargs": {...},
     "description": "...", "synonyms": [...], "enabled": true}}}

Legacy workflow format:
    {"workflows": {"name": {"steps": [{"ref": "primitive:X", "kwargs": {...}}, ...],
     "args": [...], ...}}}

Migration rules:
- Each command becomes a 1-node graph (kind="command") with the single pipeline node.
- Each workflow step becomes a pipeline node; steps are chained with ok→in control edges.
- Workflow args become GraphInput entries; {placeholder} references in step kwargs become
  data edges from input.<name> to the matching kwarg port.
- "primitive:X" ref format becomes "pipeline.X".
- "command:X" / "workflow:X" ref format becomes "command.X" / "workflow.X".
- A .bak file is written before migration (skipped if .bak already exists).
- Files already in canonical schema (schema_version present + graphs key) are skipped.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from voice_commander.commands.graph_schema import CURRENT_SCHEMA_VERSION

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def migrate_legacy_to_graphs(
    commands_path: Path,
    workflows_path: Path,
) -> tuple[int, int]:
    """Migrate both files to canonical schema. Returns (commands_migrated, workflows_migrated)."""
    cmd_n = _migrate_file(commands_path, "commands", kind="command")
    wf_n = _migrate_file(workflows_path, "workflows", kind="workflow")
    return cmd_n, wf_n


def _migrate_file(path: Path, legacy_key: str, kind: str) -> int:
    if not path.exists():
        return 0

    raw = json.loads(path.read_text(encoding="utf-8"))

    # Already migrated?
    if "schema_version" in raw and "graphs" in raw:
        return 0

    # Not a legacy file with our expected key? Skip.
    if legacy_key not in raw:
        return 0

    legacy_entries: dict[str, Any] = raw[legacy_key]
    graphs: dict[str, Any] = {}

    for name, entry in legacy_entries.items():
        g = (
            _migrate_command(name, entry) if kind == "command" else _migrate_workflow(name, entry)
        )
        graphs[name] = g

    # Write .bak (skip if already exists)
    bak_path = path.with_suffix(path.suffix + ".bak")
    if not bak_path.exists():
        bak_path.write_bytes(path.read_bytes())

    canonical = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "graphs": graphs,
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(canonical, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp_path, path)

    logger.info("Migrated %d %s(s) from %s", len(graphs), kind, path)
    return len(graphs)


def _migrate_command(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Convert a legacy command to canonical 1-node graph."""
    primitive = entry.get("primitive", "press")
    kwargs = dict(entry.get("kwargs", {}))
    synonyms = list(entry.get("synonyms", []))
    description = str(entry.get("description", ""))
    enabled = bool(entry.get("enabled", True))

    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "name": name,
        "kind": "command",
        "description": description,
        "synonyms": synonyms,
        "inputs": [],
        "llm_visible": True,
        "strict": True,
        "enabled": enabled,
        "timeout_ms": 5000,
        "foreach_iteration_cap": 50,
        "nodes": [
            {
                "id": "n1",
                "ref": _migrate_ref(primitive),
                "kwargs": kwargs,
                "pos": [80, 120],
            }
        ],
        "edges": [],
    }


def _migrate_workflow(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Convert a legacy workflow to canonical linear DAG."""
    steps: list[dict[str, Any]] = entry.get("steps", [])
    args: list[dict[str, Any]] = entry.get("args", [])
    synonyms = list(entry.get("synonyms", []))
    # Clean synonym placeholder references like "search {query}" → just "search"
    synonyms = [re.sub(r"\s*\{[^}]+\}", "", s).strip() for s in synonyms]
    description = str(entry.get("description", ""))
    enabled = bool(entry.get("enabled", True))

    # Build inputs from args
    inputs = [
        {
            "name": a["name"],
            "type": str(a.get("type", "str")).replace("string", "str"),
            "required": bool(a.get("required", True)),
            "description": str(a.get("description", "")),
        }
        for a in args
    ]

    arg_names = {a["name"] for a in args}

    # Build nodes and edges
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Add input node if there are args
    if args:
        nodes.append({
            "id": "input_node",
            "ref": "value.input",
            "kwargs": {},
            "pos": [0, 120],
        })

    prev_node_id: str | None = None
    for i, step in enumerate(steps):
        ref_raw = step.get("ref", "")
        ref = _migrate_ref(ref_raw)
        raw_kwargs = dict(step.get("kwargs", {}))

        # Resolve {placeholder} → data edges
        node_id = f"n{i + 1}"
        step_kwargs: dict[str, Any] = {}
        for kwarg_name, kwarg_val in raw_kwargs.items():
            if isinstance(kwarg_val, str):
                placeholders = _PLACEHOLDER_RE.findall(kwarg_val)
                if len(placeholders) == 1 and placeholders[0] in arg_names:
                    # Replace entire value with data edge
                    arg_name = placeholders[0]
                    edges.append({
                        "from": f"input_node.{arg_name}",
                        "to": f"{node_id}.{kwarg_name}",
                    })
                    # Still include kwarg as empty string (runtime will override via edge)
                    step_kwargs[kwarg_name] = ""
                else:
                    step_kwargs[kwarg_name] = kwarg_val
            else:
                step_kwargs[kwarg_name] = kwarg_val

        nodes.append({
            "id": node_id,
            "ref": ref,
            "kwargs": step_kwargs,
            "pos": [80 + i * 200, 120],
        })

        # Chain control flow
        if prev_node_id is not None:
            edges.append({"from": f"{prev_node_id}.ok", "to": f"{node_id}.in"})

        prev_node_id = node_id

    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "name": name,
        "kind": "workflow",
        "description": description,
        "synonyms": [s for s in synonyms if s],
        "inputs": inputs,
        "llm_visible": True,
        "strict": True,
        "enabled": enabled,
        "timeout_ms": 10000,
        "foreach_iteration_cap": 50,
        "nodes": nodes,
        "edges": edges,
    }


def _migrate_ref(ref: str) -> str:
    """Convert legacy ref format to canonical ref format."""
    # "primitive:press" → "pipeline.press"
    if ref.startswith("primitive:"):
        return "pipeline." + ref[len("primitive:"):]
    # "command:foo" → "command.foo"
    if ref.startswith("command:"):
        return "command." + ref[len("command:"):]
    # "workflow:foo" → "workflow.foo"
    if ref.startswith("workflow:"):
        return "workflow." + ref[len("workflow:"):]
    # Already canonical or bare name
    if "." not in ref and ":" not in ref:
        return "pipeline." + ref
    return ref
