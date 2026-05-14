"""Visual smoke test for PickerModalWindow.

Boots a single pyglet window, pushes a fake ``picker.open`` payload, draws
one frame, captures the framebuffer to a PNG, and exits.

Usage::

    python scripts/picker_modal_smoke.py [--output PATH] [--items N]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _fake_items(n: int) -> list[dict[str, object]]:
    samples = [
        ("Chrome", "Voice Commander — GitHub"),
        ("Code - Insiders", "picker_modal.py — voice-commander"),
        ("WindowsTerminal", "Voice Commander · pwsh"),
        ("WhatsApp", "WhatsApp"),
        ("Explorer", "C:\\Users\\sakib\\Downloads"),
        ("Comet", "Untitled — Comet"),
        ("Spotify", "Bonobo · Migration"),
        ("Slack", "#engineering — voice-commander"),
        ("Discord", "general — voice-commander"),
    ]
    items: list[dict[str, object]] = []
    for i in range(n):
        app, title = samples[i % len(samples)]
        items.append({"n": i + 1, "label": f"{app} — {title}", "app": app, "title": title})
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/picker_smoke.png")
    parser.add_argument("--items", type=int, default=7)
    parser.add_argument("--hold-sec", type=float, default=1.0,
                        help="Wait this long after capture before quitting (lets a human eyeball the window)")
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Make pyglet runnable in standalone mode.
    import contextlib
    import ctypes
    with contextlib.suppress(AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)

    import pyglet

    from voice_sprite.picker_modal import PickerModalWindow

    modal = PickerModalWindow()
    items = _fake_items(args.items)
    print(f"smoke: pushing {len(items)} items", flush=True)
    modal.show("focus", items)

    # Track readiness so the capture happens after the first real draw,
    # not before pyglet has a chance to flush the GL pipeline.
    state = {"frames": 0, "captured": False, "deadline": None}

    def capture(_dt: float) -> None:
        state["frames"] += 1
        if state["captured"] or modal._window is None:
            return
        if state["frames"] < 2:
            return
        try:
            modal._window.switch_to()
            buf = pyglet.image.get_buffer_manager().get_color_buffer()
            buf.save(str(out_path))
            print(f"smoke: saved {out_path}", flush=True)
        except Exception as e:
            print(f"smoke: capture failed: {e}", file=sys.stderr, flush=True)
            return
        state["captured"] = True
        state["deadline"] = time.monotonic() + args.hold_sec

    def maybe_quit(_dt: float) -> None:
        if state["captured"] and state["deadline"] and time.monotonic() >= state["deadline"]:
            pyglet.app.exit()

    pyglet.clock.schedule_interval(capture, 1 / 30.0)
    pyglet.clock.schedule_interval(maybe_quit, 0.05)

    # Hard cap so a stuck event loop doesn't leak the process forever.
    pyglet.clock.schedule_once(lambda _dt: pyglet.app.exit(), 10.0)

    pyglet.app.run()
    return 0 if state["captured"] else 1


if __name__ == "__main__":
    sys.exit(main())
