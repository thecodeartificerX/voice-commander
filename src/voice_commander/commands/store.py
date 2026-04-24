"""Command + workflow dataclasses and JSON-backed stores.

Schema
------
``commands.json``::

    {
      "commands": {
        "new_tab": {
          "description": "Open a new tab",
          "synonyms": ["new tab", "open new tab"],
          "enabled": true,
          "primitive": "press",
          "kwargs": {"combo": "ctrl+t"}
        }
      }
    }

``workflows.json``::

    {
      "workflows": {
        "search_web": {
          "description": "Search the web",
          "synonyms": ["search {query}", "google {query}"],
          "enabled": true,
          "args": [
            {"name": "query", "type": "string", "required": true}
          ],
          "steps": [
            {"ref": "primitive:focus", "kwargs": {"target": "{default_browser}"}},
            {"ref": "primitive:press", "kwargs": {"combo": "ctrl+t"}},
            {"ref": "primitive:press", "kwargs": {"combo": "ctrl+l"}},
            {"ref": "primitive:type",  "kwargs": {"text": "{query}"}},
            {"ref": "primitive:press", "kwargs": {"combo": "enter"}}
          ]
        }
      }
    }

``ref`` prefix selects the dispatch target:

- ``primitive:<name>`` → look up raw primitive in the registry.
- ``command:<name>``   → look up another user-defined command (one-level
  indirection; commands cannot reference workflows to avoid cycles).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import portalocker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CommandStoreError(ValueError):
    """Raised on malformed or duplicate command/workflow definitions."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandDef:
    """An atomic voice command that invokes exactly one primitive tool."""

    name: str
    description: str
    synonyms: tuple[str, ...]
    primitive: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class WorkflowArg:
    """Declared template placeholder for a workflow."""

    name: str
    type_str: str = "string"
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class WorkflowStep:
    """A single step in a workflow."""

    ref: str  # "primitive:press" or "command:new_tab"
    kwargs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ref_kind(self) -> str:
        return self.ref.split(":", 1)[0] if ":" in self.ref else "primitive"

    @property
    def ref_name(self) -> str:
        return self.ref.split(":", 1)[1] if ":" in self.ref else self.ref


@dataclass(frozen=True)
class WorkflowDef:
    """A named ordered sequence of primitive/command references."""

    name: str
    description: str
    synonyms: tuple[str, ...]
    args: tuple[WorkflowArg, ...]
    steps: tuple[WorkflowStep, ...]
    enabled: bool = True


# ---------------------------------------------------------------------------
# Generic JSON file helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise CommandStoreError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CommandStoreError(f"{path}: top-level JSON must be an object")
    return data


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False)
    tmp_path.write_text(content + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Exclusive lock on a sibling .lock file next to *path*."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(lock_path), mode="a", timeout=10):
        yield


_NAME_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789_")

# Route-level slugs that are permanently reserved and cannot be used as command/workflow names.
# Using any of these as a name would shadow the corresponding admin UI route.
_RESERVED_NAMES = frozenset({"new", "toggle", "delete"})


def _validate_name(kind: str, name: str) -> None:
    if not name:
        raise CommandStoreError(f"{kind} name cannot be empty")
    if not set(name).issubset(_NAME_ALLOWED):
        raise CommandStoreError(f"{kind} name {name!r}: only lowercase a-z, 0-9, and _ allowed")
    if name[0].isdigit():
        raise CommandStoreError(f"{kind} name {name!r}: cannot start with a digit")
    if name in _RESERVED_NAMES:
        raise CommandStoreError(f"{kind} name {name!r}: reserved — choose a different name")


# ---------------------------------------------------------------------------
# CommandStore
# ---------------------------------------------------------------------------


class CommandStore:
    """JSON-backed persistent store of user-defined commands."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> dict[str, CommandDef]:
        raw = _read_json(self._path)
        commands_raw = raw.get("commands", {})
        if not isinstance(commands_raw, dict):
            raise CommandStoreError(f"{self._path}: 'commands' key must map to an object")

        out: dict[str, CommandDef] = {}
        for name, cdata in commands_raw.items():
            if not isinstance(cdata, dict):
                logger.warning("Skipping malformed command %r in %s", name, self._path)
                continue
            out[name] = _parse_command(name, cdata)
        return out

    def save_one(self, cmd: CommandDef) -> None:
        """Upsert a single command and rewrite the file atomically."""
        _validate_name("command", cmd.name)
        with _file_lock(self._path):
            raw = _read_json(self._path)
            commands_raw = raw.setdefault("commands", {})
            if not isinstance(commands_raw, dict):
                raise CommandStoreError(f"{self._path}: 'commands' must be an object")
            commands_raw[cmd.name] = {
                "description": cmd.description,
                "synonyms": list(cmd.synonyms),
                "enabled": cmd.enabled,
                "primitive": cmd.primitive,
                "kwargs": dict(cmd.kwargs),
            }
            _write_json_atomic(self._path, raw)

    def delete(self, name: str) -> bool:
        with _file_lock(self._path):
            raw = _read_json(self._path)
            commands_raw = raw.get("commands", {})
            if not isinstance(commands_raw, dict) or name not in commands_raw:
                return False
            del commands_raw[name]
            _write_json_atomic(self._path, raw)
            return True


def _parse_command(name: str, raw: Mapping[str, Any]) -> CommandDef:
    _validate_name("command", name)
    try:
        description = str(raw.get("description", ""))
        synonyms_raw = raw.get("synonyms", [])
        if not isinstance(synonyms_raw, list):
            raise CommandStoreError(f"command {name!r}: synonyms must be a list")
        synonyms = tuple(str(s) for s in synonyms_raw)
        enabled = bool(raw.get("enabled", True))
        primitive = str(raw.get("primitive", ""))
        if not primitive:
            raise CommandStoreError(f"command {name!r}: primitive is required")
        kwargs_raw = raw.get("kwargs", {})
        if not isinstance(kwargs_raw, dict):
            raise CommandStoreError(f"command {name!r}: kwargs must be an object")
    except TypeError as exc:
        raise CommandStoreError(f"command {name!r}: {exc}") from exc

    return CommandDef(
        name=name,
        description=description,
        synonyms=synonyms,
        primitive=primitive,
        kwargs=dict(kwargs_raw),
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# WorkflowStore
# ---------------------------------------------------------------------------


class WorkflowStore:
    """JSON-backed persistent store of user-defined workflows."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> dict[str, WorkflowDef]:
        raw = _read_json(self._path)
        workflows_raw = raw.get("workflows", {})
        if not isinstance(workflows_raw, dict):
            raise CommandStoreError(f"{self._path}: 'workflows' key must map to an object")

        out: dict[str, WorkflowDef] = {}
        for name, wdata in workflows_raw.items():
            if not isinstance(wdata, dict):
                logger.warning("Skipping malformed workflow %r in %s", name, self._path)
                continue
            out[name] = _parse_workflow(name, wdata)
        return out

    def save_one(self, wf: WorkflowDef) -> None:
        _validate_name("workflow", wf.name)
        with _file_lock(self._path):
            raw = _read_json(self._path)
            workflows_raw = raw.setdefault("workflows", {})
            if not isinstance(workflows_raw, dict):
                raise CommandStoreError(f"{self._path}: 'workflows' must be an object")
            workflows_raw[wf.name] = {
                "description": wf.description,
                "synonyms": list(wf.synonyms),
                "enabled": wf.enabled,
                "args": [
                    {
                        "name": a.name,
                        "type": a.type_str,
                        "required": a.required,
                        "description": a.description,
                    }
                    for a in wf.args
                ],
                "steps": [{"ref": s.ref, "kwargs": dict(s.kwargs)} for s in wf.steps],
            }
            _write_json_atomic(self._path, raw)

    def delete(self, name: str) -> bool:
        with _file_lock(self._path):
            raw = _read_json(self._path)
            workflows_raw = raw.get("workflows", {})
            if not isinstance(workflows_raw, dict) or name not in workflows_raw:
                return False
            del workflows_raw[name]
            _write_json_atomic(self._path, raw)
            return True


def _parse_workflow(name: str, raw: Mapping[str, Any]) -> WorkflowDef:
    _validate_name("workflow", name)
    try:
        description = str(raw.get("description", ""))
        synonyms_raw = raw.get("synonyms", [])
        if not isinstance(synonyms_raw, list):
            raise CommandStoreError(f"workflow {name!r}: synonyms must be a list")
        synonyms = tuple(str(s) for s in synonyms_raw)
        enabled = bool(raw.get("enabled", True))

        args_raw = raw.get("args", [])
        if not isinstance(args_raw, list):
            raise CommandStoreError(f"workflow {name!r}: args must be a list")
        args: list[WorkflowArg] = []
        for a in args_raw:
            if not isinstance(a, dict):
                raise CommandStoreError(f"workflow {name!r}: each arg must be an object")
            arg_name = str(a.get("name", ""))
            if not arg_name:
                raise CommandStoreError(f"workflow {name!r}: arg missing name")
            args.append(
                WorkflowArg(
                    name=arg_name,
                    type_str=str(a.get("type", "string")),
                    required=bool(a.get("required", True)),
                    description=str(a.get("description", "")),
                )
            )

        steps_raw = raw.get("steps", [])
        if not isinstance(steps_raw, list):
            raise CommandStoreError(f"workflow {name!r}: steps must be a list")
        steps: list[WorkflowStep] = []
        for i, s in enumerate(steps_raw):
            if not isinstance(s, dict):
                raise CommandStoreError(f"workflow {name!r} step {i}: must be an object")
            ref = str(s.get("ref", ""))
            if not ref:
                raise CommandStoreError(f"workflow {name!r} step {i}: ref is required")
            kwargs_raw = s.get("kwargs", {})
            if not isinstance(kwargs_raw, dict):
                raise CommandStoreError(f"workflow {name!r} step {i}: kwargs must be an object")
            steps.append(WorkflowStep(ref=ref, kwargs=dict(kwargs_raw)))
    except TypeError as exc:
        raise CommandStoreError(f"workflow {name!r}: {exc}") from exc

    return WorkflowDef(
        name=name,
        description=description,
        synonyms=synonyms,
        args=tuple(args),
        steps=tuple(steps),
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# First-run seeding helpers
# ---------------------------------------------------------------------------


def seed_if_missing(target: Path, default_source: Path) -> bool:
    """Copy *default_source* to *target* if *target* does not exist.

    Returns True when the seed occurred, False when *target* already existed.
    """
    if target.exists():
        return False
    if not default_source.exists():
        raise CommandStoreError(
            f"Starter pack file missing: {default_source} (required for first-run seeding)"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(default_source.read_bytes())
    logger.info("Seeded %s from %s", target, default_source)
    return True
