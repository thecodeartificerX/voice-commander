"""Scan the foreground window's UI Automation tree for clickable elements.

``build_elements`` is pure (filter + dedupe + sort + index) and unit-tested.
``scan_window`` performs the live UIA walk and depends on the ``uiautomation``
library; it is exercised by scripts/elements_live_smoke.py.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .uia import INTERACTIVE_CONTROL_TYPES, uia_available

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawControl:
    """A control collected from the UIA walk, before filtering/indexing."""

    name: str
    control_type: str
    bounds: tuple[int, int, int, int]  # left, top, right, bottom — screen coords
    is_offscreen: bool


@dataclass(frozen=True)
class Element:
    """A clickable element offered to the user as a numbered hint."""

    index: int  # 1-based, reading order
    label: str
    control_type: str
    rect: tuple[int, int, int, int]  # x, y, w, h — screen coords
    center: tuple[int, int]  # x, y — click point, screen coords


def build_elements(
    raw: list[RawControl],
    window_rect: tuple[int, int, int, int],
    max_elements: int,
) -> list[Element]:
    """Filter, dedupe, sort (reading order) and index raw controls."""
    wl, wt, wr, wb = window_rect
    seen: set[tuple[int, int, int, int]] = set()
    kept: list[tuple[int, int, RawControl]] = []  # (top, left, raw) for sorting
    for rc in raw:
        if rc.is_offscreen:
            continue
        left, top, right, bottom = rc.bounds
        if right <= left or bottom <= top:
            continue  # zero or negative area
        if right <= wl or left >= wr or bottom <= wt or top >= wb:
            continue  # fully outside the window
        if rc.bounds in seen:
            continue  # duplicate rect
        seen.add(rc.bounds)
        kept.append((top, left, rc))
    kept.sort(key=lambda item: (item[0], item[1]))
    elements: list[Element] = []
    for index, (_top, _left, rc) in enumerate(kept[:max_elements], start=1):
        left, top, right, bottom = rc.bounds
        width, height = right - left, bottom - top
        elements.append(
            Element(
                index=index,
                label=rc.name,
                control_type=rc.control_type,
                rect=(left, top, width, height),
                center=(left + width // 2, top + height // 2),
            )
        )
    return elements


def scan_window(
    hwnd: int,
    *,
    max_elements: int = 200,
    timeout_s: float = 3.0,
) -> list[Element]:
    """Walk the UIA tree of ``hwnd`` and return its clickable elements.

    Returns an empty list if the library is unavailable, the window has no
    UIA root, or nothing clickable is found. The walk stops early once
    ``timeout_s`` elapses (Chromium trees can hold thousands of nodes).

    ``BoundingRectangle`` is a named tuple / Rect object that supports both
    attribute access (``.left``, ``.top``, ``.right``, ``.bottom``) and
    positional unpacking — confirmed against docs/references/uiautomation-library.md
    section 6.  ``WalkControl`` keyword names ``includeTop`` / ``maxDepth`` are
    confirmed correct per section 4 of the same reference.
    """
    if not uia_available():
        return []
    import uiautomation as auto

    raw: list[RawControl] = []
    deadline = time.monotonic() + timeout_s
    with auto.UIAutomationInitializerInThread():
        root = auto.ControlFromHandle(hwnd)
        if root is None:
            return []
        rb = root.BoundingRectangle
        window_rect = (rb.left, rb.top, rb.right, rb.bottom)
        for control, _depth in auto.WalkControl(root, includeTop=False, maxDepth=60):
            if time.monotonic() > deadline:
                logger.warning("elements scan hit %.1fs timeout", timeout_s)
                break
            try:
                control_type = control.ControlTypeName
                if control_type not in INTERACTIVE_CONTROL_TYPES:
                    continue
                bb = control.BoundingRectangle
                raw.append(
                    RawControl(
                        name=control.Name or "",
                        control_type=control_type,
                        bounds=(bb.left, bb.top, bb.right, bb.bottom),
                        is_offscreen=bool(control.IsOffscreen),
                    )
                )
            except Exception:  # noqa: BLE001 - a single bad control must not abort the walk
                continue
    return build_elements(raw, window_rect, max_elements)
