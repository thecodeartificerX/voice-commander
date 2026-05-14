"""Bare-primitive picker for the ``tabs`` verb.

Saying "tabs." alone opens a numbered modal of the foreground Chromium
browser's page tabs; saying the number activates that tab. The mic stays
hot — the picker is a sub-state of the running voice session, not a
separate dialog.

Mirrors :mod:`voice_commander.tools.focus_picker` in shape: a builder
returns a :class:`PickerProvider` closure over a foreground-hwnd accessor
and a tab enumerator (both injected so unit tests can fake them without
real UIA), and a thin registration helper wires it onto the global
picker registry. The daemon's ``build_streaming_daemon`` calls
:func:`register_tabs_picker` once at startup.

Empty list semantics: when the foreground window has no tabs (not a
browser, browser without a tab strip, UIA hung) the provider returns
an empty list. The picker framework miss-chimes on empty without
opening an empty modal — matching the "brings up nothing if the active
window doesn't have tabs" requirement.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from voice_commander.picker.registry import get_global_picker_registry
from voice_commander.picker.types import PickerItem, PickerProvider
from voice_commander.plan import Plan, ToolCall
from voice_commander.tools.tabs_uia import TabInfo, list_chromium_tabs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TabsPickerSettings:
    """Tunables for the tabs picker.

    ``cap`` mirrors the focus picker — the modal renders up to this many
    tabs. Very long tab strips are truncated to keep the spoken-number
    range small (saying "seventeen" is reliable; "thirty-four" less so).
    Author can bump the cap if they're a tab hoarder and trust their
    enunciation.
    """

    cap: int = 9


def _format_title(title: str, max_len: int = 70) -> str:
    """Truncate a tab title for the picker card's second line."""
    if len(title) > max_len:
        return title[: max_len - 1].rstrip() + "…"
    return title


def build_tabs_picker(
    foreground_hwnd: Callable[[], int],
    settings: TabsPickerSettings,
    enumerate_tabs: Callable[[int], list[TabInfo]] | None = None,
) -> PickerProvider:
    """Return a :class:`PickerProvider` closure for the ``tabs`` verb.

    ``enumerate_tabs`` defaults to :func:`list_chromium_tabs` — tests
    inject a stub that returns canned ``TabInfo``\\s without touching UIA.
    """
    enumerate_tabs = enumerate_tabs or list_chromium_tabs

    def _provider() -> list[PickerItem]:
        hwnd = int(foreground_hwnd() or 0)
        if not hwnd:
            logger.info("tabs picker: no foreground hwnd")
            return []
        tabs = enumerate_tabs(hwnd)
        if not tabs:
            logger.info("tabs picker: no tabs for hwnd=%d", hwnd)
            return []

        cap = max(1, settings.cap)
        if len(tabs) > cap:
            logger.info(
                "tabs picker: truncating %d tabs to cap=%d", len(tabs), cap
            )
            tabs = tabs[:cap]

        items: list[PickerItem] = []
        for t in tabs:
            display = _format_title(t.title)
            plan = Plan(
                steps=(
                    ToolCall(
                        name="tabs",
                        kwargs={
                            "_browser_hwnd": int(hwnd),
                            "_tab_index": int(t.index),
                            "_tab_title": str(t.title),
                        },
                    ),
                ),
                raw_response={
                    "router": "picker",
                    "verb": "tabs",
                    "selection": display,
                    "browser_hwnd": int(hwnd),
                    "tab_index": int(t.index),
                },
            )
            # Single-line card: every row is from the same browser, so
            # repeating "Comet"/"Chrome" on each card is noise. Put the
            # page title in the ``app`` slot — the modal renders that
            # slot as the bold primary line; leaving ``title`` empty
            # makes it draw a clean one-line row instead of stacking a
            # muted secondary text underneath.
            items.append(
                PickerItem(label=display, action=plan, app=display, title="")
            )
        return items

    return _provider


def register_tabs_picker(
    foreground_hwnd: Callable[[], int],
    settings: TabsPickerSettings,
) -> None:
    """Register the tabs picker on the global registry. Daemon calls this once."""
    provider = build_tabs_picker(foreground_hwnd, settings)
    get_global_picker_registry().register("tabs", provider)
