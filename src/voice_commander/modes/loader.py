from __future__ import annotations

import tomllib
from pathlib import Path

from ..chain import _HEAD_ALIASES as _CHAIN_HEADS
from ..elements.session import ENTRY_WORDS as _ELEMENTS_WORDS
from ..verb_router import VerbRouter, _normalize_spoken, build_default_rules
from .compile import compile_action
from .types import ModeCommand, ModeDefinition


class ModeLoadError(ValueError):
    """Raised when a mode TOML file is invalid."""


def _reserved_triggers() -> set[str]:
    reserved = {r.name for r in build_default_rules()}
    reserved |= set(_CHAIN_HEADS)
    reserved |= set(_ELEMENTS_WORDS)
    reserved.add("dictate")
    return {_normalize_spoken(w) for w in reserved}


def parse_mode_file(path: Path, base_router: VerbRouter) -> ModeDefinition:
    """Parse a single ``modes/<name>.toml`` file into a ModeDefinition.

    Raises ModeLoadError (naming *path*) on any structural or semantic error.
    """
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ModeLoadError(f"{path.name}: cannot parse TOML: {e}") from e

    meta = raw.get("mode", {})
    if not isinstance(meta, dict):
        raise ModeLoadError(f"{path.name}: [mode] must be a table")

    name = _normalize_spoken(path.stem)
    trigger = _normalize_spoken(str(meta.get("trigger", name)))
    if not trigger:
        raise ModeLoadError(f"{path.name}: empty trigger")
    if trigger in _reserved_triggers():
        raise ModeLoadError(f"{path.name}: trigger {trigger!r} is reserved")
    end_phrase = _normalize_spoken(str(meta.get("end_phrase", f"{trigger} end")))
    badge = str(meta.get("badge", trigger.upper()))

    cmds_raw = raw.get("command", [])
    if not isinstance(cmds_raw, list):
        raise ModeLoadError(f"{path.name}: [[command]] must be an array of tables")

    commands: list[ModeCommand] = []
    for i, c in enumerate(cmds_raw):
        if not isinstance(c, dict):
            raise ModeLoadError(f"{path.name}: command #{i} is not a table")
        phrases = c.get("phrases", [])
        if not isinstance(phrases, list) or not phrases:
            raise ModeLoadError(f"{path.name}: command #{i} needs a non-empty phrases list")
        action = c.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ModeLoadError(f"{path.name}: command #{i} needs a string action")
        try:
            plan = compile_action(base_router, action)
        except ValueError as e:
            raise ModeLoadError(f"{path.name}: command #{i} {e}") from e
        commands.append(ModeCommand(phrases=tuple(str(p) for p in phrases), plan=plan))

    return ModeDefinition(
        name=name,
        trigger=trigger,
        end_phrase=end_phrase,
        badge=badge,
        commands=tuple(commands),
    )
