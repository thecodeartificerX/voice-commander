"""Unified JSON-backed store for graph definitions (commands and workflows).

File layout (canonical schema):

    {
      "schema_version": 1,
      "graphs": {
        "<graph_name>": { ... GraphDef body, see graph_schema.py ... },
        ...
      }
    }

The wrapper-level ``schema_version`` mirrors the per-graph ``schema_version``
inside each entry; the redundancy lets the loader fail fast on legacy files
without parsing every entry.

Legacy files (no top-level ``schema_version`` and an old ``commands`` /
``workflows`` key with primitive/steps shape) are explicitly rejected — see
the migration script in ``commands/graph_migrate.py`` for the upgrade path.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import portalocker

from voice_commander.commands.graph import Graph, GraphKind
from voice_commander.commands.graph_schema import (
    CURRENT_SCHEMA_VERSION,
    GraphSchemaError,
    parse_graph,
    serialise_graph,
)

logger = logging.getLogger(__name__)


class GraphStoreError(ValueError):
    """Raised on malformed, legacy, or duplicate-name graph stores."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise GraphStoreError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GraphStoreError(f"{path}: top-level JSON must be an object")
    return data


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False)
    tmp_path.write_text(content + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(lock_path), mode="a", timeout=10):
        yield


_NAME_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789_")
_RESERVED_NAMES = frozenset({"new", "toggle", "delete", "builder", "validate", "palette"})


def _validate_name(kind: str, name: str) -> None:
    if not name:
        raise GraphStoreError(f"{kind} name cannot be empty")
    if not set(name).issubset(_NAME_ALLOWED):
        raise GraphStoreError(f"{kind} name {name!r}: only lowercase a-z, 0-9, and _ allowed")
    if name[0].isdigit():
        raise GraphStoreError(f"{kind} name {name!r}: cannot start with a digit")
    if name in _RESERVED_NAMES:
        raise GraphStoreError(f"{kind} name {name!r}: reserved — choose a different name")


# ---------------------------------------------------------------------------
# GraphStore
# ---------------------------------------------------------------------------


class GraphStore:
    """JSON-backed store of graph definitions, keyed by name.

    Each store instance is locked to a single ``kind`` (``"command"`` or
    ``"workflow"``); attempting to save a graph of the wrong kind raises
    :class:`GraphStoreError`. The split lets the LLM tool list curate which
    surface is "command-like" vs "workflow-like" while sharing all runtime
    semantics.
    """

    def __init__(self, path: Path, *, kind: Literal["command", "workflow"]) -> None:
        self._path = path
        self._kind: GraphKind = kind

    @property
    def path(self) -> Path:
        return self._path

    @property
    def kind(self) -> GraphKind:
        return self._kind

    def load_all(self) -> dict[str, Graph]:
        raw = _read_json(self._path)
        if not raw:
            return {}
        self._reject_legacy(raw)
        graphs_raw = raw.get("graphs", {})
        if not isinstance(graphs_raw, dict):
            raise GraphStoreError(f"{self._path}: 'graphs' must be an object")
        out: dict[str, Graph] = {}
        for name, body in graphs_raw.items():
            if not isinstance(body, Mapping):
                logger.warning("Skipping malformed graph %r in %s", name, self._path)
                continue
            try:
                graph = parse_graph(body)
            except GraphSchemaError as exc:
                raise GraphStoreError(f"{self._path}: graph {name!r}: {exc}") from exc
            if graph.name != name:
                raise GraphStoreError(
                    f"{self._path}: graph key {name!r} != graph.name {graph.name!r}"
                )
            if graph.kind != self._kind:
                raise GraphStoreError(
                    f"{self._path}: graph {name!r} has kind={graph.kind!r}, expected {self._kind!r}"
                )
            out[name] = graph
        return out

    def save_one(self, g: Graph) -> None:
        _validate_name(self._kind, g.name)
        if g.kind != self._kind:
            raise GraphStoreError(f"GraphStore[{self._kind}] cannot save graph of kind {g.kind!r}")
        with _file_lock(self._path):
            raw = _read_json(self._path)
            if raw:
                self._reject_legacy(raw)
            raw["schema_version"] = CURRENT_SCHEMA_VERSION
            graphs_raw = raw.setdefault("graphs", {})
            if not isinstance(graphs_raw, dict):
                raise GraphStoreError(f"{self._path}: 'graphs' must be an object")
            graphs_raw[g.name] = serialise_graph(g)
            _write_json_atomic(self._path, raw)

    def delete(self, name: str) -> bool:
        with _file_lock(self._path):
            raw = _read_json(self._path)
            self._reject_legacy(raw)
            graphs_raw = raw.get("graphs", {})
            if not isinstance(graphs_raw, dict) or name not in graphs_raw:
                return False
            del graphs_raw[name]
            _write_json_atomic(self._path, raw)
            return True

    def _reject_legacy(self, raw: dict[str, Any]) -> None:
        """Detect and reject legacy schema (pre-DAG)."""
        if "schema_version" in raw and "graphs" in raw:
            return
        legacy_keys = {"commands", "workflows"}
        if any(k in raw for k in legacy_keys):
            raise GraphStoreError(
                f"{self._path}: legacy schema detected — run "
                f"'uv run python scripts/migrate-graphs.py' to upgrade"
            )
        # File might be partially new (has schema_version but no graphs key) — that's ok.


# ---------------------------------------------------------------------------
# First-run seeding
# ---------------------------------------------------------------------------


def seed_if_missing(target: Path, default_source: Path) -> bool:
    if target.exists():
        return False
    if not default_source.exists():
        raise GraphStoreError(
            f"Starter pack file missing: {default_source} (required for first-run seeding)"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(default_source.read_bytes())
    logger.info("Seeded %s from %s", target, default_source)
    return True
