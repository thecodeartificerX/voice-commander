"""Backend keyboard recorder for Builder UI ``press`` combo capture.

Uses ``pynput.keyboard.Listener(suppress=True)`` so browser-reserved
chords (Ctrl+L, Ctrl+T, F11, ...) are intercepted by the daemon's
WH_KEYBOARD_LL hook before reaching the browser.
"""

from .key_recorder import KeyRecorder

__all__ = ["KeyRecorder"]
