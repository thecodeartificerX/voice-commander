from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from pynput import keyboard

logger = logging.getLogger(__name__)

KEY_ALIASES: dict[str, keyboard.Key] = {
    "scroll_lock": keyboard.Key.scroll_lock,
    "pause": keyboard.Key.pause,
    "f13": keyboard.Key.f13,
    "caps_lock": keyboard.Key.caps_lock,
}


class HotkeyController:
    def __init__(self, key: str, on_toggle: Callable[[], None]) -> None:
        if key not in KEY_ALIASES:
            raise ValueError(f"Unknown hotkey '{key}'. Known: {sorted(KEY_ALIASES)}")
        self._target = KEY_ALIASES[key]
        self._on_toggle = on_toggle
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        logger.info("HotkeyController started on %s", self._target)

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
        if key != self._target:
            return
        with self._lock:
            try:
                self._on_toggle()
            except Exception:
                logger.exception("on_toggle raised")
