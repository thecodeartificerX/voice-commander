"""System-level infrastructure tools — hidden from the UI, dispatchable by the LLM.

Tools in this module are marked ``system = true`` in their sidecar TOML, which
hides them from ``/page/primitives``, ``/api/tools``, and the Builder palette.
The LLM router and dispatcher treat them identically to user-facing tools.

The leading underscore in the module name is a naming convention that signals
"infrastructure primitive" to human readers; the registry's auto-discovery does
not exclude underscore-prefixed modules.
"""

from __future__ import annotations

from pynput import keyboard

from ..registry import tool

# Module-scope controller: one allocation at import time, reused on every
# speak() call. Per-call construction would also work (negligible cost), but
# module-scope is conventional for pynput Controllers.
_controller = keyboard.Controller()


@tool
def speak() -> None:
    """Toggle Windows voice dictation on/off. Synthesizes a Right Ctrl press."""
    _controller.press(keyboard.Key.ctrl_r)
    _controller.release(keyboard.Key.ctrl_r)
