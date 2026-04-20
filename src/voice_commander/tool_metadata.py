from __future__ import annotations

import os
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import portalocker

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    phrases: tuple[str, ...]
    description: str
    category: str
    enabled: bool


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
        self._tools_dir = tools_dir
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

        for toml_path in sorted(self._tools_dir.glob("*.toml")):
            file_data = _read_toml(toml_path)
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

            tools_raw[name] = {
                "phrases": list(md.phrases),
                "description": md.description,
                "enabled": md.enabled,
                # Only store per-tool category when it differs from the file default.
                **(
                    {"category": md.category}
                    if md.category != file_data.get("category", "")
                    else {}
                ),
            }

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
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _parse_tool(
    name: str,
    raw: dict[str, object],
    default_category: str,
    source: Path,
) -> ToolMetadata:
    try:
        phrases_raw = raw["phrases"]
        if not isinstance(phrases_raw, list):
            raise TypeError("'phrases' must be a list of strings")
        phrases: tuple[str, ...] = tuple(str(p) for p in phrases_raw)

        description = str(raw.get("description", ""))
        enabled = bool(raw.get("enabled", True))
        category = str(raw.get("category", default_category))
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
                for field_key, field_val in tool_data.items():
                    lines.append(f"{field_key} = {_toml_value(field_val)}")
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
