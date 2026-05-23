from __future__ import annotations

import logging
from pathlib import Path

from ..verb_router import _normalize_spoken
from .compile import build_base_primitive_router
from .loader import ModeLoadError, parse_mode_file
from .types import ModeDefinition

logger = logging.getLogger(__name__)


class ModeRegistry:
    """Discovers and holds all mode catalogs under *modes_dir*."""

    def __init__(self, modes_dir: Path) -> None:
        self._dir = Path(modes_dir)
        self._by_trigger: dict[str, ModeDefinition] = {}

    def load_all(self) -> None:
        """Glob ``*.toml`` and (re)build the trigger index. Idempotent."""
        base_router = build_base_primitive_router()
        new_index: dict[str, ModeDefinition] = {}
        if not self._dir.is_dir():
            self._by_trigger = new_index
            return
        for path in sorted(self._dir.glob("*.toml")):
            try:
                d = parse_mode_file(path, base_router)
            except ModeLoadError as e:
                logger.warning("modes: skipping invalid file: %s", e)
                continue
            key = _normalize_spoken(d.trigger)
            if key in new_index:
                logger.warning(
                    "modes: duplicate trigger %r in %s — keeping first", d.trigger, path.name
                )
                continue
            new_index[key] = d
        self._by_trigger = new_index

    # reload is an alias so call sites read intentionally.
    def reload(self) -> None:
        self.load_all()

    def by_trigger(self, trigger: str) -> ModeDefinition | None:
        return self._by_trigger.get(_normalize_spoken(trigger))

    def all(self) -> list[ModeDefinition]:
        return list(self._by_trigger.values())
