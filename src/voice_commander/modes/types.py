from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..plan import Plan


@dataclass(frozen=True)
class ModeCommand:
    """One catalog entry: spoken phrases mapped to a pre-compiled plan."""

    phrases: tuple[str, ...]
    plan: Plan


@dataclass(frozen=True)
class ModeDefinition:
    """A named mode: trigger/end words, badge text, and its commands."""

    name: str
    trigger: str
    end_phrase: str
    badge: str
    commands: tuple[ModeCommand, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModeOutcome:
    """Result of routing an utterance while a mode is active."""

    kind: Literal["plan", "exit", "miss"]
    plan: Plan | None = None
