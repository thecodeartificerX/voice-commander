"""Bare-primitive picker for the ``focus`` verb.

Saying "focus." alone opens a numbered modal of the user's recent windows;
saying the number focuses that window.

The MRU source is injected as a :class:`MruTracker`, and the current
foreground hwnd is read via a callable so both can be faked in unit tests.
The daemon's ``build_streaming_daemon`` wires both at startup.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from voice_commander.picker.mru import MruEntry, MruTracker, _default_resolve_hwnd
from voice_commander.picker.registry import get_global_picker_registry
from voice_commander.picker.types import PickerItem, PickerProvider
from voice_commander.plan import Plan, ToolCall

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FocusPickerSettings:
    cap: int = 7
    exclude_foreground: bool = True
    exclude_self: bool = True


def format_label(entry: MruEntry, max_len: int = 60) -> str:
    """Render an MRU entry as ``"<Pretty-Proc> — <title>"``, truncated."""
    app, title = format_app_title(entry)
    if app and title:
        label = f"{app} — {title}"
    elif app:
        label = app
    else:
        label = title
    if len(label) > max_len:
        label = label[: max_len - 1].rstrip() + "…"
    return label


def format_app_title(entry: MruEntry, max_title_len: int = 70) -> tuple[str, str]:
    """Split an MRU entry into ``(pretty_app, truncated_title)`` for two-line cards."""
    proc = entry.proc_name
    if proc.lower().endswith(".exe"):
        proc = proc[:-4]
    proc = proc[:1].upper() + proc[1:] if proc else ""

    title = entry.title or ""
    # Strip noisy " - <App>" suffixes so the title line doesn't repeat the app cell.
    if proc:
        suffix = f" - {proc}"
        if title.endswith(suffix):
            title = title[: -len(suffix)].rstrip()
        suffix_em = f" — {proc}"
        if title.endswith(suffix_em):
            title = title[: -len(suffix_em)].rstrip()

    if len(title) > max_title_len:
        title = title[: max_title_len - 1].rstrip() + "…"
    return proc, title


def build_focus_picker(
    tracker: MruTracker,
    settings: FocusPickerSettings,
    foreground_hwnd: Callable[[], int],
    enumerate_visible: Callable[[], Iterable[MruEntry]] | None = None,
) -> PickerProvider:
    """Return a :class:`PickerProvider` closure over *tracker* + *settings*.

    ``enumerate_visible`` is the cold-start fallback: when ``tracker``
    doesn't have enough entries (typical on a fresh daemon where the
    user hasn't alt-tabbed yet) the provider tops up from this
    callable. Defaults to a real ``EnumWindows`` walk; tests inject
    ``lambda: ()`` to assert MRU-only behaviour.

    The closure is registered with the global picker registry by
    :func:`register_focus_picker`. Tests build the closure directly.
    """
    enumerate_visible = enumerate_visible or _enumerate_visible_windows

    def _provider() -> list[PickerItem]:
        fg = foreground_hwnd() if settings.exclude_foreground else 0
        self_set = tracker.self_hwnds if settings.exclude_self else frozenset()

        def _keep(e: MruEntry) -> bool:
            if e.hwnd == fg:
                return False
            if e.hwnd in self_set:
                return False
            return True

        entries: list[MruEntry] = tracker.top(settings.cap, predicate=_keep)

        # MRU only populates on foreground *changes*, so a fresh daemon
        # whose user hasn't alt-tabbed yet starts with an empty (or
        # all-foreground) tracker. Without a fallback the picker would
        # miss-chime on every "focus." until the user switches windows
        # enough times. Seed the list by enumerating visible top-level
        # windows that pass the same WS_EX_TRANSPARENT / NOACTIVATE /
        # IsWindowVisible filter we apply to MRU recordings.
        if len(entries) < settings.cap:
            already = {e.hwnd for e in entries}
            for extra in enumerate_visible():
                if extra.hwnd == fg or extra.hwnd in self_set:
                    continue
                if extra.hwnd in already:
                    continue
                entries.append(extra)
                already.add(extra.hwnd)
                if len(entries) >= settings.cap:
                    break

        logger.info(
            "focus picker built items=%d (mru_top=%d cap=%d fg=%d)",
            len(entries),
            len(tracker.snapshot()),
            settings.cap,
            fg,
        )

        items: list[PickerItem] = []
        for e in entries:
            label = format_label(e)
            app, title = format_app_title(e)
            plan = Plan(
                steps=(ToolCall(name="focus", kwargs={"_hwnd": int(e.hwnd)}),),
                raw_response={
                    "router": "picker",
                    "verb": "focus",
                    "selection": label,
                    "hwnd": int(e.hwnd),
                },
            )
            items.append(PickerItem(label=label, action=plan, app=app, title=title))
        return items

    return _provider


def _enumerate_visible_windows() -> Iterable[MruEntry]:
    """Walk visible top-level windows, filtered the same way as MRU recordings.

    Returns newest-first by Z-order is impossible without internal Win32
    state; EnumWindows yields in arbitrary order (commonly top-of-Z-order
    first on Windows 10/11). That's fine for the cold-start case — once
    the user alt-tabs a couple of times, real MRU takes over and the
    fallback contributes nothing.
    """
    try:
        import win32gui
    except ImportError:
        return ()

    found: list[int] = []

    def _enum(hwnd: int, _: object) -> bool:
        found.append(int(hwnd))
        return True

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        logger.exception("EnumWindows failed during picker fallback")
        return ()

    out: list[MruEntry] = []
    for hwnd in found:
        entry = _default_resolve_hwnd(hwnd)
        if entry is not None and entry.title:
            out.append(entry)
    return out


def register_focus_picker(
    tracker: MruTracker,
    settings: FocusPickerSettings,
    foreground_hwnd: Callable[[], int],
) -> None:
    """Register the focus picker on the global registry. Daemon calls this once."""
    provider = build_focus_picker(tracker, settings, foreground_hwnd)
    get_global_picker_registry().register("focus", provider)
