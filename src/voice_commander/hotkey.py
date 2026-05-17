from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from pynput import keyboard

logger = logging.getLogger(__name__)

# Minimum interval between two dispatches of the same key.
# Rejects hardware key-bounce and driver double-release events (which fire
# well under 50 ms). Intentional rapid re-presses are always many hundreds
# of milliseconds apart and are unaffected.
_DEBOUNCE_S: float = 0.050

KEY_ALIASES: dict[str, keyboard.Key] = {
    "scroll_lock": keyboard.Key.scroll_lock,
    "pause": keyboard.Key.pause,
    "f13": keyboard.Key.f13,
    "caps_lock": keyboard.Key.caps_lock,
    "ctrl_r": keyboard.Key.ctrl_r,
    "ctrl_l": keyboard.Key.ctrl_l,
}


class HotkeyController:
    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        if not bindings:
            raise ValueError("At least one binding required")
        self._dispatch: dict[keyboard.Key, Callable[[], None]] = {}
        for key_name, callback in bindings.items():
            if key_name not in KEY_ALIASES:
                raise ValueError(f"Unknown hotkey '{key_name}'. Known: {sorted(KEY_ALIASES)}")
            self._dispatch[KEY_ALIASES[key_name]] = callback
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()
        # Per-key debounce: maps each bound Key to the monotonic timestamp of
        # its last *dispatched* release. Keys absent from this dict have never
        # fired (treated as last_fire=0.0).
        self._last_fire: dict[keyboard.Key, float] = {}

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        logger.info("HotkeyController started, bindings: %s", list(self._dispatch.keys()))

    def stop(self) -> None:
        if self._listener is not None:
            listener = self._listener
            self._listener = None
            listener.stop()
            # join() with a short timeout to ensure the listener thread has
            # fully exited before we return.  pynput's stop() posts a stop
            # event asynchronously; the join makes teardown deterministic.
            listener.join(timeout=1.0)
            logger.info("HotkeyController stopped")

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        callback = self._dispatch.get(key)
        if callback is None:
            return
        now = time.monotonic()
        last = self._last_fire.get(key, 0.0)
        if now - last < _DEBOUNCE_S:
            logger.debug(
                "Hotkey debounce: dropping double-release for %s (gap=%.1f ms)",
                key,
                (now - last) * 1000,
            )
            return
        self._last_fire[key] = now
        with self._lock:
            try:
                callback()
            except Exception:
                logger.exception("Hotkey callback raised for %s", key)
