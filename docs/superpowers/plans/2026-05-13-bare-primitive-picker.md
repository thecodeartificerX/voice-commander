# Bare-Primitive Picker Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a pluggable framework that turns a bare primitive verb (e.g. `focus` said alone) into an on-screen numbered picker — the next utterance picks an item by number — and register one provider for `focus`.

**Architecture:** Daemon owns `PickerSession` state. The pipeline, after publishing `transcript`, checks `picker_session.active`; if active, the transcript is coerced to an integer and dispatched as the chosen item's pre-built `Plan`, bypassing the `VerbRouter`. Otherwise `VerbRouter` runs as today. When `VerbRouter` sees a bare primitive verb that has a registered `BarePickerProvider`, it returns a `Plan` with a single synthetic `__picker.open` step; the daemon intercepts that name before reaching the dispatcher and calls `picker_session.open(...)` instead. The sprite process renders the modal in a new always-on-top pyglet window driven by `picker.open` / `picker.close` SSE events. MRU is sourced from a `WinEventHook(EVENT_SYSTEM_FOREGROUND)` running on a dedicated message-pump thread.

**Tech Stack:** Python 3.11, `pywin32` (`SetWinEventHook`, `EnumWindows`, `GetForegroundWindow`), `pyglet` 2.1.x (sprite-side modal), `pytest`. No new third-party dependencies.

---

## Spec reference

Spec: `docs/superpowers/specs/2026-05-13-bare-primitive-picker-design.md`. Re-read it before starting; this plan assumes its terminology.

## File structure (locked before tasks)

**Create**

| Path | Responsibility |
|---|---|
| `src/voice_commander/picker/__init__.py` | Public exports for the `picker` package. |
| `src/voice_commander/picker/types.py` | `PickerItem` dataclass + `PickerProvider` type alias. |
| `src/voice_commander/picker/registry.py` | `BarePickerRegistry` + `@bare_picker(verb)` decorator + global singleton. |
| `src/voice_commander/picker/coerce.py` | `coerce_number(text, max_n) -> int \| None`. |
| `src/voice_commander/picker/mru.py` | `MruTracker` — ring buffer + WinEventHook pump + filter rules. |
| `src/voice_commander/picker/session.py` | `PickerSession` state machine + EventBus publication. |
| `src/voice_commander/tools/focus_picker.py` | `@bare_picker("focus")` provider using `MruTracker`. |
| `src/voice_sprite/picker_modal.py` | Pyglet always-on-top modal window. |
| `tests/unit/test_picker_types.py` | Tests for `PickerItem`. |
| `tests/unit/test_picker_registry.py` | Tests for registry + decorator. |
| `tests/unit/test_picker_coerce.py` | Table-driven tests for `coerce_number`. |
| `tests/unit/test_picker_mru.py` | Tests for ring buffer + filters (hook driven by fakes). |
| `tests/unit/test_picker_session.py` | State-machine tests with fake `EventBus` + clock. |
| `tests/unit/test_picker_focus_provider.py` | Tests `focus_picker()` reads MRU + produces correct Plans. |
| `tests/integration/test_picker_pipeline.py` | Daemon pipeline integration: bare-verb → modal opens → number → focus dispatched. |
| `docs/decisions/0083-bare-primitive-picker.md` | ADR. |

**Modify**

| Path | Why |
|---|---|
| `src/voice_commander/config.py` | Add `PickerConfig` + `PickerFocusConfig` dataclasses; thread into `Config`. |
| `src/voice_commander/verb_router.py` | Accept optional `picker_registry`; bare-verb + provider → `__picker.open` Plan. |
| `src/voice_commander/daemon.py` | Wire `PickerSession` + `MruTracker`; intercept `__picker.open`; route active-picker transcripts. |
| `src/voice_commander/tools/primitives.py` | `focus()` accepts optional `_hwnd` shortcut path that skips `resolve_window`. |
| `src/voice_commander/tools/primitives.toml` | Document `_hwnd` arg on `[tools.focus.args]`. |
| `src/voice_sprite/__main__.py` | Wire `picker.open` / `picker.close` handlers; instantiate `PickerModalWindow`. |
| `tests/unit/test_verb_router.py` | Update `test_focus_without_tail_misses` — bare `focus` now routes via picker. |
| `CLAUDE.md` | Update "Current state" paragraph with picker description. |
| `docs/architecture.md` | Add `picker/` package contract + sprite modal renderer. |
| `docs/agents/technical-decisions.md` | Append row for ADR 0083. |
| `config.toml` (project root) | Add commented `[picker]` + `[picker.focus]` section. |

## Conventions

- All commits use **Conventional Commits** (project ethos). Scope `picker` for the framework, `picker(focus)` for the focus provider, `sprite(picker)` for sprite modal.
- Run tests on Windows (no GitHub-hosted runner exists per CLAUDE.md). Commands assume PowerShell.
- Imports at the top of files. Never inline a `try/except ImportError` inside `picker/` code — the framework is Windows-only and `pywin32` is a hard requirement (consistent with `resolver.py`'s top-level imports inside functions only for tools that genuinely run cross-platform). Exception: the `MruTracker` may keep its `pywin32` import inside the constructor so unit tests can patch it.
- All public functions and classes get short docstrings; **no block comments inside function bodies** unless they capture a non-obvious WHY (matching the project's existing style).

---

## Task 0: Scaffold the `picker` package

**Files:**
- Create: `src/voice_commander/picker/__init__.py`

- [ ] **Step 1: Create the package directory and empty `__init__.py`**

Write `src/voice_commander/picker/__init__.py`:

```python
"""Bare-primitive picker framework.

When a primitive verb is uttered without an argument (e.g. ``focus`` alone),
the framework opens a numbered on-screen picker built from a registered
``BarePickerProvider``. The next utterance is coerced to an integer and
dispatched as the chosen item's pre-built ``Plan``.

Public exports are exposed here so callers can import from
``voice_commander.picker`` without reaching into submodules.
"""

from voice_commander.picker.coerce import coerce_number
from voice_commander.picker.registry import BarePickerRegistry, bare_picker, get_global_picker_registry
from voice_commander.picker.session import PickerSession
from voice_commander.picker.types import PickerItem, PickerProvider

__all__ = [
    "BarePickerRegistry",
    "PickerItem",
    "PickerProvider",
    "PickerSession",
    "bare_picker",
    "coerce_number",
    "get_global_picker_registry",
]
```

- [ ] **Step 2: Verify the package imports**

This step will fail until later tasks land submodules. Run:

```powershell
python -c "from voice_commander import picker"
```

Expected: ImportError on `coerce_number` (submodules not yet written). That's fine — the package skeleton is in place. We'll come back at the end of Task 6 and confirm the import succeeds.

- [ ] **Step 3: Commit**

```powershell
git add src/voice_commander/picker/__init__.py
git commit -m "feat(picker): scaffold picker package skeleton"
```

---

## Task 1: `PickerItem` dataclass + `PickerProvider` type alias

**Files:**
- Create: `src/voice_commander/picker/types.py`
- Create: `tests/unit/test_picker_types.py`

- [ ] **Step 1: Write the failing test**

Write `tests/unit/test_picker_types.py`:

```python
from __future__ import annotations

import dataclasses

import pytest

from voice_commander.picker.types import PickerItem
from voice_commander.plan import Plan, ToolCall


def _plan(name: str = "focus") -> Plan:
    return Plan(
        steps=(ToolCall(name=name, kwargs={}),),
        raw_response={"router": "picker"},
    )


def test_picker_item_holds_label_and_action():
    item = PickerItem(label="Chrome", action=_plan())
    assert item.label == "Chrome"
    assert item.action.steps[0].name == "focus"


def test_picker_item_is_frozen():
    item = PickerItem(label="Chrome", action=_plan())
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.label = "Edge"  # type: ignore[misc]


def test_picker_item_requires_both_fields():
    with pytest.raises(TypeError):
        PickerItem(label="Chrome")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        PickerItem(action=_plan())  # type: ignore[call-arg]
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
pytest tests/unit/test_picker_types.py -v
```

Expected: collection or import error — `voice_commander.picker.types` does not exist yet.

- [ ] **Step 3: Implement `PickerItem` + `PickerProvider`**

Write `src/voice_commander/picker/types.py`:

```python
"""Picker primitive types — items + the provider callable shape.

Kept in a separate module so consumers can import the types without dragging
in the `MruTracker` (which imports pywin32).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from voice_commander.plan import Plan


@dataclass(frozen=True)
class PickerItem:
    """One row of a picker — what the modal shows + what to dispatch on selection.

    Attributes
    ----------
    label : str
        Display text rendered next to the number in the modal
        (e.g. ``"Chrome — voice-commander"``).
    action : Plan
        The plan dispatched when this item is selected. Pre-built by the
        provider so dispatch needs no extra resolution at selection time.
    """

    label: str
    action: Plan


PickerProvider = Callable[[], list[PickerItem]]
"""Zero-arg callable that produces a fresh list of items every time the
picker opens. Empty list means "nothing to show" — the framework will
miss-chime instead of opening an empty modal."""
```

- [ ] **Step 4: Run the test to verify it passes**

```powershell
pytest tests/unit/test_picker_types.py -v
```

Expected: all three tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/picker/types.py tests/unit/test_picker_types.py
git commit -m "feat(picker): add PickerItem + PickerProvider types"
```

---

## Task 2: `BarePickerRegistry` + `@bare_picker` decorator

**Files:**
- Create: `src/voice_commander/picker/registry.py`
- Create: `tests/unit/test_picker_registry.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_picker_registry.py`:

```python
from __future__ import annotations

import pytest

from voice_commander.picker.registry import (
    BarePickerRegistry,
    DuplicatePickerError,
    bare_picker,
    get_global_picker_registry,
    reset_global_picker_registry,
)
from voice_commander.picker.types import PickerItem
from voice_commander.plan import Plan, ToolCall


def _items() -> list[PickerItem]:
    return [
        PickerItem(
            label="Chrome",
            action=Plan(
                steps=(ToolCall(name="focus", kwargs={"_hwnd": 1}),),
                raw_response={"router": "picker"},
            ),
        )
    ]


def test_register_and_lookup():
    reg = BarePickerRegistry()
    reg.register("focus", _items)
    assert reg.has("focus")
    assert reg.verbs() == ("focus",)
    provider = reg.get("focus")
    assert provider is not None
    assert provider()[0].label == "Chrome"


def test_get_unknown_returns_none():
    reg = BarePickerRegistry()
    assert reg.get("nope") is None
    assert reg.has("nope") is False


def test_register_duplicate_raises():
    reg = BarePickerRegistry()
    reg.register("focus", _items)
    with pytest.raises(DuplicatePickerError):
        reg.register("focus", _items)


def test_verb_normalised_to_lowercase():
    reg = BarePickerRegistry()
    reg.register("Focus", _items)
    assert reg.has("focus")
    assert reg.has("FOCUS")
    assert reg.verbs() == ("focus",)


def test_decorator_registers_on_global_registry():
    reset_global_picker_registry()
    try:

        @bare_picker("focus")
        def provider() -> list[PickerItem]:
            return _items()

        reg = get_global_picker_registry()
        assert reg.has("focus")
        assert reg.get("focus") is provider
    finally:
        reset_global_picker_registry()


def test_decorator_rejects_empty_verb():
    reset_global_picker_registry()
    try:
        with pytest.raises(ValueError):

            @bare_picker("")
            def _p() -> list[PickerItem]:
                return _items()
    finally:
        reset_global_picker_registry()
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/unit/test_picker_registry.py -v
```

Expected: import error — `registry` module does not exist.

- [ ] **Step 3: Implement the registry**

Write `src/voice_commander/picker/registry.py`:

```python
"""Bare-primitive picker registry.

The registry maps a primitive verb name (``"focus"``, ``"open"``, …) to a
zero-arg :class:`PickerProvider`. The verb name is lowercased on register
and lookup so casing differences from the transcript pipeline cannot cause
a miss.

The ``@bare_picker(verb)`` decorator registers on the module-level global
singleton, mirroring how :func:`voice_commander.registry.tool` works for
primitive tools.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from voice_commander.picker.types import PickerProvider

P = TypeVar("P", bound=PickerProvider)


class DuplicatePickerError(Exception):
    """Raised when two providers try to register the same verb."""


class BarePickerRegistry:
    def __init__(self) -> None:
        self._by_verb: dict[str, PickerProvider] = {}

    def register(self, verb: str, provider: PickerProvider) -> None:
        key = verb.lower().strip()
        if not key:
            raise ValueError("verb must be a non-empty string")
        if key in self._by_verb:
            raise DuplicatePickerError(f"Picker for verb {key!r} already registered")
        self._by_verb[key] = provider

    def get(self, verb: str) -> PickerProvider | None:
        return self._by_verb.get(verb.lower().strip())

    def has(self, verb: str) -> bool:
        return verb.lower().strip() in self._by_verb

    def verbs(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_verb))


_GLOBAL_PICKER_REGISTRY = BarePickerRegistry()


def get_global_picker_registry() -> BarePickerRegistry:
    return _GLOBAL_PICKER_REGISTRY


def reset_global_picker_registry() -> None:
    """Test hook — drop every registered provider."""
    global _GLOBAL_PICKER_REGISTRY
    _GLOBAL_PICKER_REGISTRY = BarePickerRegistry()


def bare_picker(verb: str) -> Callable[[P], P]:
    """Register *func* as the provider for bare invocation of *verb*.

    Usage::

        @bare_picker("focus")
        def focus_picker() -> list[PickerItem]:
            ...
    """

    def _register(func: P) -> P:
        get_global_picker_registry().register(verb, func)
        return func

    return _register
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_picker_registry.py -v
```

Expected: all six tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/picker/registry.py tests/unit/test_picker_registry.py
git commit -m "feat(picker): add BarePickerRegistry + @bare_picker decorator"
```

---

## Task 3: `coerce_number` — text → integer

**Files:**
- Create: `src/voice_commander/picker/coerce.py`
- Create: `tests/unit/test_picker_coerce.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_picker_coerce.py`:

```python
from __future__ import annotations

import pytest

from voice_commander.picker.coerce import coerce_number


@pytest.mark.parametrize(
    ("text", "max_n", "expected"),
    [
        # Bare digit forms
        ("1", 5, 1),
        ("5", 5, 5),
        ("3.", 5, 3),
        ("3!", 5, 3),
        ("  4  ", 5, 4),
        # Spelled words (1-9 supported even when max_n smaller)
        ("one", 5, 1),
        ("two", 5, 2),
        ("three", 5, 3),
        ("four", 5, 4),
        ("five", 5, 5),
        ("Three.", 5, 3),
        ("THREE", 5, 3),
        # Prefixed forms
        ("number 3", 5, 3),
        ("number three", 5, 3),
        ("option 3", 5, 3),
        ("the third", 5, 3),
        ("third", 5, 3),
        ("third one", 5, 3),
        ("first", 5, 1),
        ("fifth", 5, 5),
        ("second option", 5, 2),
    ],
)
def test_coerce_accepts(text: str, max_n: int, expected: int) -> None:
    assert coerce_number(text, max_n) == expected


@pytest.mark.parametrize(
    ("text", "max_n"),
    [
        ("", 5),
        ("   ", 5),
        ("hello", 5),
        ("twenty", 5),
        ("thirty", 5),
        ("0", 5),
        ("6", 5),  # > max_n
        ("seven", 5),  # > max_n
        ("seventh", 5),  # > max_n
        ("number 7", 5),  # > max_n
        ("number 0", 5),
        ("-1", 5),
        ("1.5", 5),
    ],
)
def test_coerce_rejects(text: str, max_n: int) -> None:
    assert coerce_number(text, max_n) is None


def test_coerce_zero_max_always_none() -> None:
    assert coerce_number("1", 0) is None
    assert coerce_number("one", 0) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/unit/test_picker_coerce.py -v
```

Expected: import error.

- [ ] **Step 3: Implement `coerce_number`**

Write `src/voice_commander/picker/coerce.py`:

```python
"""Coerce a spoken transcript to a picker index (1-based).

Recognises bare digits, spelled cardinals (one..nine), spelled ordinals
(first..ninth), and the common prefixed forms ("number three", "option 2",
"the third", "third one"). Returns ``None`` for anything outside that
vocabulary or outside ``[1, max_n]``.
"""

from __future__ import annotations

import re

_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
}

_STRIP_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_PREFIX_TOKENS = frozenset({"number", "option", "the"})
_SUFFIX_TOKENS = frozenset({"one", "option"})


def _normalise(text: str) -> str:
    return " ".join(_STRIP_PUNCT_RE.sub(" ", text.lower()).split())


def coerce_number(text: str, max_n: int) -> int | None:
    """Return the 1-based index encoded by *text*, or ``None``.

    Parameters
    ----------
    text:
        Raw transcript text. Punctuation and case are normalised.
    max_n:
        Inclusive upper bound. Values outside ``[1, max_n]`` return ``None``.
        ``max_n <= 0`` always returns ``None``.
    """
    if max_n <= 0:
        return None
    normalised = _normalise(text)
    if not normalised:
        return None

    tokens = normalised.split()
    while tokens and tokens[0] in _PREFIX_TOKENS:
        tokens.pop(0)
    while tokens and tokens[-1] in _SUFFIX_TOKENS and len(tokens) > 1:
        tokens.pop()

    if len(tokens) != 1:
        return None
    token = tokens[0]

    if token.isdigit():
        try:
            n = int(token)
        except ValueError:
            return None
    elif token in _WORDS:
        n = _WORDS[token]
    else:
        return None

    if 1 <= n <= max_n:
        return n
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_picker_coerce.py -v
```

Expected: all parametrised cases PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/picker/coerce.py tests/unit/test_picker_coerce.py
git commit -m "feat(picker): add coerce_number (text → 1-based index)"
```

---

## Task 4: `PickerConfig` + `PickerFocusConfig` in `config.py`

**Files:**
- Modify: `src/voice_commander/config.py`
- Modify: `tests/unit/test_config.py` (add picker tests)
- Modify: `config.toml` (project root — add commented example section)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py` (preserve existing tests). Add this block at the end of the file:

```python
# ---------------------------------------------------------------------------
# Picker config (ADR 0083)
# ---------------------------------------------------------------------------


def test_picker_defaults(tmp_path):
    """[picker] missing entirely → safe defaults."""
    from voice_commander.config import Config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("")
    cfg = Config.load(cfg_path)
    assert cfg.picker.enabled is True
    assert cfg.picker.timeout_sec == 5
    assert cfg.picker.cancel_words == ("cancel", "nevermind", "stop")
    assert cfg.picker.focus.cap == 5
    assert cfg.picker.focus.exclude_foreground is True
    assert cfg.picker.focus.exclude_self is True


def test_picker_overrides(tmp_path):
    from voice_commander.config import Config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[picker]
enabled = false
timeout_sec = 8
cancel_words = ["cancel", "abort"]

[picker.focus]
cap = 7
exclude_foreground = false
exclude_self = false
"""
    )
    cfg = Config.load(cfg_path)
    assert cfg.picker.enabled is False
    assert cfg.picker.timeout_sec == 8
    assert cfg.picker.cancel_words == ("cancel", "abort")
    assert cfg.picker.focus.cap == 7
    assert cfg.picker.focus.exclude_foreground is False
    assert cfg.picker.focus.exclude_self is False
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
pytest tests/unit/test_config.py::test_picker_defaults tests/unit/test_config.py::test_picker_overrides -v
```

Expected: AttributeError on `cfg.picker`.

- [ ] **Step 3: Add the config dataclasses**

Edit `src/voice_commander/config.py`. Insert the two new dataclasses before the existing `Config` class (after `ObservabilityConfig`):

```python
@dataclass(frozen=True)
class PickerFocusConfig:
    cap: int = 5
    exclude_foreground: bool = True
    exclude_self: bool = True


@dataclass(frozen=True)
class PickerConfig:
    enabled: bool = True
    timeout_sec: int = 5
    cancel_words: tuple[str, ...] = ("cancel", "nevermind", "stop")
    focus: PickerFocusConfig = field(default_factory=PickerFocusConfig)
```

Add a `picker` field to `Config`:

```python
@dataclass(frozen=True)
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    web: WebConfig = field(default_factory=WebConfig)
    sprite: SpriteConfig = field(default_factory=SpriteConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    picker: PickerConfig = field(default_factory=PickerConfig)
```

Extend `Config.load` so it parses `[picker]` and `[picker.focus]`. Add after the `vad_gates` parsing (mirroring the pattern):

```python
        picker_raw = dict(raw.get("picker", {}))
        focus_raw = picker_raw.pop("focus", {})
        cancel_raw = picker_raw.pop("cancel_words", None)
        if cancel_raw is not None:
            if not isinstance(cancel_raw, list) or not all(isinstance(w, str) for w in cancel_raw):
                raise TypeError("Config picker.cancel_words must be a list of strings")
            picker_raw["cancel_words"] = tuple(cancel_raw)
```

Then in the `return cls(...)` block append:

```python
            picker=_section(
                PickerConfig,
                {**picker_raw, "focus": _section(PickerFocusConfig, focus_raw)},
            ),
```

The existing `_section` helper rejects unknown keys, so unknown picker keys raise. `cancel_words` is converted to a tuple before reaching `_section` because the helper's strict type-check would reject `list` vs `tuple`.

`_type_ok` currently has no branch for tuple — add one. Replace the function with:

```python
def _type_ok(value: Any, expected: Any) -> bool:
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is bool:
        return isinstance(value, bool)
    if expected is str:
        return isinstance(value, str)
    if getattr(expected, "__origin__", None) is tuple:
        return isinstance(value, tuple)
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_config.py -v
```

Expected: all pre-existing tests still PASS, and the two new picker tests PASS.

- [ ] **Step 5: Add a commented example section to `config.toml`**

Append to `config.toml` at the repo root (do not overwrite existing sections):

```toml

# --- Bare-primitive picker (ADR 0083) ---
# Saying a primitive verb alone (e.g. "focus") opens a numbered modal of
# candidate targets. The next utterance picks one by number.
[picker]
enabled = true
timeout_sec = 5
cancel_words = ["cancel", "nevermind", "stop"]

[picker.focus]
cap = 5                  # max items shown (1-9)
exclude_foreground = true
exclude_self = true      # filter daemon + sprite + modal windows
```

- [ ] **Step 6: Commit**

```powershell
git add src/voice_commander/config.py tests/unit/test_config.py config.toml
git commit -m "feat(picker): add [picker] + [picker.focus] config sections"
```

---

## Task 5: `MruTracker` — pure ring buffer + filter logic

**Files:**
- Create: `src/voice_commander/picker/mru.py`
- Create: `tests/unit/test_picker_mru.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_picker_mru.py`:

```python
from __future__ import annotations

import pytest

from voice_commander.picker.mru import MruTracker, MruEntry


def _entry(hwnd: int, *, pid: int = 100, proc: str = "chrome.exe", title: str | None = None) -> MruEntry:
    return MruEntry(hwnd=hwnd, pid=pid, proc_name=proc, title=title or f"win{hwnd}")


def test_record_pushes_to_front_and_dedupes():
    tracker = MruTracker(capacity=8)
    tracker.record(_entry(1))
    tracker.record(_entry(2))
    tracker.record(_entry(3))
    tracker.record(_entry(1))  # re-focus moves to front, no duplicate
    hwnds = [e.hwnd for e in tracker.snapshot()]
    assert hwnds == [1, 3, 2]


def test_capacity_bounds_buffer():
    tracker = MruTracker(capacity=3)
    for i in range(1, 6):
        tracker.record(_entry(i))
    hwnds = [e.hwnd for e in tracker.snapshot()]
    assert hwnds == [5, 4, 3]


def test_top_filters_with_predicate():
    tracker = MruTracker(capacity=8)
    for i in range(1, 6):
        tracker.record(_entry(i))
    # Keep only even hwnds.
    top = tracker.top(2, predicate=lambda e: e.hwnd % 2 == 0)
    assert [e.hwnd for e in top] == [4, 2]


def test_top_respects_n_cap():
    tracker = MruTracker(capacity=8)
    for i in range(1, 6):
        tracker.record(_entry(i))
    top = tracker.top(2)
    assert [e.hwnd for e in top] == [5, 4]


def test_top_returns_empty_when_buffer_empty():
    tracker = MruTracker(capacity=8)
    assert tracker.top(5) == []


def test_register_self_hwnd_excludes_it_from_top():
    tracker = MruTracker(capacity=8)
    tracker.register_self_hwnd(42)
    tracker.record(_entry(1))
    tracker.record(_entry(42))
    tracker.record(_entry(2))
    top = tracker.top(5, predicate=lambda e: e.hwnd not in tracker.self_hwnds)
    assert [e.hwnd for e in top] == [2, 1]
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/unit/test_picker_mru.py -v
```

Expected: import error.

- [ ] **Step 3: Implement the pure ring buffer + filter logic**

Write `src/voice_commander/picker/mru.py`:

```python
"""MRU tracker for focused windows.

Two layers:

* :class:`MruTracker` — a pure ring buffer + filter helper. No Win32
  dependency, fully unit-testable.
* :class:`Win32MruPump` — installs a :func:`SetWinEventHook` on a dedicated
  message-pump thread and pushes :class:`MruEntry` records into the tracker.

The pump lives at the bottom of this module so unit tests can import
:class:`MruTracker` without triggering the pywin32 import.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MruEntry:
    hwnd: int
    pid: int
    proc_name: str
    title: str


class MruTracker:
    """Ring buffer of recently foregrounded windows, newest first.

    Re-recording an hwnd already in the buffer moves it to the front; this
    matches the "switching back and forth" intuition. Thread-safe: ``record``
    and ``snapshot`` / ``top`` may be called from different threads.
    """

    def __init__(self, capacity: int = 32) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._capacity = capacity
        self._buf: deque[MruEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._self_hwnds: set[int] = set()

    def register_self_hwnd(self, hwnd: int) -> None:
        """Mark *hwnd* as belonging to this daemon / sprite / modal.

        The tracker stores the set but does not filter automatically — callers
        pass ``predicate=lambda e: e.hwnd not in tracker.self_hwnds`` to
        :meth:`top` when self-exclusion is desired.
        """
        with self._lock:
            self._self_hwnds.add(hwnd)

    def unregister_self_hwnd(self, hwnd: int) -> None:
        with self._lock:
            self._self_hwnds.discard(hwnd)

    @property
    def self_hwnds(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._self_hwnds)

    def record(self, entry: MruEntry) -> None:
        """Push *entry* to the front, removing any prior occurrence of the same hwnd."""
        with self._lock:
            for i, existing in enumerate(self._buf):
                if existing.hwnd == entry.hwnd:
                    del self._buf[i]
                    break
            self._buf.appendleft(entry)

    def snapshot(self) -> list[MruEntry]:
        with self._lock:
            return list(self._buf)

    def top(
        self,
        n: int,
        predicate: Callable[[MruEntry], bool] | None = None,
    ) -> list[MruEntry]:
        if n <= 0:
            return []
        with self._lock:
            entries: Iterable[MruEntry] = list(self._buf)
        out: list[MruEntry] = []
        for entry in entries:
            if predicate is not None and not predicate(entry):
                continue
            out.append(entry)
            if len(out) >= n:
                break
        return out
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_picker_mru.py -v
```

Expected: all six tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/picker/mru.py tests/unit/test_picker_mru.py
git commit -m "feat(picker): add MruTracker ring buffer + filter helpers"
```

---

## Task 6: `Win32MruPump` — `SetWinEventHook` message pump thread

**Files:**
- Modify: `src/voice_commander/picker/mru.py` (append `Win32MruPump`)
- Modify: `tests/unit/test_picker_mru.py` (add pump-injection test)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_picker_mru.py`:

```python
def test_pump_records_entries_via_injected_callback():
    """Drive the foreground hook via a fake callback to verify push wiring."""
    from voice_commander.picker.mru import Win32MruPump

    tracker = MruTracker(capacity=8)

    captured_cb: list = []

    def fake_install_hook(cb):
        captured_cb.append(cb)

        def _uninstall() -> None:
            captured_cb.clear()

        return _uninstall

    def fake_resolve(hwnd: int) -> MruEntry:
        return MruEntry(hwnd=hwnd, pid=hwnd * 10, proc_name="chrome.exe", title=f"t{hwnd}")

    pump = Win32MruPump(
        tracker=tracker,
        install_hook=fake_install_hook,
        resolve_hwnd=fake_resolve,
    )
    pump.start()
    try:
        # Simulate two foreground changes.
        assert captured_cb, "install_hook should have been called once on start"
        cb = captured_cb[0]
        cb(101)
        cb(202)
    finally:
        pump.stop()

    hwnds = [e.hwnd for e in tracker.snapshot()]
    assert hwnds == [202, 101]
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
pytest tests/unit/test_picker_mru.py::test_pump_records_entries_via_injected_callback -v
```

Expected: ImportError on `Win32MruPump`.

- [ ] **Step 3: Implement `Win32MruPump`**

Append to `src/voice_commander/picker/mru.py`:

```python
# ---------------------------------------------------------------------------
# Win32 message-pump pump (Windows-only)
# ---------------------------------------------------------------------------


_HookInstaller = Callable[[Callable[[int], None]], Callable[[], None]]
_HwndResolver = Callable[[int], MruEntry | None]


class Win32MruPump:
    """Push foreground-change hwnds into an :class:`MruTracker`.

    On start, the pump spins up a dedicated daemon thread that installs a
    ``WinEventHook(EVENT_SYSTEM_FOREGROUND)`` and runs a Win32 message loop.
    Every foreground change resolves to an :class:`MruEntry` via *resolve_hwnd*
    and is recorded on the injected :class:`MruTracker`.

    The *install_hook* / *resolve_hwnd* dependencies are injected so unit
    tests can drive the pump without touching Win32 at all.
    """

    def __init__(
        self,
        tracker: MruTracker,
        install_hook: _HookInstaller | None = None,
        resolve_hwnd: _HwndResolver | None = None,
    ) -> None:
        self._tracker = tracker
        self._install_hook = install_hook or _default_install_hook
        self._resolve_hwnd = resolve_hwnd or _default_resolve_hwnd
        self._thread: threading.Thread | None = None
        self._uninstall: Callable[[], None] | None = None
        self._stopped = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopped.clear()
        # Drive installation on the caller thread when we have a fake;
        # the real Win32 path installs from inside the worker thread.
        if self._install_hook is _default_install_hook:
            self._thread = threading.Thread(
                target=self._run_win32, name="vc-mru-pump", daemon=True
            )
            self._thread.start()
        else:

            def _push(hwnd: int) -> None:
                entry = self._resolve_hwnd(hwnd)
                if entry is not None:
                    self._tracker.record(entry)

            self._uninstall = self._install_hook(_push)

    def stop(self) -> None:
        self._stopped.set()
        if self._uninstall is not None:
            try:
                self._uninstall()
            except Exception:
                logger.exception("Win32MruPump uninstall failed")
            self._uninstall = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run_win32(self) -> None:
        def _push(hwnd: int) -> None:
            entry = self._resolve_hwnd(hwnd)
            if entry is not None:
                self._tracker.record(entry)

        try:
            self._uninstall = self._install_hook(_push)
        except Exception:
            logger.exception("Win32MruPump: install_hook failed; pump inactive")
            return

        # Pump messages until stop is requested. The hook callback runs on this
        # thread, so the deque writes happen here.
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            msg = wintypes.MSG()
            while not self._stopped.is_set():
                # PeekMessage with PM_REMOVE so we can poll the stop event.
                if user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 0x0001):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    self._stopped.wait(0.05)
        except Exception:
            logger.exception("Win32MruPump message loop crashed")


# Default Win32 install_hook + resolve_hwnd ------------------------------------


def _default_install_hook(callback: Callable[[int], None]) -> Callable[[], None]:
    """Install ``EVENT_SYSTEM_FOREGROUND`` hook and return an uninstaller.

    Imported lazily so unit tests that inject fakes don't drag pywin32 in.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EVENT_SYSTEM_FOREGROUND = 0x0003
    WINEVENT_OUTOFCONTEXT = 0x0000
    WINEVENT_SKIPOWNPROCESS = 0x0002

    WinEventProcType = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        wintypes.LONG,
        wintypes.LONG,
        wintypes.DWORD,
        wintypes.DWORD,
    )

    def _proc(_hook, _event, hwnd, _id_obj, _id_child, _tid, _time) -> None:
        try:
            callback(int(hwnd))
        except Exception:
            logger.exception("MRU hook callback failed for hwnd=%s", hwnd)

    proc_ref = WinEventProcType(_proc)
    handle = user32.SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND,
        EVENT_SYSTEM_FOREGROUND,
        0,
        proc_ref,
        0,
        0,
        WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
    )
    if not handle:
        raise OSError("SetWinEventHook returned NULL")

    def _uninstall() -> None:
        user32.UnhookWinEvent(handle)

    # Keep a reference to proc_ref so Python doesn't GC the trampoline.
    _uninstall._proc_ref = proc_ref  # type: ignore[attr-defined]
    return _uninstall


def _default_resolve_hwnd(hwnd: int) -> MruEntry | None:
    """Resolve *hwnd* to an :class:`MruEntry`. Returns ``None`` on failure."""
    try:
        import win32gui
        import win32process
    except ImportError:
        return None

    try:
        title = win32gui.GetWindowText(hwnd) or ""
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return None

    proc_name = ""
    try:
        import psutil

        proc_name = psutil.Process(pid).name() if pid else ""
    except Exception:
        proc_name = ""

    if not title and not proc_name:
        return None
    return MruEntry(hwnd=int(hwnd), pid=int(pid or 0), proc_name=proc_name, title=title)
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_picker_mru.py -v
```

Expected: all seven tests PASS (six existing + one new pump test).

- [ ] **Step 5: Verify package import works end-to-end**

```powershell
python -c "from voice_commander import picker; print(picker.__all__)"
```

Expected output: a list including `PickerItem`, `PickerProvider`, `PickerSession`, `BarePickerRegistry`, `coerce_number`, `bare_picker`, `get_global_picker_registry`. (Note: the `__init__.py` imports `PickerSession`, which doesn't exist yet — adjust by deferring that import to Task 7 if needed.)

If the import fails because `PickerSession` is not yet defined, drop the `PickerSession` line from `__init__.py` temporarily and put it back in Task 7's commit.

- [ ] **Step 6: Commit**

```powershell
git add src/voice_commander/picker/mru.py tests/unit/test_picker_mru.py
git commit -m "feat(picker): add Win32MruPump (SetWinEventHook message pump)"
```

---

## Task 7: `PickerSession` — state machine skeleton (`open`, `close`, `cancel`, `active`)

**Files:**
- Create: `src/voice_commander/picker/session.py`
- Create: `tests/unit/test_picker_session.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_picker_session.py`:

```python
from __future__ import annotations

from voice_commander.picker.session import PickerSession
from voice_commander.picker.types import PickerItem
from voice_commander.plan import Plan, ToolCall


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, data: dict | None = None) -> None:
        self.events.append((event_type, data or {}))


def _item(label: str, hwnd: int) -> PickerItem:
    return PickerItem(
        label=label,
        action=Plan(
            steps=(ToolCall(name="focus", kwargs={"_hwnd": hwnd}),),
            raw_response={"router": "picker", "verb": "focus", "selection": label},
        ),
    )


def test_inactive_by_default():
    session = PickerSession(bus=_FakeBus())
    assert session.active is False
    assert session.items == ()


def test_open_sets_active_and_emits_event():
    bus = _FakeBus()
    session = PickerSession(bus=bus)
    items = [_item("Chrome", 11), _item("VS Code", 22)]
    session.open("focus", items)
    assert session.active is True
    assert session.items == tuple(items)
    assert bus.events[0][0] == "picker.open"
    payload = bus.events[0][1]
    assert payload["verb"] == "focus"
    assert payload["items"] == [
        {"n": 1, "label": "Chrome"},
        {"n": 2, "label": "VS Code"},
    ]


def test_close_clears_state_and_emits_event():
    bus = _FakeBus()
    session = PickerSession(bus=bus)
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()
    session.close()
    assert session.active is False
    assert session.items == ()
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "select"})]


def test_cancel_emits_close_with_reason_cancel():
    bus = _FakeBus()
    session = PickerSession(bus=bus)
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()
    session.cancel(reason="word")
    assert session.active is False
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "word"})]


def test_re_open_replaces_items_and_re_publishes():
    bus = _FakeBus()
    session = PickerSession(bus=bus)
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()
    session.open("focus", [_item("VS Code", 22)])
    assert session.items == (_item("VS Code", 22),)
    assert [e[0] for e in bus.events] == ["picker.open"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/unit/test_picker_session.py -v
```

Expected: import error.

- [ ] **Step 3: Implement the session skeleton**

Write `src/voice_commander/picker/session.py`:

```python
"""PickerSession — daemon-side state machine for an open bare-primitive picker.

A session is the sub-state of an active voice session during which the next
utterance picks an item from the modal. Holds no Win32 / pyglet code: the
sprite renders via the SSE ``picker.open`` / ``picker.close`` events that this
class publishes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from typing import Protocol

from voice_commander.picker.types import PickerItem

logger = logging.getLogger(__name__)


class _BusLike(Protocol):
    def publish(self, event_type: str, data: dict | None = None) -> None: ...


class PickerSession:
    """Daemon-owned picker state.

    Thread-safety: every public method acquires the internal lock, so callers
    on the pipeline thread, the heartbeat thread, and the hotkey thread can
    interact without races.
    """

    def __init__(self, bus: _BusLike, now: callable = time.monotonic) -> None:  # type: ignore[valid-type]
        self._bus = bus
        self._now = now
        self._lock = threading.Lock()
        self._active = False
        self._verb = ""
        self._items: tuple[PickerItem, ...] = ()
        self._opened_at: float = 0.0

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def items(self) -> tuple[PickerItem, ...]:
        with self._lock:
            return self._items

    @property
    def verb(self) -> str:
        with self._lock:
            return self._verb

    def open(self, verb: str, items: Iterable[PickerItem]) -> None:
        """Open (or replace) the picker for *verb* with *items*.

        Emits ``picker.open`` with a payload of ``{verb, items: [{n, label}]}``.
        Empty *items* is legal but rare — callers should miss-chime instead of
        opening on empty.
        """
        items_tuple = tuple(items)
        with self._lock:
            self._active = True
            self._verb = verb
            self._items = items_tuple
            self._opened_at = self._now()
        payload = {
            "verb": verb,
            "items": [{"n": i + 1, "label": it.label} for i, it in enumerate(items_tuple)],
        }
        self._bus.publish("picker.open", payload)

    def close(self, reason: str = "select") -> None:
        """Close the picker with *reason* (``select`` | ``word`` | ``timeout`` | ``abort`` | ``out_of_range``)."""
        with self._lock:
            if not self._active:
                return
            verb = self._verb
            self._active = False
            self._verb = ""
            self._items = ()
            self._opened_at = 0.0
        self._bus.publish("picker.close", {"verb": verb, "reason": reason})

    def cancel(self, reason: str = "word") -> None:
        """Alias for :meth:`close` that emphasises non-selection paths."""
        self.close(reason=reason)
```

- [ ] **Step 4: Re-enable the `PickerSession` export**

If you removed `PickerSession` from `picker/__init__.py` in Task 6, restore it now. The full `__all__` should include `PickerSession`.

- [ ] **Step 5: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_picker_session.py -v
```

Expected: all five tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/voice_commander/picker/session.py tests/unit/test_picker_session.py src/voice_commander/picker/__init__.py
git commit -m "feat(picker): add PickerSession open/close/cancel state machine"
```

---

## Task 8: `PickerSession.handle_transcript` — cancel words, number coercion, dispatch payload

**Files:**
- Modify: `src/voice_commander/picker/session.py`
- Modify: `tests/unit/test_picker_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_picker_session.py`:

```python
def test_handle_transcript_returns_action_and_closes_on_valid_number():
    bus = _FakeBus()
    items = [_item("Chrome", 11), _item("VS Code", 22), _item("Slack", 33)]
    session = PickerSession(
        bus=bus,
        cancel_words=("cancel", "nevermind", "stop"),
    )
    session.open("focus", items)
    bus.events.clear()

    outcome = session.handle_transcript("three.")
    assert outcome is not None
    assert outcome.kind == "select"
    assert outcome.plan is items[2].action
    assert outcome.n == 3
    assert session.active is False
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "select"})]


def test_handle_transcript_cancel_word_closes_with_word_reason():
    bus = _FakeBus()
    session = PickerSession(bus=bus, cancel_words=("cancel", "nevermind"))
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()

    outcome = session.handle_transcript("Cancel.")
    assert outcome is not None
    assert outcome.kind == "cancel"
    assert outcome.plan is None
    assert session.active is False
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "word"})]


def test_handle_transcript_out_of_range_keeps_open():
    bus = _FakeBus()
    items = [_item("Chrome", 11), _item("VS Code", 22)]
    session = PickerSession(bus=bus, cancel_words=())
    session.open("focus", items)
    bus.events.clear()

    outcome = session.handle_transcript("seven")
    assert outcome is not None
    assert outcome.kind == "miss"
    assert outcome.plan is None
    assert session.active is True
    assert bus.events == []  # no close, picker stays open


def test_handle_transcript_non_number_keeps_open():
    bus = _FakeBus()
    session = PickerSession(bus=bus, cancel_words=())
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()

    outcome = session.handle_transcript("hello world")
    assert outcome is not None
    assert outcome.kind == "miss"
    assert session.active is True


def test_handle_transcript_inactive_returns_none():
    session = PickerSession(bus=_FakeBus(), cancel_words=())
    assert session.handle_transcript("three") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/unit/test_picker_session.py -v
```

Expected: failures on the new tests — `handle_transcript` / `cancel_words` / `outcome.kind` not implemented.

- [ ] **Step 3: Implement `handle_transcript`**

Edit `src/voice_commander/picker/session.py`. Add a `PickerOutcome` dataclass at the top of the module (under the existing imports) and update `PickerSession.__init__` plus add `handle_transcript`:

```python
from dataclasses import dataclass
from typing import Literal

from voice_commander.picker.coerce import coerce_number
from voice_commander.plan import Plan


OutcomeKind = Literal["select", "cancel", "miss"]


@dataclass(frozen=True)
class PickerOutcome:
    kind: OutcomeKind
    plan: Plan | None
    n: int | None
```

Update `__init__` signature:

```python
    def __init__(
        self,
        bus: _BusLike,
        now: callable = time.monotonic,  # type: ignore[valid-type]
        cancel_words: tuple[str, ...] = ("cancel", "nevermind", "stop"),
    ) -> None:
        self._bus = bus
        self._now = now
        self._cancel_set = frozenset(w.lower().strip() for w in cancel_words if w)
        self._lock = threading.Lock()
        self._active = False
        self._verb = ""
        self._items: tuple[PickerItem, ...] = ()
        self._opened_at: float = 0.0
```

Add `handle_transcript`:

```python
    def handle_transcript(self, text: str) -> PickerOutcome | None:
        """Resolve a transcript against the open picker.

        Returns ``None`` when the session is inactive. Otherwise returns a
        :class:`PickerOutcome` describing what happened:

        * ``kind="select"`` — number recognised; picker closed; ``plan`` set.
        * ``kind="cancel"`` — cancel word recognised; picker closed; ``plan`` is None.
        * ``kind="miss"`` — non-number or out-of-range; picker stays open;
          ``plan`` is None.
        """
        with self._lock:
            if not self._active:
                return None
            verb = self._verb
            items = self._items

        normalised = " ".join(text.lower().split()).strip(".,!?")
        if normalised in self._cancel_set:
            self.close(reason="word")
            return PickerOutcome(kind="cancel", plan=None, n=None)

        n = coerce_number(text, len(items))
        if n is None:
            logger.info("picker miss verb=%s text=%r", verb, text)
            return PickerOutcome(kind="miss", plan=None, n=None)

        chosen = items[n - 1]
        self.close(reason="select")
        return PickerOutcome(kind="select", plan=chosen.action, n=n)
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_picker_session.py -v
```

Expected: all ten tests PASS (five existing + five new).

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/picker/session.py tests/unit/test_picker_session.py
git commit -m "feat(picker): handle_transcript — cancel/select/miss outcomes"
```

---

## Task 9: `PickerSession.tick` — timeout auto-close

**Files:**
- Modify: `src/voice_commander/picker/session.py`
- Modify: `tests/unit/test_picker_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_picker_session.py`:

```python
def test_tick_closes_on_timeout():
    bus = _FakeBus()
    clock = {"t": 1000.0}
    session = PickerSession(
        bus=bus,
        now=lambda: clock["t"],
        cancel_words=(),
        timeout_sec=5.0,
    )
    session.open("focus", [_item("Chrome", 11)])
    bus.events.clear()

    clock["t"] = 1003.0
    session.tick()
    assert session.active is True
    assert bus.events == []

    clock["t"] = 1005.5
    session.tick()
    assert session.active is False
    assert bus.events == [("picker.close", {"verb": "focus", "reason": "timeout"})]


def test_tick_noop_when_inactive():
    bus = _FakeBus()
    clock = {"t": 0.0}
    session = PickerSession(
        bus=bus, now=lambda: clock["t"], cancel_words=(), timeout_sec=1.0
    )
    clock["t"] = 100.0
    session.tick()
    assert bus.events == []


def test_tick_reset_on_re_open():
    """Re-opening (e.g. provider re-runs) resets the timeout window."""
    bus = _FakeBus()
    clock = {"t": 0.0}
    session = PickerSession(
        bus=bus, now=lambda: clock["t"], cancel_words=(), timeout_sec=5.0
    )
    session.open("focus", [_item("Chrome", 11)])
    clock["t"] = 4.5
    session.open("focus", [_item("VS Code", 22)])  # reset timer
    clock["t"] = 9.0
    session.tick()
    assert session.active is True  # 4.5 elapsed since re-open, under 5.0
    clock["t"] = 9.6
    session.tick()
    assert session.active is False  # now 5.1 elapsed since re-open
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/unit/test_picker_session.py::test_tick_closes_on_timeout -v
```

Expected: `TypeError` — `timeout_sec` not in `__init__`.

- [ ] **Step 3: Add `timeout_sec` + `tick`**

Update `PickerSession.__init__` signature:

```python
    def __init__(
        self,
        bus: _BusLike,
        now: callable = time.monotonic,  # type: ignore[valid-type]
        cancel_words: tuple[str, ...] = ("cancel", "nevermind", "stop"),
        timeout_sec: float = 5.0,
    ) -> None:
        self._bus = bus
        self._now = now
        self._cancel_set = frozenset(w.lower().strip() for w in cancel_words if w)
        self._timeout_sec = float(timeout_sec)
        self._lock = threading.Lock()
        self._active = False
        self._verb = ""
        self._items: tuple[PickerItem, ...] = ()
        self._opened_at: float = 0.0
```

Add `tick`:

```python
    def tick(self) -> None:
        """Heartbeat-driven check that fires :meth:`close(reason="timeout")` if elapsed."""
        with self._lock:
            if not self._active:
                return
            elapsed = self._now() - self._opened_at
            should_close = elapsed >= self._timeout_sec
        if should_close:
            self.close(reason="timeout")
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_picker_session.py -v
```

Expected: all thirteen tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/picker/session.py tests/unit/test_picker_session.py
git commit -m "feat(picker): tick() auto-closes session after timeout"
```

---

## Task 10: `focus_picker` provider — wires `MruTracker` into `PickerItem`s

**Files:**
- Create: `src/voice_commander/tools/focus_picker.py`
- Create: `tests/unit/test_picker_focus_provider.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_picker_focus_provider.py`:

```python
from __future__ import annotations

import pytest

from voice_commander.picker.mru import MruEntry, MruTracker
from voice_commander.picker.registry import reset_global_picker_registry
from voice_commander.tools.focus_picker import (
    FocusPickerSettings,
    build_focus_picker,
    format_label,
)


@pytest.fixture(autouse=True)
def _reset_pickers():
    reset_global_picker_registry()
    yield
    reset_global_picker_registry()


def _entry(hwnd: int, *, proc: str = "chrome.exe", title: str = "Untitled") -> MruEntry:
    return MruEntry(hwnd=hwnd, pid=hwnd * 10, proc_name=proc, title=title)


def test_format_label_combines_proc_and_title():
    e = _entry(1, proc="chrome.exe", title="voice-commander - Google Chrome")
    assert format_label(e) == "Chrome — voice-commander - Google Chrome"


def test_format_label_strips_exe_suffix():
    e = _entry(1, proc="Code.exe", title="daemon.py - voice-commander")
    assert format_label(e) == "Code — daemon.py - voice-commander"


def test_format_label_falls_back_to_title_only_when_proc_empty():
    e = _entry(1, proc="", title="My Window")
    assert format_label(e) == "My Window"


def test_format_label_truncates_long_titles():
    e = _entry(
        1,
        proc="chrome.exe",
        title="a really long window title that exceeds the cap to keep modal width sane",
    )
    label = format_label(e, max_len=40)
    assert len(label) <= 40
    assert label.endswith("…")


def test_provider_returns_top_cap_entries():
    tracker = MruTracker(capacity=16)
    for h in range(1, 8):
        tracker.record(_entry(h, proc=f"app{h}.exe", title=f"win{h}"))

    settings = FocusPickerSettings(cap=5, exclude_foreground=False, exclude_self=False)
    provider = build_focus_picker(
        tracker=tracker,
        settings=settings,
        foreground_hwnd=lambda: 0,
    )
    items = provider()
    assert len(items) == 5
    # Newest first.
    assert [it.action.steps[0].kwargs["_hwnd"] for it in items] == [7, 6, 5, 4, 3]
    assert items[0].action.steps[0].name == "focus"
    assert items[0].action.raw_response["router"] == "picker"


def test_provider_excludes_current_foreground():
    tracker = MruTracker(capacity=16)
    for h in range(1, 6):
        tracker.record(_entry(h, proc=f"app{h}.exe"))
    settings = FocusPickerSettings(cap=5, exclude_foreground=True, exclude_self=False)
    provider = build_focus_picker(
        tracker=tracker, settings=settings, foreground_hwnd=lambda: 4
    )
    hwnds = [it.action.steps[0].kwargs["_hwnd"] for it in provider()]
    assert 4 not in hwnds


def test_provider_excludes_self_hwnds_when_enabled():
    tracker = MruTracker(capacity=16)
    tracker.register_self_hwnd(2)
    for h in range(1, 6):
        tracker.record(_entry(h, proc=f"app{h}.exe"))
    settings = FocusPickerSettings(cap=5, exclude_foreground=False, exclude_self=True)
    provider = build_focus_picker(
        tracker=tracker, settings=settings, foreground_hwnd=lambda: 0
    )
    hwnds = [it.action.steps[0].kwargs["_hwnd"] for it in provider()]
    assert 2 not in hwnds


def test_provider_returns_empty_when_no_candidates():
    tracker = MruTracker(capacity=8)
    settings = FocusPickerSettings(cap=5, exclude_foreground=False, exclude_self=False)
    provider = build_focus_picker(
        tracker=tracker, settings=settings, foreground_hwnd=lambda: 0
    )
    assert provider() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/unit/test_picker_focus_provider.py -v
```

Expected: import error.

- [ ] **Step 3: Implement the provider builder**

Write `src/voice_commander/tools/focus_picker.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_picker_focus_provider.py -v
```

Expected: all eight tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/tools/focus_picker.py tests/unit/test_picker_focus_provider.py
git commit -m "feat(picker(focus)): focus_picker provider built on MruTracker"
```

---

## Task 11: `focus()` accepts an `_hwnd` shortcut path

**Files:**
- Modify: `src/voice_commander/tools/primitives.py`
- Modify: `src/voice_commander/tools/primitives.toml`
- Modify: `tests/unit/test_primitives.py` (or the existing focus-tests module — confirm location)

- [ ] **Step 1: Locate the existing focus tests**

```powershell
Get-ChildItem tests -Recurse -Filter "test_primitives*.py"
```

Use whichever file already contains `focus`-related tests. Inside that file, add the test below. If no such file exists, create `tests/unit/test_primitives_focus_hwnd.py`.

- [ ] **Step 2: Write the failing test**

Add:

```python
from __future__ import annotations

import pytest

from voice_commander.tools import primitives


class _FakeWin32:
    """Stand-in for the win32gui / win32con / win32process module trio."""

    def __init__(self) -> None:
        self.focused: int | None = None

    # win32gui surface used by focus()
    def IsIconic(self, hwnd: int) -> bool:  # noqa: N802 — match win32 naming
        return False

    def ShowWindow(self, hwnd: int, _flag: int) -> None:  # noqa: N802
        pass

    def GetForegroundWindow(self) -> int:  # noqa: N802
        return 0


def test_focus_with_hwnd_skips_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``_hwnd`` is supplied, ``focus`` must NOT call ``resolve_window``."""
    called = {"resolve": 0, "do_focus": 0, "verify": 0}

    def _no_resolve(_target: str) -> int:
        called["resolve"] += 1
        raise AssertionError("resolve_window should not be called when _hwnd is set")

    def _do_focus(hwnd: int, _fg_tid: int, _t_tid: int) -> None:
        called["do_focus"] += 1
        assert hwnd == 99

    def _verify_foreground(hwnd: int) -> bool:
        called["verify"] += 1
        return hwnd == 99

    monkeypatch.setattr("voice_commander.resolver.resolve_window", _no_resolve)
    monkeypatch.setattr("voice_commander.tools.primitives._do_focus", _do_focus)
    monkeypatch.setattr("voice_commander.tools.primitives._verify_foreground", _verify_foreground)

    out = primitives.focus(target="", _hwnd=99)
    assert out == 99
    assert called == {"resolve": 0, "do_focus": 1, "verify": 1}


def test_focus_target_still_resolves_when_hwnd_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing behaviour preserved: no ``_hwnd`` → resolve_window is called."""

    def _fake_resolve(target: str) -> int:
        assert target == "chrome"
        return 77

    def _do_focus(hwnd: int, _fg: int, _t: int) -> None:
        assert hwnd == 77

    monkeypatch.setattr("voice_commander.resolver.resolve_window", _fake_resolve)
    monkeypatch.setattr("voice_commander.tools.primitives._do_focus", _do_focus)
    monkeypatch.setattr("voice_commander.tools.primitives._verify_foreground", lambda h: True)

    assert primitives.focus("chrome") == 77
```

- [ ] **Step 3: Run the test to verify it fails**

```powershell
pytest tests/unit/test_primitives_focus_hwnd.py -v
```

Expected: ``TypeError: focus() got an unexpected keyword argument '_hwnd'``.

- [ ] **Step 4: Update `focus()` to accept `_hwnd`**

Edit `src/voice_commander/tools/primitives.py`. Replace the `focus` function with:

```python
@tool
def focus(target: str = "", _hwnd: int = 0) -> int:
    """Focus a window.

    When ``_hwnd`` is non-zero, the resolver is skipped and the supplied
    Win32 handle is foregrounded directly. This is the path used by the
    bare-primitive focus picker, which already knows the hwnd it wants.

    Otherwise *target* is fuzzy-matched via :func:`resolver.resolve_window`
    as before.

    Returns
    -------
    int
        The Win32 window handle (hwnd) of the foregrounded window.

    Raises
    ------
    FocusWindowError
        If neither ``_hwnd`` nor a resolvable ``target`` is supplied, or if
        the focus attempt fails verification.
    """
    try:
        import win32con
        import win32gui
        import win32process
    except ImportError as exc:
        logger.warning("pywin32 not available, cannot focus window")
        raise FocusWindowError("pywin32 not available; cannot focus window") from exc

    if _hwnd:
        target_hwnd = int(_hwnd)
    else:
        if not target:
            raise FocusWindowError("focus() requires target or _hwnd")
        target_hwnd = resolver.resolve_window(target)

    if win32gui.IsIconic(target_hwnd):
        win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)

    fg_hwnd = win32gui.GetForegroundWindow()
    if fg_hwnd:
        foreground_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
    else:
        foreground_tid = 0
    target_tid, _ = win32process.GetWindowThreadProcessId(target_hwnd)

    try:
        _do_focus(target_hwnd, foreground_tid, target_tid)
    except Exception as exc:
        raise FocusWindowError(
            f"SetForegroundWindow failed for target={target!r} hwnd={target_hwnd}: {exc}"
        ) from exc

    if not _verify_foreground(target_hwnd):
        raise FocusWindowError(
            f"Focus verification failed for target={target!r} "
            f"hwnd={target_hwnd} (GetForegroundWindow did not match after 500 ms)"
        )

    return target_hwnd
```

- [ ] **Step 5: Update `primitives.toml`**

Edit the `[tools.focus]` block to add the optional `_hwnd` arg and relax `target` from `required = true` to `required = false`:

```toml
[tools.focus.args.target]
description = "Window to focus (e.g. 'notepad', 'chrome', 'spotify'). Fuzzy-matched against process names and window titles. Optional when '_hwnd' is supplied."
required = false
type = "string"
widget_kind = "window-picker"
default = ""

[tools.focus.args._hwnd]
description = "Pre-resolved Win32 window handle. Set by the bare-primitive picker; skips fuzzy resolution when non-zero."
required = false
type = "integer"
default = 0
```

- [ ] **Step 6: Run the tests to verify the changes**

```powershell
pytest tests/unit/test_primitives_focus_hwnd.py tests/unit -k "focus" -v
```

Expected: the new tests PASS and no pre-existing focus test regressed.

- [ ] **Step 7: Commit**

```powershell
git add src/voice_commander/tools/primitives.py src/voice_commander/tools/primitives.toml tests/unit/test_primitives_focus_hwnd.py
git commit -m "feat(picker(focus)): focus() accepts _hwnd shortcut path"
```

---

## Task 12: `VerbRouter` — bare-verb + picker registry → `__picker.open` Plan

**Files:**
- Modify: `src/voice_commander/verb_router.py`
- Modify: `tests/unit/test_verb_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_verb_router.py`:

```python
# ---------------------------------------------------------------------------
# Bare primitive picker (ADR 0083)
# ---------------------------------------------------------------------------


def test_bare_focus_with_no_picker_still_misses():
    """Existing behaviour preserved when no picker registry is wired."""
    router = VerbRouter(build_default_rules())
    assert router.route("focus") is None


def test_bare_focus_with_picker_registry_routes_to_picker_open():
    from voice_commander.picker.registry import BarePickerRegistry
    from voice_commander.picker.types import PickerItem

    reg = BarePickerRegistry()
    reg.register("focus", lambda: [])
    router = VerbRouter(build_default_rules(), picker_registry=reg)
    plan = router.route("focus")
    assert plan is not None
    assert plan.steps == (ToolCall(name="__picker.open", kwargs={"verb": "focus"}),)
    assert plan.raw_response["router"] == "verb"
    assert plan.raw_response["bare_picker"] is True


def test_bare_focus_with_picker_tolerates_punctuation():
    from voice_commander.picker.registry import BarePickerRegistry

    reg = BarePickerRegistry()
    reg.register("focus", lambda: [])
    router = VerbRouter(build_default_rules(), picker_registry=reg)
    assert router.route("Focus.") is not None
    assert router.route("FOCUS!") is not None


def test_focus_with_tail_still_routes_to_focus_primitive():
    """Adding a picker registry must not change the existing tail-routing path."""
    from voice_commander.picker.registry import BarePickerRegistry

    reg = BarePickerRegistry()
    reg.register("focus", lambda: [])
    router = VerbRouter(build_default_rules(), picker_registry=reg)
    plan = router.route("focus chrome")
    assert plan is not None
    assert plan.steps == (ToolCall(name="focus", kwargs={"target": "chrome"}),)
```

Also update the existing `test_focus_without_tail_misses`:

```python
def test_focus_without_tail_misses_when_no_picker_registered():
    """Bare verbs still miss unless a BarePickerRegistry is wired in (Task 12)."""
    assert _router().route("focus") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/unit/test_verb_router.py -v
```

Expected: the new tests fail — `VerbRouter` does not accept `picker_registry`.

- [ ] **Step 3: Wire `picker_registry` into `VerbRouter`**

Edit `src/voice_commander/verb_router.py`. Update the import block to add a forward reference:

```python
if TYPE_CHECKING:
    from .picker.registry import BarePickerRegistry
    from .registry import ToolRegistry
```

Update `__init__`:

```python
    def __init__(
        self,
        rules: tuple[VerbRule, ...],
        registry: "ToolRegistry | None" = None,
        picker_registry: "BarePickerRegistry | None" = None,
    ) -> None:
        self._rules = {rule.name: rule for rule in rules}
        self._alias_map: dict[str, str] = {}
        for rule in rules:
            for alias in rule.aliases:
                self._alias_map[alias] = rule.name
        self._registry = registry
        self._picker_registry = picker_registry
```

Update `route` — insert the picker check right before the existing `if not tail and verb.default_target is not None:` block but only when both `not tail` and no `default_target` would fire (so primitives like `click`/`scroll` keep their bare defaults). Replace the existing logic from `verb_name = self._alias_map.get(head)` through `return None` with:

```python
        verb_name = self._alias_map.get(head)
        if verb_name is None:
            return None
        verb = self._rules[verb_name]

        if not tail:
            # Bare verb with a default target (e.g. "click", "scroll") wins
            # over the picker so existing behaviour is preserved.
            if verb.default_target is not None:
                return self._plan_for(verb.default_target, verb.name, tail)
            # No default target, no tail — try the bare-primitive picker.
            if self._picker_registry is not None and self._picker_registry.has(verb.name):
                return Plan(
                    steps=(ToolCall(name="__picker.open", kwargs={"verb": verb.name}),),
                    raw_response={
                        "router": "verb",
                        "verb": verb.name,
                        "tail": "",
                        "bare_picker": True,
                    },
                )

        if tail:
            for sub in verb.subcommands:
                if tail.lower() in sub.aliases:
                    return self._plan_for(sub.target, verb.name, tail)

        if tail and verb.raw_tail_tool and verb.raw_tail_arg:
            arg_value: object = tail
            if verb.tail_coerce is not None:
                try:
                    arg_value = verb.tail_coerce(tail)
                except (ValueError, TypeError):
                    return None
            return Plan(
                steps=(ToolCall(name=verb.raw_tail_tool, kwargs={verb.raw_tail_arg: arg_value}),),
                raw_response={"router": "verb", "verb": verb.name, "tail": tail},
            )

        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_verb_router.py -v
```

Expected: all router tests PASS (existing + new four).

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/verb_router.py tests/unit/test_verb_router.py
git commit -m "feat(picker): VerbRouter routes bare verb → __picker.open Plan"
```

---

## Task 13: Daemon pipeline — intercept `__picker.open`, route active-picker transcripts

**Files:**
- Modify: `src/voice_commander/daemon.py`
- Create: `tests/integration/test_picker_pipeline.py`

- [ ] **Step 1: Write the failing integration test**

Write `tests/integration/test_picker_pipeline.py`:

```python
"""Daemon pipeline integration tests for the bare-primitive picker."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from voice_commander.event_bus import EventBus
from voice_commander.feedback import FeedbackSink
from voice_commander.picker.mru import MruEntry, MruTracker
from voice_commander.picker.registry import BarePickerRegistry
from voice_commander.picker.session import PickerSession
from voice_commander.picker.types import PickerItem
from voice_commander.plan import Plan, ToolCall
from voice_commander.tools.focus_picker import FocusPickerSettings, build_focus_picker


class _CaptureFeedback(FeedbackSink):
    def __init__(self) -> None:
        self.missed: list[str] = []
        self.transcripts: list[str] = []
        self.plan_starts: list[tuple[str, int]] = []
        self.plan_completes: list[tuple[str, int]] = []
        self.errors: list[tuple[str, BaseException]] = []

    def on_recording_start(self) -> None: ...
    def on_recording_stop(self) -> None: ...
    def on_transcript(self, text: str, confidence: float) -> None:
        self.transcripts.append(text)
    def on_plan_start(self, transcript: str, n_steps: int) -> None:
        self.plan_starts.append((transcript, n_steps))
    def on_plan_complete(self, transcript: str, executed: int) -> None:
        self.plan_completes.append((transcript, executed))
    def on_miss(self, transcript: str, top3: tuple) -> None:
        self.missed.append(transcript)
    def on_error(self, source: str, exc: BaseException) -> None:
        self.errors.append((source, exc))


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray):
        return self.queue.pop(0)


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


def _make_daemon(*, mru_entries: list[MruEntry], transcripts: list[_Transcription]):
    """Build a stripped-down daemon for pipeline-only testing."""
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.registry import ToolRegistry, get_global_registry, reset_global_registry
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.verb_router import VerbRouter, build_default_rules

    reset_global_registry()
    reset_global_picker_registry()

    # Stub registry with focus tool that records calls.
    captured: list[dict] = []

    def _focus(target: str = "", _hwnd: int = 0) -> int:
        captured.append({"target": target, "_hwnd": _hwnd})
        return _hwnd or 1

    from voice_commander.registry import ToolEntry
    registry = get_global_registry()
    registry.register(
        ToolEntry(
            name="focus",
            phrases=(),
            func=_focus,
            module="test",
            docstring=None,
        )
    )

    tracker = MruTracker(capacity=16)
    for e in mru_entries:
        tracker.record(e)

    picker_reg = BarePickerRegistry()
    provider = build_focus_picker(
        tracker=tracker,
        settings=FocusPickerSettings(cap=5, exclude_foreground=False, exclude_self=False),
        foreground_hwnd=lambda: 0,
    )
    picker_reg.register("focus", provider)

    bus = EventBus()
    feedback = _CaptureFeedback()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=picker_reg)
    picker_session = PickerSession(bus=bus, cancel_words=("cancel",), timeout_sec=5.0)

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
    )
    daemon._transcriber_ready.set()
    daemon._picker_session = picker_session  # set in build_streaming_daemon for prod
    daemon._picker_registry = picker_reg

    return daemon, captured, feedback, bus, picker_session


def test_bare_focus_opens_picker_then_number_fires_focus(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        MruEntry(hwnd=11, pid=1100, proc_name="chrome.exe", title="VC"),
        MruEntry(hwnd=22, pid=2200, proc_name="Code.exe", title="daemon.py"),
        MruEntry(hwnd=33, pid=3300, proc_name="slack.exe", title="eng"),
    ]
    # entries recorded in order 11, 22, 33 → top is 33, 22, 11.
    daemon, captured, feedback, bus, session = _make_daemon(
        mru_entries=entries,
        transcripts=[_Transcription("focus."), _Transcription("two.")],
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "focus." — opens picker
    assert session.active is True
    assert captured == []  # no focus call yet

    daemon._process_utterance(audio)  # "two." — selects items[1]
    assert session.active is False
    # Items were [33, 22, 11]; "two" → items[1] → hwnd 22.
    assert captured == [{"target": "", "_hwnd": 22}]


def test_bare_focus_cancel_word_closes_no_dispatch() -> None:
    entries = [MruEntry(hwnd=11, pid=1, proc_name="chrome.exe", title="t")]
    daemon, captured, feedback, bus, session = _make_daemon(
        mru_entries=entries,
        transcripts=[_Transcription("focus."), _Transcription("cancel.")],
    )
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    assert session.active is False
    assert captured == []


def test_bare_focus_out_of_range_keeps_picker_open() -> None:
    entries = [MruEntry(hwnd=11, pid=1, proc_name="chrome.exe", title="t")]
    daemon, captured, feedback, bus, session = _make_daemon(
        mru_entries=entries,
        transcripts=[_Transcription("focus."), _Transcription("seven."), _Transcription("one.")],
    )
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "focus."
    assert session.active is True
    daemon._process_utterance(audio)  # "seven." — out of range
    assert session.active is True
    assert captured == []
    daemon._process_utterance(audio)  # "one." — valid
    assert session.active is False
    assert captured == [{"target": "", "_hwnd": 11}]
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/integration/test_picker_pipeline.py -v
```

Expected: AttributeError or behavioural failure — daemon does not own a `_picker_session` yet, and `__picker.open` is not intercepted.

- [ ] **Step 3: Integrate `PickerSession` into the daemon**

Edit `src/voice_commander/daemon.py`. Update the imports near the top:

```python
from .picker.registry import BarePickerRegistry
from .picker.session import PickerSession
```

Add new init params (default `None` so existing call sites keep working):

```python
        registry: ToolRegistry | None = None,
        picker_session: PickerSession | None = None,
        picker_registry: BarePickerRegistry | None = None,
        ...
```

Store them:

```python
        self._picker_session = picker_session
        self._picker_registry = picker_registry
```

Update `_process_utterance` so picker-active transcripts go through the session before the verb router. Replace the section between `self._publish("transcript", ...)` and the `plan = self._verb_router.route(result.text)` block with:

```python
            # Picker sub-state (ADR 0083): if a bare-primitive picker is open,
            # the next utterance is a selection, not a new command.
            if self._picker_session is not None and self._picker_session.active:
                outcome = self._picker_session.handle_transcript(result.text)
                if outcome is None:
                    pass  # closed between check and call — fall through
                elif outcome.kind == "select" and outcome.plan is not None:
                    if self._registry is None:
                        logger.error("Registry not set — cannot run picker selection")
                        return
                    self._dispatcher.run_plan(result.text, outcome.plan, self._registry)
                    return
                elif outcome.kind == "cancel":
                    self._feedback.on_plan_complete(result.text, 0)
                    return
                else:  # miss — out-of-range / non-number
                    run.set_status("miss")
                    self._feedback.on_miss(result.text, ())
                    _publish_miss(result.text)
                    return

            # Gate: word-count  (infrastructure noise — no plan_outcome)
            word_count = len(result.text.split())
```

Update the post-`VerbRouter` block — after `plan = self._verb_router.route(result.text)` and the existing `if plan is None: ... return` — insert an intercept for `__picker.open` before the dispatcher call:

```python
            if (
                self._picker_session is not None
                and self._picker_registry is not None
                and len(plan.steps) == 1
                and plan.steps[0].name == "__picker.open"
            ):
                verb = str(plan.steps[0].kwargs.get("verb", ""))
                provider = self._picker_registry.get(verb)
                if provider is None:
                    logger.warning("picker open requested for unknown verb %r", verb)
                    self._feedback.on_miss(result.text, ())
                    _publish_miss(result.text)
                    return
                items = provider()
                if not items:
                    logger.info("picker %r produced empty list", verb)
                    self._feedback.on_miss(result.text, ())
                    _publish_miss(result.text)
                    return
                self._picker_session.open(verb, items)
                self._feedback.on_plan_complete(result.text, 0)
                return
```

(Place this block right above `if self._registry is None:`.)

- [ ] **Step 4: Run the integration test to verify it passes**

```powershell
pytest tests/integration/test_picker_pipeline.py -v
```

Expected: all three tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/daemon.py tests/integration/test_picker_pipeline.py
git commit -m "feat(picker): daemon intercepts __picker.open + routes active-picker utterances"
```

---

## Task 14: Heartbeat ticks the picker session for timeout

**Files:**
- Modify: `src/voice_commander/daemon.py`
- Modify: `tests/integration/test_picker_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_picker_pipeline.py`:

```python
def test_heartbeat_tick_times_out_open_picker() -> None:
    entries = [MruEntry(hwnd=11, pid=1, proc_name="chrome.exe", title="t")]
    daemon, captured, feedback, bus, session = _make_daemon(
        mru_entries=entries,
        transcripts=[_Transcription("focus.")],
    )
    # Replace the session with one that uses a controllable clock + 1 s timeout.
    from voice_commander.picker.session import PickerSession
    clock = {"t": 100.0}
    daemon._picker_session = PickerSession(
        bus=bus, now=lambda: clock["t"], cancel_words=(), timeout_sec=1.0
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    assert daemon._picker_session.active is True

    # Simulate one heartbeat tick within the timeout window.
    clock["t"] = 100.5
    daemon._tick_picker_session()
    assert daemon._picker_session.active is True

    # Simulate one tick past the timeout window.
    clock["t"] = 101.5
    daemon._tick_picker_session()
    assert daemon._picker_session.active is False
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
pytest tests/integration/test_picker_pipeline.py::test_heartbeat_tick_times_out_open_picker -v
```

Expected: AttributeError — `_tick_picker_session` not defined.

- [ ] **Step 3: Wire the tick into the heartbeat loop**

Edit `src/voice_commander/daemon.py`. Add the helper:

```python
    def _tick_picker_session(self) -> None:
        if self._picker_session is not None:
            self._picker_session.tick()
```

Update `_heartbeat_loop`:

```python
    def _heartbeat_loop(self) -> None:
        while not self._shutdown.wait(1.0):
            self._publish("daemon_heartbeat")
            self._tick_picker_session()
```

- [ ] **Step 4: Run the test to verify it passes**

```powershell
pytest tests/integration/test_picker_pipeline.py -v
```

Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_commander/daemon.py tests/integration/test_picker_pipeline.py
git commit -m "feat(picker): heartbeat ticks PickerSession for timeout"
```

---

## Task 15: `build_streaming_daemon` wires MRU tracker + picker session + registry + provider

**Files:**
- Modify: `src/voice_commander/daemon.py` (factory)

- [ ] **Step 1: Add the wiring**

Inside `build_streaming_daemon`, after `event_bus = EventBus()` and before the `StreamingDaemon(...)` construction, insert:

```python
    # --- Bare-primitive picker (ADR 0083) ---
    from .picker.mru import MruTracker, Win32MruPump
    from .picker.registry import get_global_picker_registry
    from .picker.session import PickerSession
    from .tools.focus_picker import FocusPickerSettings, register_focus_picker

    picker_session: PickerSession | None = None
    picker_registry = get_global_picker_registry()
    mru_tracker: MruTracker | None = None
    mru_pump: Win32MruPump | None = None

    if cfg.picker.enabled:
        mru_tracker = MruTracker(capacity=max(8, cfg.picker.focus.cap * 4))

        def _foreground_hwnd() -> int:
            try:
                import win32gui

                return int(win32gui.GetForegroundWindow() or 0)
            except Exception:
                return 0

        register_focus_picker(
            tracker=mru_tracker,
            settings=FocusPickerSettings(
                cap=cfg.picker.focus.cap,
                exclude_foreground=cfg.picker.focus.exclude_foreground,
                exclude_self=cfg.picker.focus.exclude_self,
            ),
            foreground_hwnd=_foreground_hwnd,
        )

        picker_session = PickerSession(
            bus=event_bus,
            cancel_words=tuple(cfg.picker.cancel_words),
            timeout_sec=float(cfg.picker.timeout_sec),
        )

        mru_pump = Win32MruPump(tracker=mru_tracker)
        mru_pump.start()
```

Pass `picker_session` + `picker_registry` to the `StreamingDaemon(...)` call:

```python
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=transcriber,
        dispatcher=dispatcher,
        verb_router=VerbRouter(
            build_default_rules(),
            registry=registry,
            picker_registry=picker_registry if cfg.picker.enabled else None,
        ),
        registry=registry,
        picker_session=picker_session,
        picker_registry=picker_registry if cfg.picker.enabled else None,
        min_confidence=cfg.transcription.min_confidence,
        min_word_count=cfg.vad.gates.min_word_count,
        max_no_speech_prob=cfg.vad.gates.max_no_speech_prob,
        output_dir=cfg.audio.output_dir,
        web_server=web_server,
        event_bus=event_bus,
        tracer=_obs_tracer,
        store=_obs_store,
    )
```

Stash `mru_pump` on the daemon so shutdown can stop it. Right after the `daemon = ...` line:

```python
    daemon._mru_pump = mru_pump
```

In `StreamingDaemon.__init__`, add the field declaration alongside `_config_watcher`:

```python
        self._mru_pump: Any = None
```

In `StreamingDaemon.shutdown`, before stopping the observability store, add:

```python
        if self._mru_pump is not None:
            try:
                self._mru_pump.stop()
            except Exception:
                logger.exception("Error stopping MRU pump")
            self._mru_pump = None
```

- [ ] **Step 2: Run the daemon-wiring tests**

```powershell
pytest tests/unit/test_daemon_wiring.py tests/integration/test_picker_pipeline.py -v
```

Expected: all PASS. The pipeline tests should still pass because they pass `picker_session` + `picker_registry` directly; the wiring change is only used by the real factory.

- [ ] **Step 3: Smoke-run the daemon (manual)**

```powershell
python -m voice_commander
```

Switch between three different windows, then press Scroll Lock, say "focus". The modal will not render yet (sprite hasn't been wired), but the daemon log should show `picker.open` event publication and no crash. Press Scroll Lock again to end. Stop with Ctrl+C.

- [ ] **Step 4: Commit**

```powershell
git add src/voice_commander/daemon.py
git commit -m "feat(picker): build_streaming_daemon wires MruTracker + PickerSession + provider"
```

---

## Task 16: Sprite-side modal window — `PickerModalWindow`

**Files:**
- Create: `src/voice_sprite/picker_modal.py`
- Create: `tests/unit/test_picker_modal.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_picker_modal.py` (purely tests rendering math and state; no pyglet):

```python
from __future__ import annotations

from voice_sprite.picker_modal import (
    ModalGeometry,
    ModalState,
    compute_modal_position,
    format_rows,
)


def test_format_rows_renders_numbered_lines():
    rows = format_rows([{"n": 1, "label": "Chrome"}, {"n": 2, "label": "VS Code"}])
    assert rows == ["1. Chrome", "2. VS Code"]


def test_format_rows_handles_empty_list():
    assert format_rows([]) == []


def test_compute_modal_position_centres_on_work_area():
    geometry = ModalGeometry(width=300, height=180)
    pos = compute_modal_position(
        geometry=geometry,
        work_area=(0, 0, 1920, 1080),
    )
    assert pos == (1920 // 2 - 150, 1080 // 2 - 90)


def test_compute_modal_position_centres_on_secondary_monitor():
    geometry = ModalGeometry(width=300, height=180)
    pos = compute_modal_position(
        geometry=geometry,
        work_area=(1920, 0, 1920, 1080),
    )
    assert pos == (1920 + 1920 // 2 - 150, 1080 // 2 - 90)


def test_modal_state_open_close_round_trip():
    state = ModalState()
    assert state.visible is False
    state.open(verb="focus", items=[{"n": 1, "label": "Chrome"}])
    assert state.visible is True
    assert state.verb == "focus"
    assert state.rows == ["1. Chrome"]
    state.close()
    assert state.visible is False
    assert state.rows == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```powershell
pytest tests/unit/test_picker_modal.py -v
```

Expected: import error.

- [ ] **Step 3: Implement the modal helpers + pyglet window**

Write `src/voice_sprite/picker_modal.py`:

```python
"""Always-on-top centred modal window rendered by the sprite process.

The modal mirrors :class:`voice_commander.picker.session.PickerSession` state
via two SSE events — ``picker.open`` (with ``items`` payload) and
``picker.close``.

Rendering math is split out as pure functions so it can be unit-tested
without pyglet. The :class:`PickerModalWindow` glues those pieces to
pyglet at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModalGeometry:
    width: int = 320
    height: int = 200


@dataclass
class ModalState:
    verb: str = ""
    rows: list[str] = field(default_factory=list)
    visible: bool = False

    def open(self, verb: str, items: list[dict[str, Any]]) -> None:
        self.verb = verb
        self.rows = format_rows(items)
        self.visible = True

    def close(self) -> None:
        self.verb = ""
        self.rows = []
        self.visible = False


def format_rows(items: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for item in items:
        n = item.get("n")
        label = item.get("label", "")
        if n is None:
            continue
        out.append(f"{n}. {label}")
    return out


def compute_modal_position(
    geometry: ModalGeometry,
    work_area: tuple[int, int, int, int],
) -> tuple[int, int]:
    """Return ``(x, y)`` for a centred modal within *work_area* (left, top, w, h)."""
    left, top, width, height = work_area
    x = left + (width - geometry.width) // 2
    y = top + (height - geometry.height) // 2
    return x, y


# ---------------------------------------------------------------------------
# Pyglet glue (only imported when the sprite process actually runs)
# ---------------------------------------------------------------------------


class PickerModalWindow:
    """Lazy pyglet window holder — show()/hide() driven by SSE events."""

    def __init__(self, geometry: ModalGeometry | None = None) -> None:
        self._geometry = geometry or ModalGeometry()
        self._state = ModalState()
        self._window: Any = None

    @property
    def state(self) -> ModalState:
        return self._state

    def show(self, verb: str, items: list[dict[str, Any]]) -> None:
        self._state.open(verb, items)
        self._ensure_window()
        if self._window is None:
            return
        self._window.set_visible(True)
        # Re-position on the current cursor's monitor (best-effort).
        try:
            self._reposition()
        except Exception:
            logger.exception("modal repositioning failed")
        self._window.refresh()

    def hide(self) -> None:
        self._state.close()
        if self._window is not None:
            self._window.set_visible(False)

    # ---- pyglet wiring ----

    def _ensure_window(self) -> None:
        if self._window is not None:
            return
        try:
            import pyglet
        except ImportError:
            logger.warning("pyglet not available; picker modal will not render")
            return
        self._window = _PygletModalWindow(self._state, self._geometry)
        self._window.apply_win32_flags()

    def _reposition(self) -> None:
        if self._window is None:
            return
        work_area = _get_cursor_work_area()
        x, y = compute_modal_position(self._geometry, work_area)
        self._window.set_location(x, y)


def _get_cursor_work_area() -> tuple[int, int, int, int]:
    """Return the work area (left, top, width, height) of the cursor's monitor."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        logger.exception("cursor work-area lookup failed; defaulting to (0,0,1920,1080)")
    return 0, 0, 1920, 1080


try:  # pragma: no cover — only loaded inside the sprite process
    import pyglet  # noqa: F401
except ImportError:
    _PygletModalWindow = None  # type: ignore[assignment]
else:
    import pyglet

    class _PygletModalWindow(pyglet.window.Window):  # type: ignore[misc]
        def __init__(self, state: ModalState, geometry: ModalGeometry) -> None:
            super().__init__(
                width=geometry.width,
                height=geometry.height,
                caption="vc-picker",
                resizable=False,
                visible=False,
                style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
            )
            self._state = state
            self._batch = pyglet.graphics.Batch()
            self._labels: list[pyglet.text.Label] = []

        def apply_win32_flags(self) -> None:
            try:
                from voice_sprite.win32_flags import apply_click_through

                apply_click_through(self._hwnd)
            except Exception:
                logger.exception("apply_click_through failed for picker modal")

        def refresh(self) -> None:
            # Discard previous labels.
            for lbl in self._labels:
                lbl.delete()
            self._labels = []

            header = pyglet.text.Label(
                f"{self._state.verb.upper()} — SAY NUMBER",
                font_size=10,
                bold=True,
                x=12,
                y=self.height - 22,
                anchor_x="left",
                anchor_y="top",
                color=(255, 204, 0, 255),
                batch=self._batch,
            )
            self._labels.append(header)

            for i, row in enumerate(self._state.rows):
                lbl = pyglet.text.Label(
                    row,
                    font_size=12,
                    x=12,
                    y=self.height - 44 - i * 22,
                    anchor_x="left",
                    anchor_y="top",
                    color=(228, 231, 236, 255),
                    batch=self._batch,
                )
                self._labels.append(lbl)

            footer = pyglet.text.Label(
                'say "cancel" to exit',
                font_size=9,
                x=12,
                y=10,
                anchor_x="left",
                anchor_y="bottom",
                color=(122, 130, 144, 255),
                batch=self._batch,
            )
            self._labels.append(footer)

        def on_draw(self) -> None:
            pyglet.gl.glClearColor(0.13, 0.15, 0.18, 0.95)
            self.clear()
            self._batch.draw()
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
pytest tests/unit/test_picker_modal.py -v
```

Expected: all five tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/voice_sprite/picker_modal.py tests/unit/test_picker_modal.py
git commit -m "feat(sprite(picker)): PickerModalWindow + pure rendering helpers"
```

---

## Task 17: Sprite `__main__` — handle `picker.open` / `picker.close` SSE events

**Files:**
- Modify: `src/voice_sprite/__main__.py`

- [ ] **Step 1: Wire the modal into the sprite event handler**

Edit `src/voice_sprite/__main__.py`. Inside `main()`, after the `chat_log = ChatLog(...)` block (before pyglet display setup), instantiate the modal:

```python
    from .picker_modal import PickerModalWindow

    picker_modal = PickerModalWindow()
```

Inside `on_event(event_type, data)`, add two branches before the existing `tool_fired` / `plan_outcome` / `transcript` ones:

```python
        if event_type == "picker.open":
            try:
                verb = str(data.get("verb", ""))
                items = list(data.get("items", []))
                picker_modal.show(verb, items)
            except Exception:
                logger.exception("picker_modal.show failed")
            return
        if event_type == "picker.close":
            try:
                picker_modal.hide()
            except Exception:
                logger.exception("picker_modal.hide failed")
            return
```

(Place these *above* the sprite-state evaluation — the modal events are not state-machine transitions and should not affect `sm.on_event`.)

- [ ] **Step 2: Manual smoke test**

Open two terminal windows. In the first, run:

```powershell
python -m voice_commander
```

Wait for the warm-up to finish. In the second, run:

```powershell
python -m voice_sprite
```

Then in the daemon terminal, you should already have the daemon log line `picker.open` if you trigger the picker — open at least 3 different windows for an MRU history first.

Press Scroll Lock to open a session. Say "focus." — verify the centered modal appears with up to 5 numbered rows. Say "two." — verify the second window is focused and the modal disappears. Say another command (e.g. "click") to confirm the original voice session is still alive. Press Scroll Lock to close.

Negative manual checks:

- Open the picker, say "cancel." → modal hides silently.
- Open the picker, say "seven." → modal stays, miss chime fires.
- Open the picker, wait 5 seconds → modal auto-hides, miss chime fires.
- Open the picker, press Scroll Lock → modal hides + session ends.

If anything misbehaves, stop and fix before continuing. **Validation gate: a human must confirm the end-to-end flow before Task 18.**

- [ ] **Step 3: Commit**

```powershell
git add src/voice_sprite/__main__.py
git commit -m "feat(sprite(picker)): handle picker.open/picker.close SSE events"
```

---

## Task 18: ADR + docs updates

**Files:**
- Create: `docs/decisions/0083-bare-primitive-picker.md`
- Modify: `docs/agents/technical-decisions.md`
- Modify: `CLAUDE.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Write the ADR**

Create `docs/decisions/0083-bare-primitive-picker.md`:

```markdown
# ADR 0083 — Bare-Primitive Picker Framework

**Status:** Accepted
**Date:** 2026-05-13
**Spec:** `docs/superpowers/specs/2026-05-13-bare-primitive-picker-design.md`

## Context

A bare primitive verb (e.g. ``focus`` with no argument) was a miss. The user
wants bare verbs to open an inline disambiguation: a numbered modal of
recent targets, the next utterance picks one by number. The same shape
should generalise to future ``tab`` / ``open`` pickers without bespoke
infra per verb.

## Decision

We ship a pluggable framework rooted at ``voice_commander.picker``:

- **D1.** Picker UI is a centred always-on-top modal on the active monitor.
- **D2.** Default item cap is 5 (TOML-configurable per verb).
- **D3.** MRU source is a ``WinEventHook(EVENT_SYSTEM_FOREGROUND)`` ring buffer.
- **D4.** Picker is a sub-state of the running voice session: mic stays hot,
  original session continues after selection.
- **D5.** Pluggable via ``@bare_picker(verb)`` registering on a global
  ``BarePickerRegistry``.
- **D6.** Daemon owns state; the sprite process renders the modal via SSE
  ``picker.open`` / ``picker.close`` events.

The ``VerbRouter`` returns a synthetic ``Plan`` with a single
``__picker.open`` step when it sees a bare verb that has a provider; the
daemon intercepts that step name before dispatch and calls
``PickerSession.open(...)`` instead. While a picker is open the daemon's
pipeline routes the next transcript through ``PickerSession.handle_transcript``
rather than the ``VerbRouter``.

## Consequences

- New package: ``voice_commander.picker`` (registry / session / coerce / mru / types).
- New sprite module: ``voice_sprite.picker_modal``.
- ``focus()`` gains an optional ``_hwnd`` shortcut so picker selections skip
  fuzzy re-resolution.
- One new config section: ``[picker]`` + ``[picker.focus]``.
- New SSE events: ``picker.open``, ``picker.close``.
- One synthetic tool name reserved: ``__picker.open`` (never registered in
  ``ToolRegistry``; intercepted in the pipeline).

## Alternatives considered

- **Vimium-style per-window number badges.** Higher discoverability but
  much more rendering surface area (per-monitor overlay, hwnd-to-screen
  projection, DPI scaling). Deferred.
- **Daemon-rendered Tkinter modal.** Avoids growing sprite responsibility,
  but adds a new GUI lib in the daemon process — breaks the headless-
  daemon invariant.
- **Hardcoded focus-only branch.** Fastest to ship; future pickers would
  duplicate the modal + coerce path. Rejected — boils-the-ocean principle.

## References

- Spec: `docs/superpowers/specs/2026-05-13-bare-primitive-picker-design.md`
- Plan: `docs/superpowers/plans/2026-05-13-bare-primitive-picker.md`
```

- [ ] **Step 2: Append the ADR summary row**

Edit `docs/agents/technical-decisions.md`. Locate the table (or section structured as one) and append a row at the bottom:

```markdown
| 0083 | 2026-05-13 | Bare-primitive picker framework — bare verb opens numbered modal; pluggable `@bare_picker(verb)`; MRU via WinEventHook; daemon owns state; sprite renders modal via SSE. |
```

- [ ] **Step 3: Update CLAUDE.md "Current state" paragraph**

Edit the "Current state" paragraph in `CLAUDE.md`. After the existing sentence about how `VerbRouter.route()` works, insert:

> When a primitive verb (`focus`, `open`, …) is said without an argument and a `BarePickerProvider` is registered for it (ADR 0083), the router emits a synthetic `__picker.open` step that the daemon intercepts to open an on-screen numbered modal of candidate targets; the next utterance is coerced to an integer ("three" / "3") and dispatched as the chosen item's pre-built `Plan`. The picker is a sub-state of the running voice session — the mic stays hot, the session continues after selection. The framework lives under `src/voice_commander/picker/` and ships one provider today (`focus`), backed by an MRU ring buffer driven by a `WinEventHook(EVENT_SYSTEM_FOREGROUND)`. The sprite process renders the modal via two new SSE events: `picker.open` (payload `{verb, items: [{n, label}]}`) and `picker.close` (payload `{verb, reason}`).

- [ ] **Step 4: Update `docs/architecture.md`**

Find the section that describes subsystem contracts (per the existing convention) and add a new sub-section:

```markdown
### `picker/` — bare-primitive picker framework (ADR 0083)

- `picker.types.PickerItem(label: str, action: Plan)` — frozen.
- `picker.types.PickerProvider = Callable[[], list[PickerItem]]`.
- `picker.registry.BarePickerRegistry` — verb→provider map; `@bare_picker(verb)` decorator.
- `picker.coerce.coerce_number(text, max_n) -> int | None` — handles digits, words, ordinals, prefixed ("number 3"), suffixed ("third one").
- `picker.mru.MruTracker` — thread-safe deque with self-hwnd registration + predicate-based `top(n, predicate=...)`.
- `picker.mru.Win32MruPump` — `SetWinEventHook` message-pump thread; injected hook + resolver hooks for tests.
- `picker.session.PickerSession` — state machine (`open` / `close` / `cancel` / `handle_transcript` / `tick`); publishes `picker.open` + `picker.close` on the EventBus.

The pipeline check is: if `picker_session.active`, the next transcript is routed through `PickerSession.handle_transcript` before the `VerbRouter`; otherwise the router runs as today and, on bare verb + registered provider, returns a `Plan(steps=(ToolCall("__picker.open", ...),))` that the daemon intercepts.
```

- [ ] **Step 5: Commit**

```powershell
git add docs/decisions/0083-bare-primitive-picker.md docs/agents/technical-decisions.md CLAUDE.md docs/architecture.md
git commit -m "docs(picker): ADR 0083 + CLAUDE.md + architecture.md + technical-decisions.md"
```

---

## Task 19: Final validation pass

- [ ] **Step 1: Run the full test suite**

```powershell
pytest -q
```

Expected: all tests PASS. If anything regressed, fix it before claiming done. Do not ship a green plan with a red suite.

- [ ] **Step 2: Re-run the manual end-to-end smoke from Task 17**

Run daemon + sprite. Validate the four golden paths (select / cancel / out-of-range / timeout) plus the Scroll-Lock-abort path. Validate session continues after selection by chaining a second command.

- [ ] **Step 3: Re-read the spec**

Open `docs/superpowers/specs/2026-05-13-bare-primitive-picker-design.md`. Walk every section. Confirm each requirement maps to a landed task. Note anything that drifted.

- [ ] **Step 4: Open a PR**

```powershell
git push -u origin <branch>
gh pr create --title "feat(picker): bare-primitive picker framework + focus provider" --body "$(cat <<'EOF'
## Summary
- Pluggable bare-primitive picker framework rooted at `voice_commander.picker`.
- One provider registered: `focus` — MRU-driven, capped at 5 items.
- Sprite renders modal via SSE `picker.open` / `picker.close`.
- ADR 0083 + CLAUDE.md + architecture.md updated.

## Test plan
- [x] `pytest -q` green.
- [x] Manual: bare `focus.` opens modal, `two.` focuses item 2, session continues.
- [x] Manual: `cancel.` closes modal.
- [x] Manual: `seven.` (out of range) keeps modal open + miss chime.
- [x] Manual: 5 s silence times out modal.
- [x] Manual: Scroll Lock during picker aborts cleanly.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review (for the plan author)

### 1. Spec coverage

| Spec requirement | Task |
|---|---|
| Pluggable BarePickerProvider + registry + decorator | T2 |
| PickerItem dataclass | T1 |
| coerce_number (digits/words/ordinals/prefixed/suffixed) | T3 |
| MruTracker ring buffer + filters | T5 |
| Win32 SetWinEventHook pump | T6 |
| PickerSession state machine (open/close/cancel/handle_transcript/tick) | T7–T9 |
| focus_picker provider built on MruTracker | T10 |
| focus() accepts _hwnd shortcut | T11 |
| VerbRouter bare-verb → __picker.open | T12 |
| Daemon pipeline intercepts __picker.open + routes active picker transcripts | T13 |
| Heartbeat ticks PickerSession for timeout | T14 |
| build_streaming_daemon wires MruTracker + Win32MruPump + PickerSession | T15 |
| Sprite PickerModalWindow + helpers | T16 |
| Sprite event_client wiring (picker.open / picker.close) | T17 |
| Config [picker] + [picker.focus] | T4 |
| ADR 0083 + CLAUDE.md + technical-decisions + architecture | T18 |
| End-to-end + negative manual validation | T17 manual + T19 |

### 2. Placeholder scan

No "TBD" / "TODO" / "fill in details" / "similar to Task N" markers. Each step shows the actual code to write or command to run. The label formatter, MRU pump install hook, modal CSS palette, and config dataclass field types are all concrete.

### 3. Type consistency

- `PickerItem(label, action)` matches across types.py, registry tests, session tests, focus_picker tests, and daemon integration.
- `PickerProvider = Callable[[], list[PickerItem]]` matches every consumer.
- `coerce_number(text, max_n)` signature is identical in coerce.py and the session's `handle_transcript`.
- `MruTracker.top(n, predicate=...)` signature matches between mru.py implementation and focus_picker tests.
- `PickerSession.handle_transcript` returns `PickerOutcome | None`; both pipeline integration test and the implementation agree.
- `Win32MruPump(tracker, install_hook=..., resolve_hwnd=...)` signature matches between mru.py and the test.
- `__picker.open` synthetic step name is identical in VerbRouter, daemon intercept, and the integration test.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-13-bare-primitive-picker.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
