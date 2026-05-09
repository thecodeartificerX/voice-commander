"""``KeyRecorder`` — record one keyboard chord with system-wide suppression.

The Builder UI's ``press`` primitive needs the user to demonstrate the
chord they want bound. Doing this in the browser (``window.onkeydown`` +
``preventDefault``) fails for browser-chrome shortcuts: Ctrl+L focuses
the address bar, Ctrl+T opens a new tab, F11 toggles fullscreen, etc.
The page never sees the keydown.

This module captures from the daemon side using ``pynput.keyboard.Listener``
with ``suppress=True``, which installs a Windows ``WH_KEYBOARD_LL`` hook
that returns non-zero — keys are swallowed before any other application
or the OS shell sees them. (Ctrl+Alt+Del and Win+L are processed by
``winlogon`` *above* user-mode hooks and cannot be suppressed by any
app; the UI documents that limitation.)

Lifecycle: ``start()`` spawns a daemon Listener + a 15 s timeout timer.
The first non-modifier key press is canonicalised via
:mod:`voice_commander.tools.keyboard_combo` and emitted on the
``EventBus`` as ``key_recorder_captured``. ``cancel()`` / timeout fire
``_cancelled`` / ``_timeout``. Reentrancy is rejected (one record at a
time, daemon-wide). The listener factory is injectable so unit tests can
substitute a fake without touching real keyboard hardware.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

from pynput import keyboard

from ..event_bus import EventBus
from ..tools.keyboard_combo import (
    UnknownKeyError,
    _pynput_modifier_name,
    format_combo,
    pynput_key_to_keyname,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 15.0

EVT_STARTED = "key_recorder_started"
EVT_CAPTURED = "key_recorder_captured"
EVT_CANCELLED = "key_recorder_cancelled"
EVT_TIMEOUT = "key_recorder_timeout"
EVT_FAILED = "key_recorder_failed"


class _ListenerProto(Protocol):
    daemon: bool

    def start(self) -> None: ...
    def stop(self) -> None: ...


ListenerFactory = Callable[..., _ListenerProto]


class KeyRecorder:
    """Single-shot keyboard chord recorder, daemon-wide singleton.

    Parameters
    ----------
    event_bus
        Where ``key_recorder_*`` events are published.
    listener_factory
        Constructor for the underlying keyboard listener; defaults to
        :class:`pynput.keyboard.Listener`. Tests substitute a fake.
    """

    def __init__(
        self,
        event_bus: EventBus,
        *,
        listener_factory: ListenerFactory = keyboard.Listener,
    ) -> None:
        self._bus = event_bus
        self._factory = listener_factory
        self._lock = threading.Lock()
        self._listener: _ListenerProto | None = None
        self._timer: threading.Timer | None = None
        self._modifiers: set[str] = set()
        self._done = True  # only False between successful start() and finalize

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_recording(self) -> bool:
        with self._lock:
            return self._listener is not None and not self._done

    def start(self, timeout: float = DEFAULT_TIMEOUT_S) -> bool:
        """Begin a record session. Returns ``False`` if one is already active."""
        with self._lock:
            if self._listener is not None:
                return False
            self._modifiers = set()
            self._done = False
            try:
                listener = self._factory(
                    on_press=self._on_press,
                    on_release=self._on_release,
                    suppress=True,
                )
            except Exception:
                self._done = True
                logger.exception("KeyRecorder: listener factory raised")
                raise
            try:
                listener.daemon = True
            except Exception:  # pragma: no cover — fakes may not allow this
                pass
            timer = threading.Timer(timeout, self._on_timeout)
            timer.daemon = True
            self._listener = listener
            self._timer = timer

        # Outside the lock: start hardware listeners. If start() raises,
        # roll back state so a retry can succeed.
        try:
            listener.start()
        except Exception:
            with self._lock:
                self._listener = None
                self._timer = None
                self._done = True
            logger.exception("KeyRecorder: listener.start raised")
            raise
        timer.start()
        self._bus.publish(EVT_STARTED, {"timeout": timeout})
        logger.info("KeyRecorder started (timeout=%.1fs)", timeout)
        return True

    def cancel(self) -> None:
        """Stop a running session. Idempotent — no-op if not recording."""
        if self._finalize(EVT_CANCELLED, {}, stop_listener=True):
            logger.info("KeyRecorder cancelled")

    # ------------------------------------------------------------------
    # Listener callbacks (run on the pynput listener thread)
    # ------------------------------------------------------------------

    def _on_press(self, key: object) -> bool | None:
        with self._lock:
            if self._done:
                return False  # listener already finalized — stop self
            mod = _pynput_modifier_name(key)
            if mod is not None:
                self._modifiers.add(mod)
                return None  # keep listening for the main key
            # Non-modifier — terminal capture
            try:
                main = pynput_key_to_keyname(key)
                if main is None:
                    raise UnknownKeyError(f"unrepresentable key: {key!r}")
                combo = format_combo(self._modifiers, main)
            except UnknownKeyError as exc:
                event_type = EVT_FAILED
                payload: dict[str, Any] = {"reason": str(exc)}
            else:
                event_type = EVT_CAPTURED
                payload = {"combo": combo}

        # Outside the lock: publish + tear down. Returning False from this
        # callback also signals the listener to stop, but we must still
        # cancel the timer and clear state.
        self._finalize(event_type, payload, stop_listener=False)
        return False

    def _on_release(self, key: object) -> bool | None:
        with self._lock:
            if self._done:
                return False
            mod = _pynput_modifier_name(key)
            if mod is not None:
                self._modifiers.discard(mod)
        return None

    # ------------------------------------------------------------------
    # Timer callback (runs on threading.Timer's own thread)
    # ------------------------------------------------------------------

    def _on_timeout(self) -> None:
        if self._finalize(EVT_TIMEOUT, {}, stop_listener=True):
            logger.info("KeyRecorder timed out")

    # ------------------------------------------------------------------
    # Internal: single-shot teardown + publish
    # ------------------------------------------------------------------

    def _finalize(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        stop_listener: bool,
    ) -> bool:
        """Atomically transition from running → finalized exactly once.

        Returns ``True`` if this call performed the transition (and the
        caller should treat the publish as observable), ``False`` if a
        prior call already finalized.
        """
        with self._lock:
            if self._done and self._listener is None:
                return False
            self._done = True
            listener = self._listener
            timer = self._timer
            self._listener = None
            self._timer = None

        if timer is not None:
            timer.cancel()
        if stop_listener and listener is not None:
            try:
                listener.stop()
            except Exception:  # pragma: no cover
                logger.exception("KeyRecorder: listener.stop raised")
        self._bus.publish(event_type, payload)
        if event_type == EVT_CAPTURED:
            logger.info("KeyRecorder captured combo=%s", payload.get("combo"))
        elif event_type == EVT_FAILED:
            logger.warning("KeyRecorder failed: %s", payload.get("reason"))
        return True
