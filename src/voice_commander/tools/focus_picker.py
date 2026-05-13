"""Bare-primitive picker for the ``focus`` verb.

Saying "focus." alone opens a numbered modal of the user's recent windows;
saying the number focuses that window.

The MRU source is injected as a :class:`MruTracker`, and the current
foreground hwnd is read via a callable so both can be faked in unit tests.
The daemon's ``build_streaming_daemon`` wires both at startup.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from voice_commander.picker.mru import MruEntry, MruTracker
from voice_commander.picker.registry import get_global_picker_registry
from voice_commander.picker.types import PickerItem, PickerProvider
from voice_commander.plan import Plan, ToolCall

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FocusPickerSettings:
    cap: int = 5
    exclude_foreground: bool = True
    exclude_self: bool = True


def format_label(entry: MruEntry, max_len: int = 60) -> str:
    """Render an MRU entry as ``"<Pretty-Proc> — <title>"``, truncated."""
    proc = entry.proc_name
    if proc.lower().endswith(".exe"):
        proc = proc[:-4]
    proc = proc[:1].upper() + proc[1:] if proc else ""
    if proc and entry.title:
        label = f"{proc} — {entry.title}"
    elif proc:
        label = proc
    else:
        label = entry.title
    if len(label) > max_len:
        label = label[: max_len - 1].rstrip() + "…"
    return label


def build_focus_picker(
    tracker: MruTracker,
    settings: FocusPickerSettings,
    foreground_hwnd: Callable[[], int],
) -> PickerProvider:
    """Return a :class:`PickerProvider` closure over *tracker* + *settings*.

    The closure is registered with the global picker registry by
    :func:`register_focus_picker`. Tests build the closure directly.
    """

    def _provider() -> list[PickerItem]:
        fg = foreground_hwnd() if settings.exclude_foreground else 0
        self_set = tracker.self_hwnds if settings.exclude_self else frozenset()

        def _keep(e: MruEntry) -> bool:
            if e.hwnd == fg:
                return False
            if e.hwnd in self_set:
                return False
            return True

        top = tracker.top(settings.cap, predicate=_keep)
        items: list[PickerItem] = []
        for e in top:
            label = format_label(e)
            plan = Plan(
                steps=(ToolCall(name="focus", kwargs={"_hwnd": int(e.hwnd)}),),
                raw_response={
                    "router": "picker",
                    "verb": "focus",
                    "selection": label,
                    "hwnd": int(e.hwnd),
                },
            )
            items.append(PickerItem(label=label, action=plan))
        return items

    return _provider


def register_focus_picker(
    tracker: MruTracker,
    settings: FocusPickerSettings,
    foreground_hwnd: Callable[[], int],
) -> None:
    """Register the focus picker on the global registry. Daemon calls this once."""
    provider = build_focus_picker(tracker, settings, foreground_hwnd)
    get_global_picker_registry().register("focus", provider)
