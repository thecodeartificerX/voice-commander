"""Standalone entrypoint for the streaming dictation experiment.

    python -m voice_commander.dictation_stream

Press Right Ctrl to start a dictation session; press it again to end — the
transcript is pasted at the cursor. Ctrl-C exits.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from .config import load as load_config
from .session import StreamSession

logger = logging.getLogger(__name__)

_DEBOUNCE_S = 0.05


class SessionToggle:
    """Debounced start/stop toggle for streaming-dictation sessions.

    ``fire`` is called once per Right Ctrl press. The first press starts a
    session; the next press stops it and returns the pasted transcript.
    Presses within ``debounce_s`` of the previous one are dropped (driver
    double-release events).
    """

    def __init__(
        self,
        make_session: Callable[[], object],
        *,
        debounce_s: float = _DEBOUNCE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._make_session = make_session
        self._debounce_s = debounce_s
        self._clock = clock
        self._last = float("-inf")
        self._session: object | None = None

    def fire(self) -> str | None:
        """Handle one hotkey press. Returns pasted text on stop, else None."""
        now = self._clock()
        if now - self._last < self._debounce_s:
            return None
        self._last = now
        if self._session is None:
            self._session = self._make_session()
            self._session.start()  # type: ignore[attr-defined]
            return None
        session, self._session = self._session, None
        return session.stop()  # type: ignore[attr-defined]


def main() -> None:
    """Bind Right Ctrl and toggle streaming-dictation sessions until Ctrl-C."""
    from pynput import keyboard

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(Path("config.toml"))
    logger.info(
        "Streaming dictation ready — endpoint %s. Right Ctrl to dictate, Ctrl-C to quit.",
        config.ws_url,
    )

    toggle = SessionToggle(lambda: StreamSession(config))

    def on_press(key: object) -> None:
        if key is keyboard.Key.ctrl_r:
            toggle.fire()

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    try:
        listener.join()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        listener.stop()


if __name__ == "__main__":
    main()
