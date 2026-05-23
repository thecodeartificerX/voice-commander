"""ModeSession — daemon-side state machine for the active named mode (ADR 0100).

A sub-state of the voice session: ``try_enter`` is called in normal mode to
enter a mode on its trigger word; ``handle_utterance`` routes in-mode
utterances (end-phrase exits); ``reset`` force-exits on session close.
Publishes ``mode.enter`` / ``mode.exit`` on the event bus.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

from ..verb_router import _normalize_spoken
from .compile import build_base_primitive_router
from .registry import ModeRegistry
from .router import ModeRouter
from .types import ModeDefinition, ModeOutcome

logger = logging.getLogger(__name__)


class _BusLike(Protocol):
    def publish(self, event_type: str, data: dict[str, object] | None = None) -> None: ...


class ModeSession:
    """Tracks the active named mode and routes in-mode utterances.

    Thread-safety: every public method acquires the internal lock, so callers
    on the pipeline thread and the hotkey thread can interact without races.
    """

    def __init__(self, bus: _BusLike, registry: ModeRegistry) -> None:
        self._bus = bus
        self._registry = registry
        self._active: ModeDefinition | None = None
        self._router: ModeRouter | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active is not None

    @property
    def active_mode(self) -> str | None:
        with self._lock:
            return self._active.name if self._active else None

    def try_enter(self, transcript: str) -> ModeDefinition | None:
        """In normal mode: enter a mode if *transcript* is a registered trigger."""
        with self._lock:
            if self._active is not None:
                logger.debug(
                    "try_enter ignored: already in mode %r (must exit first)", self._active.name
                )
                return None
            d = self._registry.by_trigger(_normalize_spoken(transcript))
            if d is None:
                return None
            self._active = d
            self._router = ModeRouter(d, build_base_primitive_router())
        self._bus.publish("mode.enter", {"name": d.name, "badge": d.badge})
        return d

    def handle_utterance(self, transcript: str) -> ModeOutcome:
        """While active: end-phrase exits; else route against the mode catalog."""
        with self._lock:
            if self._active is None or self._router is None:
                return ModeOutcome(kind="miss")
            if _normalize_spoken(transcript) == self._active.end_phrase:
                self._exit_locked("end_phrase")
                return ModeOutcome(kind="exit")
            plan = self._router.route(transcript)
        if plan is None:
            return ModeOutcome(kind="miss")
        return ModeOutcome(kind="plan", plan=plan)

    def _exit_locked(self, reason: str) -> None:
        """Exit the active mode and publish ``mode.exit``. Assumes lock is HELD."""
        if self._active is None:
            return
        name = self._active.name
        self._active = None
        self._router = None
        self._bus.publish("mode.exit", {"name": name, "reason": reason})

    def exit(self, reason: str) -> None:
        """Exit the active mode and publish ``mode.exit``. Idempotent."""
        with self._lock:
            self._exit_locked(reason)

    def reset(self) -> None:
        """Force-exit on session close (Scroll Lock)."""
        self.exit("session_ended")
