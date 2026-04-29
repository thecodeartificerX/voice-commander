from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import portalocker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArgMetadata:
    name: str
    type_str: str
    description: str
    required: bool
    default: str | None


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    phrases: tuple[str, ...]
    description: str
    category: str
    enabled: bool
    settle_ms: int = 0
    llm_only: bool = False
    # ``internal`` tools stay dispatchable but are hidden from the LLM.
    # Every primitive in ``primitives.toml`` sets this to True; only
    # user-defined commands/workflows should remain visible.
    internal: bool = False
    # ``system`` tools are hidden from the web UI (/page/primitives, /api/tools,
    # Builder palette) but remain fully dispatchable by the LLM router and daemon.
    system: bool = False
    args: dict[str, ArgMetadata] = field(default_factory=dict)
    returns: dict[str, dict[str, str]] = field(default_factory=dict)


class ToolMetadataError(Exception):
    """Raised when a tool name cannot be located in any sidecar TOML file."""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ToolMetadataStore:
    """Load and save per-tool metadata stored in sidecar TOML files.

    Each ``.toml`` file in *tools_dir* follows the schema::

        category = "clipboard"      # module-level default

        [tools.copy]
        phrases = ["copy", "copy that"]
        description = "Sends Ctrl+C."
        enabled = true

    Tool names (keys under ``[tools]``) must be unique across all TOML files
    in the directory.  The internal index is built the first time ``load_all``
    is called and is refreshed on every subsequent call.
    """

    def __init__(self, tools_dir: Path) -> None:
        # Accept either a directory (normal use) or a single .toml file
        # (useful in tests that construct a fixture file directly).
        if tools_dir.is_file():
            self._tools_dir = tools_dir.parent
            self._single_file: Path | None = tools_dir
        else:
            self._tools_dir = tools_dir
            self._single_file = None
        # name -> toml path; populated by load_all()
        self._index: dict[str, Path] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, ToolMetadata]:
        """Glob ``*.toml`` in *tools_dir*, parse every tool section.

        Rebuilds the internal name→path index as a side-effect.
        Returns a dict keyed by tool name (e.g. ``"copy"``, ``"paste"``).
        """
        result: dict[str, ToolMetadata] = {}
        new_index: dict[str, Path] = {}

        toml_files = (
            [self._single_file] if self._single_file else sorted(self._tools_dir.glob("*.toml"))
        )
        for toml_path in toml_files:
            try:
                file_data = _read_toml(toml_path)
            except ToolMetadataError:
                logger.warning("Skipping corrupt TOML file: %s", toml_path)
                continue
            default_category = str(file_data.get("category", ""))
            tools_raw = file_data.get("tools", {})
            tools_section: dict[str, object] = tools_raw if isinstance(tools_raw, dict) else {}

            for name, raw in tools_section.items():
                if not isinstance(raw, dict):
                    continue
                md = _parse_tool(name, raw, default_category, toml_path)
                result[name] = md
                new_index[name] = toml_path

        self._index = new_index
        return result

    def load_one(self, name: str) -> ToolMetadata:
        """Return metadata for a single tool by name.

        Raises :class:`ToolMetadataError` if the tool is not found.
        """
        # Ensure the index is populated.
        if not self._index:
            self.load_all()

        toml_path = self._index.get(name)
        if toml_path is None:
            raise ToolMetadataError(
                f"Tool '{name}' not found in any TOML file under {self._tools_dir}"
            )

        file_data = _read_toml(toml_path)
        default_category = str(file_data.get("category", ""))
        tools_raw = file_data.get("tools", {})
        tools_section: dict[str, object] = tools_raw if isinstance(tools_raw, dict) else {}

        raw = tools_section.get(name)
        if not isinstance(raw, dict):
            raise ToolMetadataError(f"Tool '{name}' missing or malformed in {toml_path}")

        return _parse_tool(name, raw, default_category, toml_path)

    def save(self, name: str, md: ToolMetadata) -> None:
        """Persist updated metadata for *name* back to its sidecar TOML.

        The entire file is rewritten atomically (``<path>.tmp`` → replace).
        Acquires a per-tool lock via :meth:`file_lock` before touching disk.
        """
        toml_path = self.path_for(name)
        tmp_path = toml_path.with_suffix(".toml.tmp")

        with self.file_lock(name):
            file_data = _read_toml(toml_path)

            # Update the in-memory representation.
            tools_raw = file_data.setdefault("tools", {})
            if not isinstance(tools_raw, dict):
                raise ToolMetadataError(f"'tools' key in {toml_path} is not a TOML table")

            tool_entry: dict[str, object] = {
                "description": md.description,
                "enabled": md.enabled,
                # Only store per-tool category when it differs from the file default.
                **(
                    {"category": md.category}
                    if md.category != file_data.get("category", "")
                    else {}
                ),
            }
            # Only write non-default values for new fields.
            if md.settle_ms != 0:
                tool_entry["settle_ms"] = md.settle_ms
            if md.llm_only:
                tool_entry["llm_only"] = md.llm_only
            if md.internal:
                tool_entry["internal"] = md.internal
            if md.system:
                tool_entry["system"] = md.system
            if md.args:
                tool_entry["args"] = {
                    arg_name: {
                        "type": arg_md.type_str,
                        "description": arg_md.description,
                        "required": arg_md.required,
                        **({"default": arg_md.default} if arg_md.default is not None else {}),
                    }
                    for arg_name, arg_md in md.args.items()
                }
            tools_raw[name] = tool_entry

            # Serialise and write atomically.
            content = _render_toml(file_data)
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, toml_path)

    def path_for(self, name: str) -> Path:
        """Return the TOML path that contains *name*.

        Raises :class:`ToolMetadataError` if not found.
        """
        if not self._index:
            self.load_all()

        path = self._index.get(name)
        if path is None:
            raise ToolMetadataError(
                f"Tool '{name}' not found in any TOML file under {self._tools_dir}"
            )
        return path

    @contextmanager
    def file_lock(self, name: str) -> Iterator[None]:
        """Context manager that acquires an exclusive lock for *name*.

        The lockfile lives at ``tools_dir/.locks/<name>.lock``.
        The ``.locks/`` directory is created if it does not exist.
        """
        locks_dir = self._tools_dir / ".locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        lock_path = locks_dir / f"{name}.lock"

        with portalocker.Lock(str(lock_path), mode="a", timeout=10):
            yield


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ToolMetadataError(f"Invalid TOML in {path}: {exc}") from exc


def _parse_tool(
    name: str,
    raw: dict[str, object],
    default_category: str,
    source: Path,
) -> ToolMetadata:
    try:
        phrases_raw = raw.get("phrases", [])
        if not isinstance(phrases_raw, list):
            phrases_raw = []
        phrases: tuple[str, ...] = tuple(str(p) for p in phrases_raw)

        description = str(raw.get("description", ""))
        enabled = bool(raw.get("enabled", True))
        category = str(raw.get("category", default_category))
        settle_ms_raw = raw.get("settle_ms", 0)
        settle_ms = int(settle_ms_raw) if isinstance(settle_ms_raw, (int, float)) else 0
        llm_only = bool(raw.get("llm_only", False))
        internal = bool(raw.get("internal", False))
        system = bool(raw.get("system", False))

        args: dict[str, ArgMetadata] = {}
        args_raw = raw.get("args")
        if isinstance(args_raw, dict):
            for arg_name, arg_data in args_raw.items():
                if isinstance(arg_data, dict):
                    args[arg_name] = ArgMetadata(
                        name=arg_name,
                        type_str=str(arg_data.get("type", "")),
                        description=str(arg_data.get("description", "")),
                        required=bool(arg_data.get("required", True)),
                        default=str(arg_data["default"]) if "default" in arg_data else None,
                    )

        returns: dict[str, dict[str, str]] = {}
        returns_raw = raw.get("returns")
        if isinstance(returns_raw, dict):
            for port_name, port_data in returns_raw.items():
                if isinstance(port_data, dict):
                    returns[port_name] = {
                        "type": str(port_data.get("type", "")),
                        "description": str(port_data.get("description", "")),
                    }
    except (KeyError, TypeError) as exc:
        raise ToolMetadataError(
            f"Malformed tool section '[tools.{name}]' in {source}: {exc}"
        ) from exc

    return ToolMetadata(
        name=name,
        phrases=phrases,
        description=description,
        category=category,
        enabled=enabled,
        settle_ms=settle_ms,
        llm_only=llm_only,
        internal=internal,
        system=system,
        args=args,
        returns=returns,
    )


# ---------------------------------------------------------------------------
# TOML writer (manual — keeps files human-readable)
# ---------------------------------------------------------------------------


def _render_toml(data: dict[str, object]) -> str:
    """Serialise a two-level TOML structure to a string.

    Supports the schema used by sidecar files:

    * Top-level scalar keys (``category = "…"``) come first.
    * A ``[tools.<name>]`` section for every tool follows, each with
      ``phrases``, ``description``, ``enabled``, and an optional
      ``category`` override.
    """
    lines: list[str] = []

    # --- top-level scalars first -----------------------------------------
    for key, value in data.items():
        if key == "tools":
            continue
        lines.append(f"{key} = {_toml_value(value)}")

    if lines:
        lines.append("")  # blank line before tool sections

    # --- [tools.<name>] sections -----------------------------------------
    tools_section = data.get("tools", {})
    if isinstance(tools_section, dict):
        for tool_name, tool_data in tools_section.items():
            lines.append(f"[tools.{tool_name}]")
            if isinstance(tool_data, dict):
                # Flat scalar/list fields first; defer nested "args" dict-of-dicts.
                args_data: dict[str, object] | None = None
                for field_key, field_val in tool_data.items():
                    if field_key == "args" and isinstance(field_val, dict):
                        args_data = field_val
                        continue
                    lines.append(f"{field_key} = {_toml_value(field_val)}")
                # Render [tools.<name>.args.<arg>] sub-tables after flat fields.
                if args_data:
                    for arg_name, arg_fields in args_data.items():
                        lines.append("")
                        lines.append(f"[tools.{tool_name}.args.{arg_name}]")
                        if isinstance(arg_fields, dict):
                            for arg_key, arg_val in arg_fields.items():
                                lines.append(f"{arg_key} = {_toml_value(arg_val)}")
            lines.append("")  # blank line between sections

    # Trim trailing blank line.
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines) + "\n"


def _toml_value(value: object) -> str:
    """Convert a Python value to its TOML literal representation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        items = ", ".join(_toml_value(v) for v in value)
        return f"[{items}]"
    raise TypeError(f"Cannot serialise {type(value).__name__} to TOML")


def _toml_string(s: str) -> str:
    """Wrap *s* in double quotes, escaping only what TOML requires."""
    escaped = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'
