# Elements Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-session "elements" mode — the user says "element"/"elements", the app scans the foreground window's UI Automation tree, draws a numbered hint overlay on every clickable control, and left-clicks the element whose number the user speaks (one-shot).

**Architecture:** A new `voice_commander.elements` package holds a state machine (`ElementsSession`), a UIA scanner, a number parser, and a clicker — all headless and unit-testable. `StreamingDaemon` intercepts transcripts: an entry word starts a scan on a worker thread; while hints are shown the next transcript is consumed as a number. The scanner publishes `elements.show` / `elements.hide` events on the existing `EventBus`; the `voice_sprite` process renders a transparent click-through overlay window in response.

**Tech Stack:** Python 3.11, `uiautomation` (Windows UI Automation wrapper), `pywin32` (foreground window + monitor geometry), `pyautogui` (click), `pyglet` (overlay window), `pytest` + mypy-strict + ruff.

**Reference doc:** `docs/references/uiautomation-library.md` — vendored API reference. Consult it whenever this plan touches the `uiautomation` library; if a signature here disagrees with that file, the vendored file wins.

**Spec:** `docs/superpowers/specs/2026-05-16-elements-mode-design.md`

**Known limitation (documented, not a bug):** Browser *page* content is only visible to UIA when Chrome/Edge run with `--force-renderer-accessibility` (or after another assistive client has activated their accessibility tree). Native app UI works unconditionally. Task 13 (ADR) records this; Task 14 (smoke script) verifies it.

**Deviations from the spec, intentional:**
- `entry_words` is an internal constant `ENTRY_WORDS` in `elements/session.py`, not a config key (avoids list-typed TOML config). The `[elements]` section keeps the three scalar tunables.
- The "UIA missing" check is a one-line `logger.warning` in `StreamingDaemon.__init__`, not a new `validator.py` entry — simpler, same user-visible effect.

---

## File Structure

**New package — `src/voice_commander/elements/`:**
- `__init__.py` — package docstring only.
- `numbers.py` — `parse_number(text) -> int | None`. Pure, no deps.
- `uia.py` — `uia_available()` and `INTERACTIVE_CONTROL_TYPES`. Isolates the `uiautomation` import.
- `scanner.py` — `Element`, `RawControl` dataclasses; `build_elements(...)` (pure); `scan_window(hwnd, ...)` (UIA walk).
- `clicker.py` — `click_point(x, y)`.
- `desktop.py` — `foreground_window()`, `monitor_rect(hwnd)`. Isolates the `pywin32` import.
- `session.py` — `ElementsState` enum, `ENTRY_WORDS`, `ElementsSession` state machine.

**New file — `src/voice_sprite/elements_overlay.py`:**
- `tag_xy(...)` (pure geometry) + `ElementsOverlayWindow` (pyglet overlay).

**Modified:**
- `pyproject.toml` — add `uiautomation` dependency + mypy override.
- `src/voice_commander/config.py` — `ElementsConfig` dataclass + wire into `Config`.
- `config.toml.example` — `[elements]` section.
- `src/voice_commander/daemon.py` — `StreamingDaemon.__init__` params, transcript intercept, scan/click workers, factory wiring, scroll-lock cancel.
- `src/voice_sprite/__main__.py` — handle `elements.show` / `elements.hide` events.

**New docs:**
- `docs/decisions/0087-elements-mode-uia-overlay.md`
- `scripts/elements_live_smoke.py`

**New tests:**
- `tests/unit/test_elements_numbers.py`
- `tests/unit/test_elements_scanner.py`
- `tests/unit/test_elements_clicker.py`
- `tests/unit/test_elements_session.py`
- `tests/unit/test_elements_overlay.py`
- `tests/integration/test_elements_pipeline.py`

---

## Task 1: Dependency + config section

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/voice_commander/config.py`
- Modify: `config.toml.example`

- [ ] **Step 1: Add the `uiautomation` dependency**

In `pyproject.toml`, find the `dependencies = [` list and add this entry alongside the others (keep alphabetical-ish order, near `pyautogui`):

```toml
    "uiautomation>=2.0.29",
```

- [ ] **Step 2: Add the mypy override**

`uiautomation` ships no type stubs. In `pyproject.toml`, search for `[[tool.mypy.overrides]]`. If a block already lists modules with `ignore_missing_imports = true`, add `"uiautomation"` to its `module` list. If no such block exists, append:

```toml
[[tool.mypy.overrides]]
module = ["uiautomation"]
ignore_missing_imports = true
```

- [ ] **Step 3: Install the dependency**

Run: `uv sync`
Expected: resolves and installs `uiautomation` with no error.

- [ ] **Step 4: Add `ElementsConfig` to `config.py`**

In `src/voice_commander/config.py`, add this dataclass next to `DictationConfig` (the existing one around lines 25-30):

```python
@dataclass(frozen=True)
class ElementsConfig:
    """Elements mode — voice-driven UIA element click (ADR 0087)."""

    max_elements: int = 200
    scan_timeout_s: float = 3.0
    hint_timeout_s: float = 8.0
```

- [ ] **Step 5: Wire `ElementsConfig` into `Config`**

In the `Config` dataclass (around lines 148-160), add a field next to `dictation`:

```python
    elements: ElementsConfig = field(default_factory=ElementsConfig)
```

In `Config.load` (around lines 163-216), add this next to the `dictation=_section(...)` line:

```python
            elements=_section(ElementsConfig, raw.get("elements", {})),
```

- [ ] **Step 6: Add the `[elements]` section to `config.toml.example`**

Append to `config.toml.example`:

```toml
[elements]
# Elements mode (ADR 0087) — say "element"/"elements" to scan the foreground
# window for clickable controls, then say a number to click one.
max_elements = 200      # cap on hint tags drawn (bounds scan cost + clutter)
scan_timeout_s = 3.0    # hard cap on the UIA tree walk
hint_timeout_s = 8.0    # overlay auto-dismisses after this many seconds of silence
```

- [ ] **Step 7: Verify config loads**

Run: `python -c "from voice_commander.config import Config; c = Config.load(__import__('pathlib').Path('config.toml.example')); print(c.elements)"`
Expected: prints `ElementsConfig(max_elements=200, scan_timeout_s=3.0, hint_timeout_s=8.0)` with no error.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/voice_commander/config.py config.toml.example
git commit -m "feat(elements): add uiautomation dep + [elements] config section"
```

---

## Task 2: Number parser (`numbers.py`)

**Files:**
- Create: `src/voice_commander/elements/__init__.py`
- Create: `src/voice_commander/elements/numbers.py`
- Test: `tests/unit/test_elements_numbers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_elements_numbers.py`:

```python
"""Unit tests for spoken-number parsing (ADR 0087)."""

import pytest

from voice_commander.elements.numbers import parse_number


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7", 7),
        ("17", 17),
        ("200", 200),
        ("seven", 7),
        ("Seven.", 7),
        ("  nine  ", 9),
        ("zero", 0),
        ("ten", 10),
        ("seventeen", 17),
        ("nineteen", 19),
        ("twenty", 20),
        ("ninety", 90),
        ("twenty three", 23),
        ("twenty-three", 23),
        ("Twenty Three!", 23),
        ("forty two", 42),
    ],
)
def test_parses_numbers(text: str, expected: int) -> None:
    assert parse_number(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   ", "hello", "click that", "five six", "one hundred", "twenty zero", "thirteen four"],
)
def test_rejects_non_numbers(text: str) -> None:
    assert parse_number(text) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_elements_numbers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.elements'`.

- [ ] **Step 3: Create the package + implement `parse_number`**

Create `src/voice_commander/elements/__init__.py`:

```python
"""Elements mode — voice-driven UIA element click (ADR 0087)."""
```

Create `src/voice_commander/elements/numbers.py`:

```python
"""Parse a spoken or written number from a transcript.

Whisper emits numbers either as digits ("17") or as words ("seventeen",
"twenty three"). This module accepts both and rejects everything else;
homophone guessing ("for" -> 4) is deliberately omitted as too error-prone.
"""

from __future__ import annotations

_ONES: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_TEENS: dict[str, int] = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def parse_number(text: str) -> int | None:
    """Return the integer named by ``text``, or ``None`` if it is not a number."""
    cleaned = text.strip().lower().replace("-", " ")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch.isspace())
    tokens = cleaned.split()
    if not tokens:
        return None
    if len(tokens) == 1:
        word = tokens[0]
        if word.isdigit():
            return int(word)
        if word in _ONES:
            return _ONES[word]
        if word in _TEENS:
            return _TEENS[word]
        if word in _TENS:
            return _TENS[word]
        return None
    if len(tokens) == 2:
        tens, ones = tokens
        if tens in _TENS and ones in _ONES and _ONES[ones] != 0:
            return _TENS[tens] + _ONES[ones]
        return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_elements_numbers.py -v`
Expected: PASS — all parametrized cases green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/elements/__init__.py src/voice_commander/elements/numbers.py tests/unit/test_elements_numbers.py
git commit -m "feat(elements): spoken-number parser"
```

---

## Task 3: UIA isolation module (`uia.py`)

**Files:**
- Create: `src/voice_commander/elements/uia.py`
- Test: covered indirectly; no dedicated test (pure import-guard + constant).

- [ ] **Step 1: Implement `uia.py`**

Create `src/voice_commander/elements/uia.py`:

```python
"""Isolates the optional ``uiautomation`` dependency and UIA constants.

Keeping the import behind ``uia_available()`` means the rest of the package
imports cleanly on machines without the library, and the daemon can warn
once at startup instead of crashing.
"""

from __future__ import annotations

# UIA ControlTypeName values for controls a user can click. Matched against
# ``control.ControlTypeName`` during the tree walk in scanner.scan_window.
INTERACTIVE_CONTROL_TYPES: frozenset[str] = frozenset(
    {
        "ButtonControl",
        "HyperlinkControl",
        "MenuItemControl",
        "ListItemControl",
        "TabItemControl",
        "CheckBoxControl",
        "RadioButtonControl",
        "ComboBoxControl",
        "EditControl",
        "SplitButtonControl",
        "TreeItemControl",
    }
)


def uia_available() -> bool:
    """Return True if the ``uiautomation`` library can be imported."""
    try:
        import uiautomation  # noqa: F401
    except Exception:
        return False
    return True
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from voice_commander.elements.uia import uia_available, INTERACTIVE_CONTROL_TYPES; print(uia_available(), len(INTERACTIVE_CONTROL_TYPES))"`
Expected: prints `True 11`.

- [ ] **Step 3: Commit**

```bash
git add src/voice_commander/elements/uia.py
git commit -m "feat(elements): uiautomation isolation module"
```

---

## Task 4: Scanner (`scanner.py`)

**Files:**
- Create: `src/voice_commander/elements/scanner.py`
- Test: `tests/unit/test_elements_scanner.py`

The pure `build_elements` function is fully unit-tested. `scan_window` performs
the live UIA walk and is exercised by the Task 14 smoke script (it needs a real
desktop).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_elements_scanner.py`:

```python
"""Unit tests for elements scanner filtering + indexing (ADR 0087)."""

from voice_commander.elements.scanner import Element, RawControl, build_elements

WINDOW = (0, 0, 1000, 800)  # left, top, right, bottom


def _raw(name: str, bounds: tuple[int, int, int, int], *, offscreen: bool = False) -> RawControl:
    return RawControl(name=name, control_type="ButtonControl", bounds=bounds, is_offscreen=offscreen)


def test_keeps_visible_controls_and_indexes_in_reading_order() -> None:
    raw = [
        _raw("bottom", (10, 500, 110, 540)),
        _raw("top-right", (400, 20, 500, 60)),
        _raw("top-left", (10, 20, 110, 60)),
    ]
    result = build_elements(raw, WINDOW, max_elements=200)
    assert [e.index for e in result] == [1, 2, 3]
    assert [e.label for e in result] == ["top-left", "top-right", "bottom"]


def test_computes_rect_and_center() -> None:
    raw = [_raw("b", (100, 200, 140, 260))]
    [el] = build_elements(raw, WINDOW, max_elements=200)
    assert el.rect == (100, 200, 40, 60)
    assert el.center == (120, 230)


def test_drops_offscreen_controls() -> None:
    raw = [_raw("hidden", (10, 20, 110, 60), offscreen=True)]
    assert build_elements(raw, WINDOW, max_elements=200) == []


def test_drops_zero_area_controls() -> None:
    raw = [_raw("zero", (10, 20, 10, 60)), _raw("neg", (50, 50, 40, 40))]
    assert build_elements(raw, WINDOW, max_elements=200) == []


def test_drops_controls_outside_window_bounds() -> None:
    raw = [_raw("offscreen-right", (1100, 20, 1200, 60))]
    assert build_elements(raw, WINDOW, max_elements=200) == []


def test_dedupes_identical_rects() -> None:
    raw = [_raw("a", (10, 20, 110, 60)), _raw("b", (10, 20, 110, 60))]
    result = build_elements(raw, WINDOW, max_elements=200)
    assert len(result) == 1
    assert result[0].label == "a"


def test_caps_at_max_elements() -> None:
    raw = [_raw(f"b{i}", (10, i * 10, 110, i * 10 + 8)) for i in range(50)]
    result = build_elements(raw, WINDOW, max_elements=10)
    assert len(result) == 10
    assert [e.index for e in result] == list(range(1, 11))


def test_element_is_frozen() -> None:
    el = Element(index=1, label="x", control_type="ButtonControl", rect=(0, 0, 1, 1), center=(0, 0))
    import dataclasses
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        el.index = 2  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_elements_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.elements.scanner'`.

- [ ] **Step 3: Implement `scanner.py`**

Create `src/voice_commander/elements/scanner.py`:

```python
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
```

> **Note on `BoundingRectangle`:** this code reads `.left/.top/.right/.bottom`
> attributes. Confirm against `docs/references/uiautomation-library.md`. If that
> file shows `BoundingRectangle` is a plain 4-tuple, change `rb.left, rb.top,
> rb.right, rb.bottom` and `bb.left, ...` to `rb[0], rb[1], rb[2], rb[3]` and
> `bb[0], ...` respectively. Likewise confirm `WalkControl`'s `includeTop` /
> `maxDepth` keyword names.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_elements_scanner.py -v`
Expected: PASS — all 8 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/elements/scanner.py tests/unit/test_elements_scanner.py
git commit -m "feat(elements): UIA scanner with pure filter/index core"
```

---

## Task 5: Clicker (`clicker.py`)

**Files:**
- Create: `src/voice_commander/elements/clicker.py`
- Test: `tests/unit/test_elements_clicker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_elements_clicker.py`:

```python
"""Unit tests for the elements clicker (ADR 0087)."""

import sys
import types
from unittest.mock import MagicMock


def test_click_point_clicks_at_coordinates(monkeypatch) -> None:
    fake_pyautogui = types.ModuleType("pyautogui")
    fake_pyautogui.click = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    from voice_commander.elements.clicker import click_point

    click_point(640, 360, settle_ms=0)

    fake_pyautogui.click.assert_called_once_with(x=640, y=360)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_elements_clicker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.elements.clicker'`.

- [ ] **Step 3: Implement `clicker.py`**

Create `src/voice_commander/elements/clicker.py`:

```python
"""Left-click at a screen coordinate.

Uses the same ``pyautogui`` + settle-delay approach as the existing ``click``
primitive so a hint click behaves identically to a normal voice click.
"""

from __future__ import annotations

import time


def click_point(x: int, y: int, *, settle_ms: int = 50) -> None:
    """Move the mouse to ``(x, y)`` and left-click, then settle briefly."""
    import pyautogui

    pyautogui.click(x=x, y=y)
    if settle_ms > 0:
        time.sleep(settle_ms / 1000.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_elements_clicker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/elements/clicker.py tests/unit/test_elements_clicker.py
git commit -m "feat(elements): screen-coordinate clicker"
```

---

## Task 6: Desktop geometry helpers (`desktop.py`)

**Files:**
- Create: `src/voice_commander/elements/desktop.py`
- Test: covered by the Task 14 smoke script (needs a real desktop).

- [ ] **Step 1: Implement `desktop.py`**

Create `src/voice_commander/elements/desktop.py`:

```python
"""Foreground-window and monitor geometry helpers.

Isolates the ``pywin32`` calls so the scanner/session modules stay free of
Win32 detail. The overlay only ever covers the monitor that hosts the
foreground window, so ``monitor_rect`` keeps a single, uniform DPI.
"""

from __future__ import annotations


def foreground_window() -> int:
    """Return the HWND of the current foreground window (0 if none)."""
    import win32gui

    return int(win32gui.GetForegroundWindow())


def monitor_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return the full bounds (left, top, right, bottom) of the monitor
    that hosts ``hwnd``."""
    import win32api
    import win32con

    hmon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    info = win32api.GetMonitorInfo(hmon)
    left, top, right, bottom = info["Monitor"]
    return (int(left), int(top), int(right), int(bottom))
```

- [ ] **Step 2: Verify it imports and runs**

Run: `python -c "from voice_commander.elements.desktop import foreground_window, monitor_rect; h = foreground_window(); print('hwnd', h, 'monitor', monitor_rect(h))"`
Expected: prints a non-zero hwnd and a 4-tuple monitor rect, no error.

- [ ] **Step 3: Commit**

```bash
git add src/voice_commander/elements/desktop.py
git commit -m "feat(elements): foreground-window + monitor geometry helpers"
```

---

## Task 7: Session state machine (`session.py`)

**Files:**
- Create: `src/voice_commander/elements/session.py`
- Test: `tests/unit/test_elements_session.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_elements_session.py`:

```python
"""Unit tests for the ElementsSession state machine (ADR 0087)."""

from typing import Any

from voice_commander.elements.scanner import Element
from voice_commander.elements.session import ElementsSession, ElementsState


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


def _elements(count: int) -> list[Element]:
    return [
        Element(index=i, label=f"el{i}", control_type="ButtonControl",
                rect=(i, i, 10, 10), center=(i + 5, i + 5))
        for i in range(1, count + 1)
    ]


MONITOR = (0, 0, 1920, 1080)


def test_starts_idle() -> None:
    session = ElementsSession(_FakeBus(), hint_timeout_s=0.0)
    assert session.state is ElementsState.IDLE
    assert session.active is False


def test_begin_scan_moves_to_scanning() -> None:
    session = ElementsSession(_FakeBus(), hint_timeout_s=0.0)
    session.begin_scan()
    assert session.state is ElementsState.SCANNING
    assert session.active is True


def test_begin_scan_is_noop_when_not_idle() -> None:
    session = ElementsSession(_FakeBus(), hint_timeout_s=0.0)
    session.begin_scan()
    session.begin_scan()
    assert session.state is ElementsState.SCANNING


def test_show_publishes_elements_show() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(2), MONITOR)
    assert session.state is ElementsState.HINTS_SHOWN
    assert bus.events == [
        (
            "elements.show",
            {
                "monitor": [0, 0, 1920, 1080],
                "elements": [
                    {"index": 1, "rect": [1, 1, 10, 10], "label": "el1"},
                    {"index": 2, "rect": [2, 2, 10, 10], "label": "el2"},
                ],
            },
        )
    ]


def test_show_is_noop_when_not_scanning() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.show(_elements(1), MONITOR)  # never began a scan
    assert session.state is ElementsState.IDLE
    assert bus.events == []


def test_fail_returns_to_idle_without_event() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.fail()
    assert session.state is ElementsState.IDLE
    assert bus.events == []


def test_handle_valid_number_returns_element_and_hides() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(3), MONITOR)
    chosen = session.handle_utterance("two")
    assert chosen is not None
    assert chosen.index == 2
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})


def test_handle_out_of_range_number_cancels() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(3), MONITOR)
    assert session.handle_utterance("nine") is None
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})


def test_handle_non_number_cancels() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(3), MONITOR)
    assert session.handle_utterance("never mind") is None
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})


def test_handle_is_noop_when_not_showing() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    assert session.handle_utterance("two") is None
    assert bus.events == []


def test_cancel_from_hints_shown_hides() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(2), MONITOR)
    session.cancel()
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})


def test_cancel_from_scanning_emits_no_event() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.cancel()
    assert session.state is ElementsState.IDLE
    assert bus.events == []


def test_timeout_hides_overlay() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(2), MONITOR)
    session._on_timeout()
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_elements_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.elements.session'`.

- [ ] **Step 3: Implement `session.py`**

Create `src/voice_commander/elements/session.py`:

```python
"""Elements mode state machine (ADR 0087).

One-shot lifecycle: IDLE -> SCANNING -> HINTS_SHOWN -> IDLE. The session owns
no threads of its own except a single auto-dismiss timer; the daemon drives
scanning and clicking on a worker thread and feeds transcripts in via
``handle_utterance``.
"""

from __future__ import annotations

import threading
from enum import Enum, auto
from typing import Any, Protocol

from .numbers import parse_number
from .scanner import Element

# Entry words that start a scan. Kept as a constant (not config) so the
# daemon's transcript intercept needs no list-typed config plumbing.
ENTRY_WORDS: frozenset[str] = frozenset({"element", "elements"})


class _BusLike(Protocol):
    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None: ...


class ElementsState(Enum):
    IDLE = auto()
    SCANNING = auto()
    HINTS_SHOWN = auto()


class ElementsSession:
    """Tracks elements-mode state and emits overlay events."""

    def __init__(self, bus: _BusLike, *, hint_timeout_s: float = 8.0) -> None:
        self._bus = bus
        self._hint_timeout_s = hint_timeout_s
        self._lock = threading.Lock()
        self._state = ElementsState.IDLE
        self._elements: list[Element] = []
        self._timer: threading.Timer | None = None

    @property
    def state(self) -> ElementsState:
        with self._lock:
            return self._state

    @property
    def active(self) -> bool:
        with self._lock:
            return self._state is not ElementsState.IDLE

    def begin_scan(self) -> None:
        """IDLE -> SCANNING. No-op if a scan is already in flight."""
        with self._lock:
            if self._state is not ElementsState.IDLE:
                return
            self._state = ElementsState.SCANNING

    def show(self, elements: list[Element], monitor_rect: tuple[int, int, int, int]) -> None:
        """SCANNING -> HINTS_SHOWN. Publishes ``elements.show`` and arms the
        auto-dismiss timer. No-op unless a scan is in flight."""
        with self._lock:
            if self._state is not ElementsState.SCANNING:
                return
            self._state = ElementsState.HINTS_SHOWN
            self._elements = list(elements)
            self._start_timer_locked()
        self._bus.publish(
            "elements.show",
            {
                "monitor": list(monitor_rect),
                "elements": [
                    {"index": e.index, "rect": list(e.rect), "label": e.label}
                    for e in elements
                ],
            },
        )

    def fail(self) -> None:
        """Abort a scan (no window / nothing found / error). Emits no event;
        the caller plays the miss chime."""
        with self._lock:
            self._cancel_timer_locked()
            self._state = ElementsState.IDLE
            self._elements = []

    def handle_utterance(self, text: str) -> Element | None:
        """Consume a transcript while hints are shown. Returns the chosen
        element for a valid number, or ``None`` (implicit cancel) otherwise.
        Always publishes ``elements.hide`` and returns to IDLE."""
        with self._lock:
            if self._state is not ElementsState.HINTS_SHOWN:
                return None
            self._cancel_timer_locked()
            self._state = ElementsState.IDLE
            elements = self._elements
            self._elements = []
        chosen: Element | None = None
        number = parse_number(text)
        if number is not None and 1 <= number <= len(elements):
            chosen = elements[number - 1]
        self._bus.publish("elements.hide", {})
        return chosen

    def cancel(self) -> None:
        """Force the session back to IDLE (e.g. the voice session closed).
        Publishes ``elements.hide`` only if an overlay was on screen."""
        with self._lock:
            if self._state is ElementsState.IDLE:
                return
            self._cancel_timer_locked()
            was_shown = self._state is ElementsState.HINTS_SHOWN
            self._state = ElementsState.IDLE
            self._elements = []
        if was_shown:
            self._bus.publish("elements.hide", {})

    def _on_timeout(self) -> None:
        """Auto-dismiss callback: fired by the timer after hint_timeout_s."""
        with self._lock:
            if self._state is not ElementsState.HINTS_SHOWN:
                return
            self._state = ElementsState.IDLE
            self._elements = []
            self._timer = None
        self._bus.publish("elements.hide", {})

    def _start_timer_locked(self) -> None:
        if self._hint_timeout_s <= 0:
            return
        self._timer = threading.Timer(self._hint_timeout_s, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_elements_session.py -v`
Expected: PASS — all 13 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/elements/session.py tests/unit/test_elements_session.py
git commit -m "feat(elements): ElementsSession state machine"
```

---

## Task 8: Daemon constructor wiring

**Files:**
- Modify: `src/voice_commander/daemon.py` — imports + `StreamingDaemon.__init__`

- [ ] **Step 1: Add imports**

In `src/voice_commander/daemon.py`, near the existing dictation imports (lines 27-28), add:

```python
from .elements import clicker, desktop, scanner
from .elements.session import ENTRY_WORDS, ElementsSession, ElementsState
from .elements.uia import uia_available
```

- [ ] **Step 2: Add constructor parameters**

In `StreamingDaemon.__init__` (signature around lines 142-167), add two keyword
parameters immediately after `dictation_endpoint: str = "",`:

```python
        elements_session: ElementsSession | None = None,
        elements_max_elements: int = 200,
        elements_scan_timeout_s: float = 3.0,
```

- [ ] **Step 3: Wire the parameters in the constructor body**

In the constructor body, immediately after the dictation wiring block (lines
249-255, which ends with the `dictation_executor` assignment), add:

```python
        # --- Elements mode (ADR 0087) ---
        self._elements_session = elements_session
        self._elements_max_elements = elements_max_elements
        self._elements_scan_timeout_s = elements_scan_timeout_s
        self._elements_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="elements",
        )
        if elements_session is not None and not uia_available():
            logger.warning(
                "Elements mode is configured but the 'uiautomation' library "
                "is unavailable — the entry word will be ignored."
            )
```

- [ ] **Step 4: Verify the daemon still imports**

Run: `python -c "import voice_commander.daemon"`
Expected: no error.

- [ ] **Step 5: Run the existing daemon tests**

Run: `pytest tests/unit/test_daemon_wiring.py tests/unit/test_streaming_daemon.py -v`
Expected: PASS — no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/daemon.py
git commit -m "feat(elements): wire ElementsSession into StreamingDaemon constructor"
```

---

## Task 9: Daemon factory wiring

**Files:**
- Modify: `src/voice_commander/daemon.py` — the factory that builds `DictationSession` + `StreamingDaemon`

- [ ] **Step 1: Construct the `ElementsSession`**

In `daemon.py`, find the dictation construction block (lines 1168-1172):

```python
    # --- Dictation mode (ADR 0086) ---
    dictation_session = DictationSession(
        bus=event_bus,
        end_word=cfg.dictation.end_word,
    )
```

Immediately after it, add:

```python
    # --- Elements mode (ADR 0087) ---
    elements_session = ElementsSession(
        bus=event_bus,
        hint_timeout_s=cfg.elements.hint_timeout_s,
    )
```

- [ ] **Step 2: Pass it to `StreamingDaemon`**

In the `StreamingDaemon(...)` call (around lines 1247-1274), add these arguments
immediately after the `dictation_endpoint=cfg.dictation.endpoint,` line:

```python
        elements_session=elements_session,
        elements_max_elements=cfg.elements.max_elements,
        elements_scan_timeout_s=cfg.elements.scan_timeout_s,
```

- [ ] **Step 3: Verify the daemon imports**

Run: `python -c "import voice_commander.daemon"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add src/voice_commander/daemon.py
git commit -m "feat(elements): construct ElementsSession in the daemon factory"
```

---

## Task 10: Daemon transcript intercept + scan/click workers

**Files:**
- Modify: `src/voice_commander/daemon.py` — transcript intercept, worker methods, scroll-lock cancel
- Test: `tests/integration/test_elements_pipeline.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_elements_pipeline.py`:

```python
"""Integration tests for the elements-mode daemon flow (ADR 0087).

These exercise the daemon's elements-mode worker methods directly. The daemon
shell is built with __new__ (the same pattern as tests/unit/test_window.py) so
no audio/transcription stack is needed.
"""

from typing import Any

import pytest

from voice_commander.elements.scanner import Element
from voice_commander.elements.session import ElementsSession, ElementsState

pytestmark = pytest.mark.integration


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


class _SilentFeedback:
    def __init__(self) -> None:
        self.misses = 0
        self.starts = 0

    def on_recording_start(self) -> None:
        self.starts += 1

    def on_recording_stop(self) -> None:
        pass

    def on_miss(self, text: str, tools: tuple[Any, ...]) -> None:
        self.misses += 1


def _elements(count: int) -> list[Element]:
    return [
        Element(index=i, label=f"el{i}", control_type="ButtonControl",
                rect=(i * 10, i * 10, 20, 20), center=(i * 10 + 10, i * 10 + 10))
        for i in range(1, count + 1)
    ]


def _daemon_shell(session: ElementsSession, feedback: _SilentFeedback) -> Any:
    from voice_commander import daemon as daemon_mod

    daemon = daemon_mod.StreamingDaemon.__new__(daemon_mod.StreamingDaemon)
    daemon._elements_session = session
    daemon._elements_max_elements = 200
    daemon._elements_scan_timeout_s = 3.0
    daemon._feedback = feedback
    return daemon


def test_scan_worker_shows_elements(monkeypatch) -> None:
    from voice_commander import daemon as daemon_mod

    monkeypatch.setattr(daemon_mod.desktop, "foreground_window", lambda: 4242)
    monkeypatch.setattr(daemon_mod.desktop, "monitor_rect", lambda hwnd: (0, 0, 1920, 1080))
    monkeypatch.setattr(daemon_mod.scanner, "scan_window", lambda hwnd, **kw: _elements(3))

    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    _daemon_shell(session, _SilentFeedback())._do_element_scan()

    assert session.state is ElementsState.HINTS_SHOWN
    assert bus.events[-1][0] == "elements.show"
    assert len(bus.events[-1][1]["elements"]) == 3


def test_scan_worker_fails_when_nothing_found(monkeypatch) -> None:
    from voice_commander import daemon as daemon_mod

    monkeypatch.setattr(daemon_mod.desktop, "foreground_window", lambda: 4242)
    monkeypatch.setattr(daemon_mod.desktop, "monitor_rect", lambda hwnd: (0, 0, 1920, 1080))
    monkeypatch.setattr(daemon_mod.scanner, "scan_window", lambda hwnd, **kw: [])

    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    feedback = _SilentFeedback()
    _daemon_shell(session, feedback)._do_element_scan()

    assert session.state is ElementsState.IDLE
    assert feedback.misses == 1


def test_scan_worker_fails_when_no_foreground_window(monkeypatch) -> None:
    from voice_commander import daemon as daemon_mod

    monkeypatch.setattr(daemon_mod.desktop, "foreground_window", lambda: 0)

    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    feedback = _SilentFeedback()
    _daemon_shell(session, feedback)._do_element_scan()

    assert session.state is ElementsState.IDLE
    assert feedback.misses == 1


def test_click_worker_clicks_element_center(monkeypatch) -> None:
    from voice_commander import daemon as daemon_mod

    clicked: list[tuple[int, int]] = []
    monkeypatch.setattr(
        daemon_mod.clicker, "click_point",
        lambda x, y, **kw: clicked.append((x, y)),
    )

    daemon = _daemon_shell(ElementsSession(_FakeBus(), hint_timeout_s=0.0), _SilentFeedback())
    daemon._do_element_click(_elements(3)[1])  # index 2, center (20, 20)

    assert clicked == [(20, 20)]


def test_number_utterance_selects_element() -> None:
    bus = _FakeBus()
    session = ElementsSession(bus, hint_timeout_s=0.0)
    session.begin_scan()
    session.show(_elements(5), (0, 0, 1920, 1080))

    chosen = session.handle_utterance("three")

    assert chosen is not None and chosen.index == 3
    assert session.state is ElementsState.IDLE
    assert bus.events[-1] == ("elements.hide", {})
```

> The daemon shell is built with `__new__` and given only the four attributes
> the worker methods touch (`_elements_session`, `_elements_max_elements`,
> `_elements_scan_timeout_s`, `_feedback`). Full transcript-intercept wiring is
> verified by the manual smoke script (Task 14) and the manual end-to-end check.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_elements_pipeline.py -v`
Expected: FAIL — `AttributeError: 'StreamingDaemon' object has no attribute '_do_element_scan'` (the worker methods are added in Step 4).

- [ ] **Step 3: Add the transcript intercept**

In `daemon.py`, find the dictation intercept block inside `_process_utterance`
(the block starting `if self._dictation_session is not None and
self._dictation_session.active:`, around lines 568-577, ending with its
`return`). Immediately **after** that block, add:

```python
        # Elements mode (ADR 0087): while a scan is in flight or hints are
        # shown, every utterance belongs to elements mode — never a command.
        if self._elements_session is not None:
            state = self._elements_session.state
            if state is ElementsState.SCANNING:
                run.set_status("ok")
                return
            if state is ElementsState.HINTS_SHOWN:
                element = self._elements_session.handle_utterance(result.text)
                if element is not None:
                    self._elements_executor.submit(self._do_element_click, element)
                run.set_status("ok")
                return
            if _normalize_spoken(result.text) in ENTRY_WORDS:
                self._elements_session.begin_scan()
                self._feedback.on_recording_start()  # scan-start chime
                self._elements_executor.submit(self._do_element_scan)
                run.set_status("ok")
                return
```

> `_normalize_spoken` is already imported in `daemon.py` (the dictation code
> uses it). If it is not, add `from .verb_router import _normalize_spoken` to
> the imports.

- [ ] **Step 4: Add the worker methods**

In `daemon.py`, add these two methods to `StreamingDaemon`, placed next to the
dictation finalize methods (near `_finalize_dictation`, around lines 682-747):

```python
    def _do_element_scan(self) -> None:
        """Worker-thread scan: enumerate the foreground window's elements."""
        if self._elements_session is None:
            return
        try:
            hwnd = desktop.foreground_window()
            if hwnd == 0:
                self._elements_session.fail()
                self._feedback.on_miss("(elements: no active window)", ())
                return
            monitor = desktop.monitor_rect(hwnd)
            elements = scanner.scan_window(
                hwnd,
                max_elements=self._elements_max_elements,
                timeout_s=self._elements_scan_timeout_s,
            )
            if not elements:
                self._elements_session.fail()
                self._feedback.on_miss("(elements: nothing clickable found)", ())
                return
            self._elements_session.show(elements, monitor)
        except Exception:
            logger.exception("elements scan failed")
            self._elements_session.fail()
            self._feedback.on_miss("(elements: scan error)", ())

    def _do_element_click(self, element: "scanner.Element") -> None:
        """Worker-thread click: left-click the chosen element's center."""
        try:
            clicker.click_point(*element.center)
        except Exception:
            logger.exception("elements click failed")
            self._feedback.on_miss("(elements: click error)", ())
```

- [ ] **Step 5: Cancel elements mode when the voice session closes**

In `daemon.py`, find the `on_scroll_lock` CLOSE branch (around lines 325-340),
which already contains the dictation cancel:

```python
        if self._dictation_session is not None and self._dictation_session.active:
            self._dictation_session.cancel()
```

Immediately after that, add:

```python
        if self._elements_session is not None and self._elements_session.active:
            self._elements_session.cancel()
```

- [ ] **Step 6: Run the test + the daemon suite**

Run: `pytest tests/integration/test_elements_pipeline.py tests/unit/test_streaming_daemon.py -v`
Expected: PASS — elements tests green, no daemon regressions.

- [ ] **Step 7: Run the type checker**

Run: `mypy src/voice_commander/elements src/voice_commander/daemon.py`
Expected: `Success: no issues found`.

- [ ] **Step 8: Commit**

```bash
git add src/voice_commander/daemon.py tests/integration/test_elements_pipeline.py
git commit -m "feat(elements): daemon transcript intercept + scan/click workers"
```

---

## Task 11: Overlay window (`elements_overlay.py`)

**Files:**
- Create: `src/voice_sprite/elements_overlay.py`
- Test: `tests/unit/test_elements_overlay.py`

The pure `tag_xy` geometry is unit-tested. `ElementsOverlayWindow` is pyglet
glue (constructs labels/shapes, draws a batch) and is exercised visually by the
smoke script — consistent with how `PickerModalWindow` glue is left untested.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_elements_overlay.py`:

```python
"""Unit tests for elements-overlay tag geometry (ADR 0087)."""

from voice_sprite.elements_overlay import tag_xy


def test_tag_xy_flips_to_pyglet_origin_on_primary_monitor() -> None:
    # Element at screen (100, 50), monitor 0..1080 tall, tag 22px high.
    # pyglet origin is bottom-left, so y = 1080 - 50 - 22 = 1008.
    assert tag_xy((100, 50, 80, 30), (0, 0, 1920, 1080), tag_w=20, tag_h=22) == (100, 1008)


def test_tag_xy_is_monitor_local_on_secondary_monitor() -> None:
    # Monitor starts at x=1920; element at screen x=2000 -> local x=80.
    assert tag_xy((2000, 100, 60, 24), (1920, 0, 3840, 1080), tag_w=20, tag_h=22) == (80, 958)


def test_tag_xy_handles_nonzero_monitor_top() -> None:
    # Monitor top at y=-200, height 1080; element at screen y=0 -> local 200.
    # y = 1080 - 200 - 22 = 858.
    assert tag_xy((10, 0, 40, 40), (0, -200, 1920, 880), tag_w=20, tag_h=22) == (10, 858)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_elements_overlay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_sprite.elements_overlay'`.

- [ ] **Step 3: Implement `elements_overlay.py`**

Create `src/voice_sprite/elements_overlay.py`:

```python
"""Transparent click-through overlay that draws numbered hint tags (ADR 0087).

The overlay covers exactly the monitor that hosts the scanned window, so all
tag coordinates share one DPI. Element rects arrive in screen coordinates;
``tag_xy`` converts them to the window's bottom-left pyglet origin.
"""

from __future__ import annotations

from typing import Any

import pyglet

from .win32_flags import apply_click_through

_TAG_H = 22  # tag box height, px
_TAG_PAD = 7  # horizontal padding inside a tag, px
_DIGIT_W = 9  # approximate per-digit width at the chosen font size, px
_TAG_BG = (255, 221, 0)  # Vimium yellow
_TAG_FG = (0, 0, 0, 255)  # black text


def tag_xy(
    element_rect: tuple[int, int, int, int],
    monitor_rect: tuple[int, int, int, int],
    *,
    tag_w: int,
    tag_h: int,
) -> tuple[int, int]:
    """Convert an element's screen rect to the tag's bottom-left position in
    the overlay window's pyglet coordinate space (origin bottom-left)."""
    ex, ey, _ew, _eh = element_rect
    ml, mt, _mr, mb = monitor_rect
    monitor_height = mb - mt
    local_x = ex - ml
    local_y_top = ey - mt
    pyglet_y = monitor_height - local_y_top - tag_h
    return (local_x, pyglet_y)


def _tag_width(text: str) -> int:
    return _TAG_PAD * 2 + _DIGIT_W * len(text)


class ElementsOverlayWindow(pyglet.window.Window):
    """A borderless, transparent, click-through, topmost overlay window."""

    def __init__(
        self,
        monitor_rect: tuple[int, int, int, int],
        elements: list[dict[str, Any]],
    ) -> None:
        ml, mt, mr, mb = monitor_rect
        gl_config = pyglet.gl.Config(
            alpha_size=8, double_buffer=True, sample_buffers=0, samples=0
        )
        super().__init__(
            width=mr - ml,
            height=mb - mt,
            style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
            config=gl_config,
            vsync=False,
            visible=False,
        )
        self.set_location(ml, mt)
        self._batch = pyglet.graphics.Batch()
        self._shapes: list[Any] = []
        self._labels: list[Any] = []
        for element in elements:
            text = str(element["index"])
            tag_w = _tag_width(text)
            x, y = tag_xy(
                tuple(element["rect"]), monitor_rect, tag_w=tag_w, tag_h=_TAG_H
            )
            self._shapes.append(
                pyglet.shapes.Rectangle(
                    x, y, tag_w, _TAG_H, color=_TAG_BG, batch=self._batch
                )
            )
            self._labels.append(
                pyglet.text.Label(
                    text,
                    font_size=11,
                    bold=True,
                    color=_TAG_FG,
                    x=x + tag_w // 2,
                    y=y + _TAG_H // 2,
                    anchor_x="center",
                    anchor_y="center",
                    batch=self._batch,
                )
            )

    def on_draw(self) -> None:
        pyglet.gl.glClearColor(0, 0, 0, 0)  # transparent clear (Windows req.)
        self.clear()
        self._batch.draw()

    def apply_win32_flags(self) -> None:
        """Make the window click-through, topmost and non-activating."""
        hwnd = self.canvas.hwnd if hasattr(self.canvas, "hwnd") else getattr(self, "_hwnd", None)
        if hwnd is not None:
            apply_click_through(int(hwnd))
```

> `WINDOW_STYLE_OVERLAY`, the GL config, the per-frame transparent clear and the
> `canvas.hwnd` HWND lookup all mirror `src/voice_sprite/window.py`. If pyglet's
> API has shifted, copy the exact form from that file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_elements_overlay.py -v`
Expected: PASS — all 3 geometry tests green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/elements_overlay.py tests/unit/test_elements_overlay.py
git commit -m "feat(elements): transparent numbered-hint overlay window"
```

---

## Task 12: Sprite process wiring

**Files:**
- Modify: `src/voice_sprite/__main__.py`

- [ ] **Step 1: Add the import**

In `src/voice_sprite/__main__.py`, near the other `voice_sprite` imports, add:

```python
from .elements_overlay import ElementsOverlayWindow
```

- [ ] **Step 2: Add the overlay controller**

In `__main__.py`, inside the function that defines `on_event` (the same scope,
just above the `def on_event(...)` definition around line 214), add:

```python
    # Elements-mode overlay (ADR 0087). pyglet windows must be created and
    # closed on the event-loop thread, so SSE events are marshalled via
    # pyglet.clock.schedule_once — the same pattern as the picker modal.
    _elements_overlay: list[ElementsOverlayWindow] = []  # holds 0 or 1 window

    def _hide_elements_now() -> None:
        while _elements_overlay:
            window_to_close = _elements_overlay.pop()
            try:
                window_to_close.close()
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass

    def _show_elements(data: dict[str, Any]) -> None:
        def _create(_dt: float) -> None:
            _hide_elements_now()
            monitor = tuple(data.get("monitor", [0, 0, 0, 0]))
            elements = data.get("elements", [])
            if len(monitor) != 4 or not elements:
                return
            overlay = ElementsOverlayWindow(monitor, elements)  # type: ignore[arg-type]
            overlay.apply_win32_flags()
            overlay.set_visible(True)
            _elements_overlay.append(overlay)

        pyglet.clock.schedule_once(_create, 0.0)

    def _hide_elements() -> None:
        pyglet.clock.schedule_once(lambda _dt: _hide_elements_now(), 0.0)
```

- [ ] **Step 3: Route the events in `on_event`**

In the `on_event` function body, at the very top (before the picker handling
around lines 215-228), add:

```python
        if event_type == "elements.show":
            _show_elements(data)
            return
        if event_type == "elements.hide":
            _hide_elements()
            return
```

- [ ] **Step 4: Verify the sprite process imports**

Run: `python -c "import voice_sprite.__main__"`
Expected: no error.

- [ ] **Step 5: Run the sprite test suite**

Run: `pytest tests/unit/test_sprite_main.py tests/unit/test_window.py tests/unit/test_elements_overlay.py -v`
Expected: PASS — no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/voice_sprite/__main__.py
git commit -m "feat(elements): render hint overlay in the sprite process"
```

---

## Task 13: ADR 0087

**Files:**
- Create: `docs/decisions/0087-elements-mode-uia-overlay.md`

- [ ] **Step 1: Write the ADR**

Create `docs/decisions/0087-elements-mode-uia-overlay.md`:

```markdown
# 0087 — Elements mode: UIA element-click overlay

## Status

Accepted.

## Context

Clicking arbitrary on-screen UI by voice previously required authoring a
command graph per target. Users wanted a generic "click that thing" mode for
both native Windows apps and browser page content.

## Decision

Add a one-shot "elements" mode. Saying "element"/"elements" during an open
session scans the foreground window, draws a numbered hint overlay on every
clickable control, and left-clicks the control whose number the user speaks.

- **Capture API: Windows UI Automation.** It is the only API exposing native
  controls *and* browser page trees. OCR cannot distinguish a button from text.
- **Click method: coordinate click.** The clicker moves the mouse to the
  element's center and left-clicks via pyautogui — identical to a human click
  and to the existing `click` primitive. It never depends on an element
  implementing a UIA pattern (browser links frequently do not).
- **Lifecycle: one-shot.** One entry word yields one click, then the mode
  exits. No sticky mode, no explicit cancel word — any non-number utterance or
  an 8s timeout dismisses the overlay.
- **Scope: foreground window only.** Bounds scan cost and overlay clutter, and
  keeps the overlay on a single monitor with uniform DPI.
- **Overlay: transparent click-through pyglet window** in the sprite process,
  driven by `elements.show` / `elements.hide` SSE events (consistent with
  ADR 0045 / 0048 / 0050).

## Consequences

- New `voice_commander.elements` package: scanner, session, numbers, clicker,
  desktop helpers. New `voice_sprite/elements_overlay.py`.
- New `uiautomation` dependency.
- **Browser page content is best-effort.** Chrome/Edge expose their renderer
  accessibility tree to UIA only when launched with
  `--force-renderer-accessibility`, or after another assistive client has
  activated it. Native app UI works unconditionally. This is a documented
  limitation, not a defect.
- Coordinate clicks can miss if the UI moves between scan and click; accepted
  because the mode completes within seconds.
```

- [ ] **Step 2: Commit**

```bash
git add docs/decisions/0087-elements-mode-uia-overlay.md
git commit -m "docs(adr): 0087 elements mode / UIA element-click overlay"
```

---

## Task 14: Live smoke script

**Files:**
- Create: `scripts/elements_live_smoke.py`

This is a manual script (it needs a real desktop + the `uiautomation` library).
It is not part of the automated suite.

- [ ] **Step 1: Write the smoke script**

Create `scripts/elements_live_smoke.py`:

```python
"""Manual smoke test for elements-mode scanning (ADR 0087).

Run with a target window already in the foreground:

    python scripts/elements_live_smoke.py

It prints every clickable element the scanner found in the foreground window.
For a browser, launch Chrome/Edge with --force-renderer-accessibility to see
page content (native chrome shows regardless).
"""

from __future__ import annotations

import time

from voice_commander.elements import desktop, scanner
from voice_commander.elements.uia import uia_available


def main() -> None:
    if not uia_available():
        print("FAIL: the 'uiautomation' library is not importable.")
        return

    print("Switch to your target window — scanning in 3 seconds...")
    time.sleep(3.0)

    hwnd = desktop.foreground_window()
    if hwnd == 0:
        print("FAIL: no foreground window.")
        return

    monitor = desktop.monitor_rect(hwnd)
    started = time.monotonic()
    elements = scanner.scan_window(hwnd, max_elements=200, timeout_s=3.0)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    print(f"hwnd={hwnd}  monitor={monitor}  scan={elapsed_ms:.0f}ms")
    print(f"found {len(elements)} clickable element(s):")
    for element in elements:
        print(
            f"  [{element.index:>3}] {element.control_type:<18} "
            f"rect={element.rect} center={element.center}  {element.label!r}"
        )
    if not elements:
        print("WARNING: nothing found — for a browser, check "
              "--force-renderer-accessibility.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the smoke script against a native window**

Open Windows Settings or File Explorer, then run:
`python scripts/elements_live_smoke.py` (switch to that window during the 3s window).
Expected: prints a non-empty list of buttons / list items with sane rects.

- [ ] **Step 3: Run the smoke script against a browser (optional verification)**

Launch Chrome with `chrome.exe --force-renderer-accessibility`, open any page,
run the script and switch to the browser.
Expected: page links/buttons appear in the list. Without the flag, expect only
the browser's own chrome (tabs, toolbar buttons) — this confirms the documented
limitation.

- [ ] **Step 4: Commit**

```bash
git add scripts/elements_live_smoke.py
git commit -m "test(elements): live UIA scan smoke script"
```

---

## Final Verification

- [ ] **Run the full unit + integration suite**

Run: `pytest tests/unit/test_elements_numbers.py tests/unit/test_elements_scanner.py tests/unit/test_elements_clicker.py tests/unit/test_elements_session.py tests/unit/test_elements_overlay.py tests/integration/test_elements_pipeline.py -v`
Expected: all green.

- [ ] **Run the full type check**

Run: `mypy src/voice_commander/elements src/voice_commander/daemon.py src/voice_sprite/elements_overlay.py src/voice_sprite/__main__.py src/voice_commander/config.py`
Expected: `Success: no issues found`.

- [ ] **Run the linter**

Run: `ruff check src/voice_commander/elements src/voice_sprite/elements_overlay.py scripts/elements_live_smoke.py`
Expected: `All checks passed!`.

- [ ] **Run the existing daemon + sprite suites for regressions**

Run: `pytest tests/unit/test_daemon_wiring.py tests/unit/test_streaming_daemon.py tests/unit/test_sprite_main.py tests/unit/test_window.py tests/unit/test_dictation_session.py -v`
Expected: all green — elements mode introduced no regressions.

- [ ] **Manual end-to-end check**

Start the daemon + sprite, press Scroll Lock, say "elements", confirm the
numbered overlay appears on the foreground window, say a number, confirm the
matching control is clicked and the overlay disappears.
