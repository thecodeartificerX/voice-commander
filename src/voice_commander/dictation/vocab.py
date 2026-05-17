"""Vocabulary data model and persistence for the dictation pipeline.

Owns ``vocab.json`` — the single user-editable file that controls the three
post-processing layers: prompt biasing, text corrections, and formatting
commands.

A missing or unparseable file is always treated as an empty ``Vocabulary``
(all three lists empty). Dictation never fails because of ``vocab.json``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_VALID_ACTIONS: frozenset[str] = frozenset({"newline", "paragraph"})


@dataclass(frozen=True)
class Correction:
    """A single ``wrong → right`` text replacement."""

    wrong: str
    right: str


@dataclass(frozen=True)
class Command:
    """A spoken phrase mapped to a formatting action."""

    phrase: str
    action: Literal["newline", "paragraph"]


@dataclass(frozen=True)
class Vocabulary:
    """Complete user vocabulary loaded from ``vocab.json``.

    All three lists are tuples so the dataclass remains hashable and frozen.
    """

    vocab: tuple[str, ...] = ()
    corrections: tuple[Correction, ...] = ()
    commands: tuple[Command, ...] = ()


class VocabStore:
    """Loads/saves ``outputs/dictation/vocab.json``.

    Tolerant of a missing or corrupt file — :meth:`load` always returns a
    valid (possibly empty) :class:`Vocabulary`. Never raises on load.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> Vocabulary:
        """Load and parse ``vocab.json``; return empty ``Vocabulary`` on any failure."""
        if not self._path.exists():
            return Vocabulary()

        try:
            raw = self._path.read_text(encoding="utf-8").strip()
            if not raw:
                return Vocabulary()
            data = json.loads(raw)
        except Exception as exc:
            logger.warning("vocab.json unreadable or invalid JSON: %s", exc)
            return Vocabulary()

        if not isinstance(data, dict):
            logger.warning("vocab.json root is not a JSON object — ignoring")
            return Vocabulary()

        vocab_raw = data.get("vocab")
        if not isinstance(vocab_raw, list):
            vocab_raw = []
        vocab: tuple[str, ...] = tuple(
            str(w) for w in vocab_raw if isinstance(w, str)
        )

        corrections_raw = data.get("corrections")
        if not isinstance(corrections_raw, list):
            corrections_raw = []
        corrections: list[Correction] = []
        for item in corrections_raw:
            if isinstance(item, dict) and "wrong" in item and "right" in item:
                corrections.append(
                    Correction(wrong=str(item["wrong"]), right=str(item["right"]))
                )
            else:
                logger.warning(
                    "vocab.json: malformed correction entry %r — dropped", item
                )

        commands_raw = data.get("commands")
        if not isinstance(commands_raw, list):
            commands_raw = []
        commands: list[Command] = []
        for item in commands_raw:
            if not (isinstance(item, dict) and "phrase" in item and "action" in item):
                continue
            action = str(item["action"])
            if action not in _VALID_ACTIONS:
                logger.warning(
                    "vocab.json: unknown command action %r — entry dropped", action
                )
                continue
            commands.append(
                Command(
                    phrase=str(item["phrase"]),
                    action=action,  # type: ignore[arg-type]  # narrowed by _VALID_ACTIONS check above
                )
            )

        return Vocabulary(
            vocab=vocab,
            corrections=tuple(corrections),
            commands=tuple(commands),
        )

    def save(self, vocab: Vocabulary) -> None:
        """Serialise *vocab* to ``vocab.json``, creating parent directories as needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "vocab": list(vocab.vocab),
            "corrections": [
                {"wrong": c.wrong, "right": c.right} for c in vocab.corrections
            ],
            "commands": [
                {"phrase": cmd.phrase, "action": cmd.action} for cmd in vocab.commands
            ],
        }
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
