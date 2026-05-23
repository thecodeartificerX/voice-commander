# Named Command Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add voice-switchable, scoped command catalogs ("modes"): saying a mode's trigger (`"video"`) inside a session matches only that mode's commands + the global primitives; saying `"video end"` returns to normal command mode.

**Architecture:** A mode is a self-contained `modes/<name>.toml` catalog. Each command's `action` is a transcript string compiled at load time through a **base-primitives `VerbRouter`** into a cached `Plan` of primitive `ToolCall`s. A per-mode `ModeRouter` matches utterances against a phrase→`Plan` map (longest-match), falling through to the base-primitives router for raw primitives/chain/repeat. A `ModeSession` (mirroring `ElementsSession`/`PickerSession`) tracks the active mode and publishes `mode.enter`/`mode.exit` on the `EventBus`. The daemon inserts a mode sub-state slot into `_process_utterance` after the picker check. The sprite renders a persistent top-centre badge while a mode is active. Dispatch reuses the existing global registry, so compiled primitive plans resolve unchanged.

**Implementation note — refinement vs. spec §2.** The spec described a "per-mode `VerbRouter` … with the mode's command registry." Because `Dispatcher.run_plan(transcript, plan, registry)` resolves each `ToolCall.name` via `registry.by_name(...)`, and mode actions compile to *primitive* calls already in the global registry, we do **not** build per-mode `ToolRegistry`/`ToolEntry` objects or per-mode dispatch. Instead a mode command is a `(phrases, compiled Plan)` pair. This is the same Approach B (isolated per-mode routing, base engine unchanged) realised without dispatcher coupling. The ADR (Task 14) documents this as the authoritative mechanism.

**Tech Stack:** Python 3.11, `tomllib`, `watchdog` (already a dep), pyglet (sprite), pytest, ruff, mypy.

---

## File structure

**New package — `src/voice_commander/modes/`:**
- `__init__.py` — re-exports the public surface.
- `types.py` — `ModeCommand`, `ModeDefinition`, `ModeOutcome` dataclasses.
- `compile.py` — `build_base_primitive_router()` + `compile_action(router, action)`.
- `loader.py` — `parse_mode_file(path, base_router) -> ModeDefinition`, `ModeLoadError`, reserved/dup/unresolved validation.
- `registry.py` — `ModeRegistry`: `load_all()`, `reload()`, `by_trigger()`, `all()`.
- `router.py` — `ModeRouter`: phrase→`Plan` longest-match + base-primitives fallthrough + synthetic-intercept guard.
- `session.py` — `ModeSession`: `try_enter()`, `handle_utterance()`, `exit()`, `reset()`; publishes events.
- `watcher.py` — `ModesWatcher`: watchdog directory watcher for `modes/*.toml` hot-reload.

**Modified:**
- `src/voice_commander/config.py` — add `ModesConfig`, wire into `Config`.
- `config.toml.example` — add `[modes]` section.
- `src/voice_commander/feedback.py` — add `on_mode_enter` / `on_mode_exit`.
- `src/voice_commander/daemon.py` — `__init__` param, `_process_utterance` mode slot, `_close_voice_session` reset, `build_streaming_daemon` wiring + watcher.
- `src/voice_sprite/state_machine.py` — `active_mode_badge` field + `mode.enter`/`mode.exit` handling.
- `src/voice_sprite/__main__.py` — `_apply_mode_badge` helper + call site.
- `src/voice_sprite/window.py` — `set_mode_badge()` + persistent top-centre badge render.

**New assets / scripts / docs:**
- `modes/video.toml` — DaVinci Resolve starter pack.
- `scripts/mode_visual_e2e.py` — sprite + SSE + PrintWindow badge assertion.
- `docs/decisions/0100-named-command-modes.md` — ADR.
- `docs/modes.md` — overview; linked from `docs/index.md`.
- `docs/references/davinci-resolve-shortcuts.md` — vendored research.
- `tests/unit/test_modes_*.py`, `tests/integration/test_modes_wiring.py`.

---

## Conventions for every task

- Run tests: `pytest <path> -v` (config sets `pythonpath=src`).
- Lint/type before each commit: `ruff check src tests --line-length 100 && ruff format src tests && mypy src tests --python-version 3.11`.
- Commit messages use Conventional Commits and end with the repo's Co-Authored-By trailer.

---

### Task 1: `ModesConfig` config section

**Files:**
- Modify: `src/voice_commander/config.py` (add dataclass near the other section dataclasses ~line 43; add field to `Config` ~line 183; add parse line in `Config.load()` ~line 238)
- Modify: `config.toml.example` (append `[modes]` section)
- Test: `tests/unit/test_config_modes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config_modes.py
from pathlib import Path

from voice_commander.config import Config, ModesConfig


def test_modes_defaults() -> None:
    cfg = ModesConfig()
    assert cfg.enabled is True
    assert cfg.dir == "modes"


def test_config_load_reads_modes_section(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text('[modes]\nenabled = false\ndir = "my_modes"\n', encoding="utf-8")
    cfg = Config.load(p)
    assert cfg.modes.enabled is False
    assert cfg.modes.dir == "my_modes"


def test_config_load_modes_defaults_when_absent(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("", encoding="utf-8")
    cfg = Config.load(p)
    assert cfg.modes.enabled is True
    assert cfg.modes.dir == "modes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config_modes.py -v`
Expected: FAIL — `ImportError: cannot import name 'ModesConfig'`.

- [ ] **Step 3: Implement**

Add the dataclass (place it next to the other `@dataclass(frozen=True)` section configs, e.g. after `ElementsConfig`):

```python
@dataclass(frozen=True)
class ModesConfig:
    """Named command modes (scoped, voice-switchable catalogs).

    ``dir`` is resolved relative to the repo root and scanned for
    ``*.toml`` mode catalogs.  See ``docs/modes.md``.
    """

    enabled: bool = True
    dir: str = "modes"
```

Add the field to `Config` (after the `elements:` field, ~line 183):

```python
    modes: ModesConfig = field(default_factory=ModesConfig)
```

Add the parse line in `Config.load()`'s return (next to `dictation=...`/`elements=...`):

```python
        modes=_section(ModesConfig, raw.get("modes", {})),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config_modes.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Update `config.toml.example`**

Append (match the existing aligned-comment style):

```toml
[modes]
# Named command modes (scoped, voice-switchable catalogs). Say a mode's
# trigger word (e.g. "video") inside a session to match only that mode's
# commands plus the global primitives; say "<trigger> end" to leave.
enabled = true     # master switch for the named-modes subsystem
dir     = "modes"  # directory (relative to repo root) of <name>.toml catalogs
```

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/config.py config.toml.example tests/unit/test_config_modes.py
git commit -m "feat(modes): add [modes] config section"
```

---

### Task 2: Mode types

**Files:**
- Create: `src/voice_commander/modes/__init__.py`
- Create: `src/voice_commander/modes/types.py`
- Test: `tests/unit/test_modes_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_modes_types.py
from voice_commander.modes.types import ModeCommand, ModeDefinition, ModeOutcome
from voice_commander.plan import Plan, ToolCall


def _plan() -> Plan:
    return Plan(steps=(ToolCall(name="press", kwargs={"combo": "ctrl+b"}),), raw_response={})


def test_mode_command_holds_phrases_and_plan() -> None:
    cmd = ModeCommand(phrases=("split", "blade"), plan=_plan())
    assert cmd.phrases == ("split", "blade")
    assert cmd.plan.steps[0].name == "press"


def test_mode_definition_fields() -> None:
    d = ModeDefinition(
        name="video",
        trigger="video",
        end_phrase="video end",
        badge="🎬 VIDEO",
        commands=(ModeCommand(phrases=("split",), plan=_plan()),),
    )
    assert d.name == "video"
    assert d.trigger == "video"
    assert d.end_phrase == "video end"
    assert d.badge == "🎬 VIDEO"
    assert len(d.commands) == 1


def test_mode_outcome_kinds() -> None:
    assert ModeOutcome(kind="miss").plan is None
    assert ModeOutcome(kind="plan", plan=_plan()).plan is not None
    assert ModeOutcome(kind="exit").plan is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_modes_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_commander.modes'`.

- [ ] **Step 3: Implement**

```python
# src/voice_commander/modes/__init__.py
"""Named command modes — scoped, voice-switchable command catalogs."""
```

```python
# src/voice_commander/modes/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..plan import Plan


@dataclass(frozen=True)
class ModeCommand:
    """One catalog entry: spoken phrases mapped to a pre-compiled plan."""

    phrases: tuple[str, ...]
    plan: Plan


@dataclass(frozen=True)
class ModeDefinition:
    """A named mode: trigger/end words, badge text, and its commands."""

    name: str
    trigger: str
    end_phrase: str
    badge: str
    commands: tuple[ModeCommand, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModeOutcome:
    """Result of routing an utterance while a mode is active."""

    kind: Literal["plan", "exit", "miss"]
    plan: Plan | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_modes_types.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/modes/__init__.py src/voice_commander/modes/types.py tests/unit/test_modes_types.py
git commit -m "feat(modes): mode dataclasses"
```

---

### Task 3: Action compiler (base-primitives router)

**Files:**
- Create: `src/voice_commander/modes/compile.py`
- Test: `tests/unit/test_modes_compile.py`

The compiler routes an `action` string through a `VerbRouter` built from `build_default_rules()` with **no command registry** (so only primitives/chain/repeat resolve, never user commands) and **no picker** (bare `focus`/`open`/`tabs`/`dictate` cannot stand in as actions). A `ChainParser` over an empty registry enables primitive chains.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_modes_compile.py
import pytest

from voice_commander.modes.compile import build_base_primitive_router, compile_action


def test_compiles_press_combo() -> None:
    router = build_base_primitive_router()
    plan = compile_action(router, "press ctrl+b")
    assert plan.steps[0].name == "press"
    assert plan.steps[0].kwargs == {"combo": "ctrl+b"}


def test_compiles_scroll_subcommand() -> None:
    router = build_base_primitive_router()
    plan = compile_action(router, "scroll up")
    assert plan.steps[0].name == "scroll"
    assert plan.steps[0].kwargs == {"direction": "up"}


def test_compiles_chain() -> None:
    router = build_base_primitive_router()
    plan = compile_action(router, "chain press ctrl+m press enter")
    names = [s.name for s in plan.steps]
    assert names.count("press") == 2  # plus internal wait separators


def test_unresolvable_action_raises() -> None:
    router = build_base_primitive_router()
    with pytest.raises(ValueError, match="could not be routed"):
        compile_action(router, "frobnicate the widget")


def test_synthetic_intercept_action_raises() -> None:
    # Bare "dictate" routes to a synthetic __dictation.start step — not a valid action.
    router = build_base_primitive_router()
    with pytest.raises(ValueError, match="synthetic"):
        compile_action(router, "dictate")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_modes_compile.py -v`
Expected: FAIL — `ModuleNotFoundError: ... modes.compile`.

- [ ] **Step 3: Implement**

```python
# src/voice_commander/modes/compile.py
from __future__ import annotations

from ..chain import ChainParser
from ..plan import Plan
from ..registry import ToolRegistry
from ..verb_router import VerbRouter, build_default_rules


def build_base_primitive_router() -> VerbRouter:
    """A VerbRouter that resolves ONLY primitives + chain + repeat.

    No command registry (user commands never match) and no picker
    (bare focus/open/tabs cannot be actions). Used both to compile mode
    actions at load time and as the in-mode primitive fallthrough.
    """
    rules = build_default_rules()
    empty = ToolRegistry()
    return VerbRouter(
        rules,
        registry=None,
        picker_registry=None,
        chain_parser=ChainParser(registry=empty, verb_rules=rules),
    )


def compile_action(router: VerbRouter, action: str) -> Plan:
    """Route *action* through the base router into a cached Plan.

    Raises ValueError if the action does not resolve, or resolves to a
    synthetic intercept step (``__dictation.start`` / ``__picker.open``)
    which cannot be executed as a mode action.
    """
    plan = router.route(action)
    if plan is None:
        raise ValueError(f"action could not be routed: {action!r}")
    if plan.steps and plan.steps[0].name.startswith("__"):
        raise ValueError(f"action resolves to a synthetic intercept (not allowed): {action!r}")
    return plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_modes_compile.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/modes/compile.py tests/unit/test_modes_compile.py
git commit -m "feat(modes): action compiler over base-primitives router"
```

---

### Task 4: Mode file loader + validation

**Files:**
- Create: `src/voice_commander/modes/loader.py`
- Test: `tests/unit/test_modes_loader.py`

Reserved triggers = primitive verb names + chain heads + `dictate` + elements entry words. Validation errors raise `ModeLoadError` naming the file.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_modes_loader.py
from pathlib import Path

import pytest

from voice_commander.modes.compile import build_base_primitive_router
from voice_commander.modes.loader import ModeLoadError, parse_mode_file


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_parses_valid_mode(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "video.toml",
        """
[mode]
badge = "🎬 VIDEO"

[[command]]
phrases = ["split", "blade"]
action  = "press ctrl+b"
""",
    )
    d = parse_mode_file(p, build_base_primitive_router())
    assert d.name == "video"
    assert d.trigger == "video"          # default = filename stem
    assert d.end_phrase == "video end"   # default
    assert d.badge == "🎬 VIDEO"
    assert d.commands[0].phrases == ("split", "blade")
    assert d.commands[0].plan.steps[0].kwargs == {"combo": "ctrl+b"}


def test_explicit_trigger_and_end_phrase(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "m.toml",
        '[mode]\ntrigger = "mouseless"\nend_phrase = "mouseless off"\n\n'
        '[[command]]\nphrases=["go"]\naction="scroll down"\n',
    )
    d = parse_mode_file(p, build_base_primitive_router())
    assert d.trigger == "mouseless"
    assert d.end_phrase == "mouseless off"


def test_reserved_trigger_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "press.toml", '[[command]]\nphrases=["x"]\naction="press a"\n')
    with pytest.raises(ModeLoadError, match="reserved"):
        parse_mode_file(p, build_base_primitive_router())


def test_unresolvable_action_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "v.toml", '[[command]]\nphrases=["x"]\naction="frobnicate"\n')
    with pytest.raises(ModeLoadError, match="could not be routed"):
        parse_mode_file(p, build_base_primitive_router())


def test_empty_phrases_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "v.toml", '[[command]]\nphrases=[]\naction="press a"\n')
    with pytest.raises(ModeLoadError, match="phrases"):
        parse_mode_file(p, build_base_primitive_router())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_modes_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: ... modes.loader`.

- [ ] **Step 3: Implement**

```python
# src/voice_commander/modes/loader.py
from __future__ import annotations

import tomllib
from pathlib import Path

from ..chain import _HEAD_ALIASES as _CHAIN_HEADS
from ..elements.session import ENTRY_WORDS as _ELEMENTS_WORDS
from ..verb_router import VerbRouter, _normalize_spoken, build_default_rules
from .compile import compile_action
from .types import ModeCommand, ModeDefinition


class ModeLoadError(ValueError):
    """Raised when a mode TOML file is invalid."""


def _reserved_triggers() -> set[str]:
    reserved = {r.name for r in build_default_rules()}
    reserved |= set(_CHAIN_HEADS)
    reserved |= set(_ELEMENTS_WORDS)
    reserved.add("dictate")
    return {_normalize_spoken(w) for w in reserved}


def parse_mode_file(path: Path, base_router: VerbRouter) -> ModeDefinition:
    """Parse a single ``modes/<name>.toml`` file into a ModeDefinition.

    Raises ModeLoadError (naming *path*) on any structural or semantic error.
    """
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ModeLoadError(f"{path.name}: cannot parse TOML: {e}") from e

    meta = raw.get("mode", {})
    if not isinstance(meta, dict):
        raise ModeLoadError(f"{path.name}: [mode] must be a table")

    name = _normalize_spoken(path.stem)
    trigger = _normalize_spoken(str(meta.get("trigger", name)))
    if not trigger:
        raise ModeLoadError(f"{path.name}: empty trigger")
    if trigger in _reserved_triggers():
        raise ModeLoadError(f"{path.name}: trigger {trigger!r} is reserved")
    end_phrase = _normalize_spoken(str(meta.get("end_phrase", f"{trigger} end")))
    badge = str(meta.get("badge", trigger.upper()))

    cmds_raw = raw.get("command", [])
    if not isinstance(cmds_raw, list):
        raise ModeLoadError(f"{path.name}: [[command]] must be an array of tables")

    commands: list[ModeCommand] = []
    for i, c in enumerate(cmds_raw):
        if not isinstance(c, dict):
            raise ModeLoadError(f"{path.name}: command #{i} is not a table")
        phrases = c.get("phrases", [])
        if not isinstance(phrases, list) or not phrases:
            raise ModeLoadError(f"{path.name}: command #{i} needs a non-empty phrases list")
        action = c.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ModeLoadError(f"{path.name}: command #{i} needs a string action")
        try:
            plan = compile_action(base_router, action)
        except ValueError as e:
            raise ModeLoadError(f"{path.name}: command #{i} {e}") from e
        commands.append(ModeCommand(phrases=tuple(str(p) for p in phrases), plan=plan))

    return ModeDefinition(
        name=name,
        trigger=trigger,
        end_phrase=end_phrase,
        badge=badge,
        commands=tuple(commands),
    )
```

> If `_HEAD_ALIASES` is not importable from `chain.py`, open `src/voice_commander/chain.py`, confirm the symbol name (the verb_router imports `from .chain import _HEAD_ALIASES as _CHAIN_HEADS`), and use the same name.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_modes_loader.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/modes/loader.py tests/unit/test_modes_loader.py
git commit -m "feat(modes): TOML loader + validation"
```

---

### Task 5: ModeRegistry (directory scan + reload + trigger index)

**Files:**
- Create: `src/voice_commander/modes/registry.py`
- Test: `tests/unit/test_modes_registry.py`

Mirrors `ToolMetadataStore.load_all` (glob `*.toml`, rebuild index). A bad file is logged and **skipped**, never fatal. Duplicate triggers across files: first wins, later logged + skipped.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_modes_registry.py
from pathlib import Path

from voice_commander.modes.registry import ModeRegistry


def _mode(tmp: Path, fname: str, trigger: str, action: str = "press a") -> None:
    (tmp / fname).write_text(
        f'[mode]\ntrigger = "{trigger}"\n\n[[command]]\nphrases=["x"]\naction="{action}"\n',
        encoding="utf-8",
    )


def test_load_all_indexes_by_trigger(tmp_path: Path) -> None:
    _mode(tmp_path, "video.toml", "video")
    _mode(tmp_path, "mouseless.toml", "mouseless")
    reg = ModeRegistry(tmp_path)
    reg.load_all()
    assert {d.trigger for d in reg.all()} == {"video", "mouseless"}
    assert reg.by_trigger("video") is not None
    assert reg.by_trigger("nope") is None


def test_bad_file_skipped_not_fatal(tmp_path: Path) -> None:
    _mode(tmp_path, "good.toml", "video")
    (tmp_path / "bad.toml").write_text('[[command]]\nphrases=["x"]\naction="frobnicate"\n', "utf-8")
    reg = ModeRegistry(tmp_path)
    reg.load_all()
    assert reg.by_trigger("video") is not None
    assert len(reg.all()) == 1


def test_missing_dir_is_empty(tmp_path: Path) -> None:
    reg = ModeRegistry(tmp_path / "does_not_exist")
    reg.load_all()
    assert reg.all() == []


def test_reload_picks_up_new_file(tmp_path: Path) -> None:
    _mode(tmp_path, "video.toml", "video")
    reg = ModeRegistry(tmp_path)
    reg.load_all()
    assert len(reg.all()) == 1
    _mode(tmp_path, "mouseless.toml", "mouseless")
    reg.reload()
    assert len(reg.all()) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_modes_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: ... modes.registry`.

- [ ] **Step 3: Implement**

```python
# src/voice_commander/modes/registry.py
from __future__ import annotations

import logging
from pathlib import Path

from ..verb_router import _normalize_spoken
from .compile import build_base_primitive_router
from .loader import ModeLoadError, parse_mode_file
from .types import ModeDefinition

logger = logging.getLogger(__name__)


class ModeRegistry:
    """Discovers and holds all mode catalogs under *modes_dir*."""

    def __init__(self, modes_dir: Path) -> None:
        self._dir = Path(modes_dir)
        self._by_trigger: dict[str, ModeDefinition] = {}

    def load_all(self) -> None:
        """Glob ``*.toml`` and (re)build the trigger index. Idempotent."""
        base_router = build_base_primitive_router()
        new_index: dict[str, ModeDefinition] = {}
        if not self._dir.is_dir():
            self._by_trigger = new_index
            return
        for path in sorted(self._dir.glob("*.toml")):
            try:
                d = parse_mode_file(path, base_router)
            except ModeLoadError as e:
                logger.warning("modes: skipping invalid file: %s", e)
                continue
            key = _normalize_spoken(d.trigger)
            if key in new_index:
                logger.warning(
                    "modes: duplicate trigger %r in %s — keeping first", d.trigger, path.name
                )
                continue
            new_index[key] = d
        self._by_trigger = new_index

    # reload is an alias so call sites read intentionally.
    def reload(self) -> None:
        self.load_all()

    def by_trigger(self, trigger: str) -> ModeDefinition | None:
        return self._by_trigger.get(_normalize_spoken(trigger))

    def all(self) -> list[ModeDefinition]:
        return list(self._by_trigger.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_modes_registry.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/modes/registry.py tests/unit/test_modes_registry.py
git commit -m "feat(modes): ModeRegistry directory scan + reload"
```

---

### Task 6: ModeRouter (phrase match + primitive fallthrough)

**Files:**
- Create: `src/voice_commander/modes/router.py`
- Test: `tests/unit/test_modes_router.py`

Phrase matching reuses the normalization from `verb_router` (lowercase, punctuation-stripped, underscore→space) and longest-token-count-wins. Fallthrough uses a shared base-primitives router. A primitive that resolves to a synthetic intercept (`dictate`) is treated as a miss in-mode.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_modes_router.py
from voice_commander.modes.compile import build_base_primitive_router, compile_action
from voice_commander.modes.router import ModeRouter
from voice_commander.modes.types import ModeCommand, ModeDefinition


def _video() -> ModeDefinition:
    base = build_base_primitive_router()
    return ModeDefinition(
        name="video",
        trigger="video",
        end_phrase="video end",
        badge="🎬 VIDEO",
        commands=(
            ModeCommand(phrases=("split", "cut clip"), plan=compile_action(base, "press ctrl+b")),
        ),
    )


def test_matches_mode_command() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    plan = r.route("split")
    assert plan is not None and plan.steps[0].kwargs == {"combo": "ctrl+b"}


def test_matches_multiword_synonym_punctuation_tolerant() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    assert r.route("Cut clip.") is not None


def test_primitive_fallthrough_still_works() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    plan = r.route("scroll up")
    assert plan is not None and plan.steps[0].name == "scroll"


def test_unknown_phrase_misses() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    assert r.route("open spotify by name foobar baz") is None or r.route("kerfuffle") is None
    assert r.route("kerfuffle") is None


def test_synthetic_intercept_is_a_miss_in_mode() -> None:
    r = ModeRouter(_video(), build_base_primitive_router())
    assert r.route("dictate") is None  # bare dictate would be __dictation.start
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_modes_router.py -v`
Expected: FAIL — `ModuleNotFoundError: ... modes.router`.

- [ ] **Step 3: Implement**

```python
# src/voice_commander/modes/router.py
from __future__ import annotations

from ..plan import Plan
from ..verb_router import VerbRouter, _normalize_spoken
from .types import ModeDefinition


def _spoken(s: str) -> str:
    return _normalize_spoken(s.replace("_", " "))


class ModeRouter:
    """Routes an utterance against one mode: phrases first, then primitives."""

    def __init__(self, definition: ModeDefinition, base_router: VerbRouter) -> None:
        self._def = definition
        self._base = base_router
        # (normalized phrase, plan) sorted longest-first so specific wins.
        pairs: list[tuple[str, Plan]] = []
        for cmd in definition.commands:
            for phrase in cmd.phrases:
                norm = _spoken(phrase)
                if norm:
                    pairs.append((norm, cmd.plan))
        pairs.sort(key=lambda p: len(p[0].split()), reverse=True)
        self._pairs = pairs

    def route(self, transcript: str) -> Plan | None:
        normalized = _normalize_spoken(transcript)
        if not normalized:
            return None
        for phrase, plan in self._pairs:
            if phrase == normalized:
                return plan
        # Primitive / chain / repeat fallthrough (mode + primitives).
        plan = self._base.route(transcript)
        if plan is None:
            return None
        if plan.steps and plan.steps[0].name.startswith("__"):
            return None  # synthetic intercepts (dictate/picker) unsupported in-mode
        return plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_modes_router.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/modes/router.py tests/unit/test_modes_router.py
git commit -m "feat(modes): ModeRouter phrase match + primitive fallthrough"
```

---

### Task 7: ModeSession (state machine + events)

**Files:**
- Create: `src/voice_commander/modes/session.py`
- Test: `tests/unit/test_modes_session.py`

Mirrors `PickerSession` (takes a bus, publishes events). `try_enter` is called in normal mode; `handle_utterance` while active; `exit`/`reset` clear state. Each enter builds a `ModeRouter` from the entered definition + a fresh base-primitives router, captured for the mode's lifetime (so a hot-reload mid-mode does not swap the live router).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_modes_session.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from voice_commander.modes.registry import ModeRegistry
from voice_commander.modes.session import ModeSession


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


def _registry(tmp_path: Path) -> ModeRegistry:
    (tmp_path / "video.toml").write_text(
        '[mode]\nbadge = "🎬 VIDEO"\n\n'
        '[[command]]\nphrases=["split"]\naction="press ctrl+b"\n',
        encoding="utf-8",
    )
    reg = ModeRegistry(tmp_path)
    reg.load_all()
    return reg


def test_inactive_by_default(tmp_path: Path) -> None:
    s = ModeSession(_FakeBus(), _registry(tmp_path))
    assert s.active is False
    assert s.active_mode is None


def test_try_enter_on_trigger_publishes(tmp_path: Path) -> None:
    bus = _FakeBus()
    s = ModeSession(bus, _registry(tmp_path))
    assert s.try_enter("video") is not None
    assert s.active is True
    assert s.active_mode == "video"
    assert bus.events[0][0] == "mode.enter"
    assert bus.events[0][1] == {"name": "video", "badge": "🎬 VIDEO"}


def test_try_enter_non_trigger_returns_none(tmp_path: Path) -> None:
    s = ModeSession(_FakeBus(), _registry(tmp_path))
    assert s.try_enter("split") is None
    assert s.active is False


def test_in_mode_command_returns_plan(tmp_path: Path) -> None:
    s = ModeSession(_FakeBus(), _registry(tmp_path))
    s.try_enter("video")
    out = s.handle_utterance("split")
    assert out.kind == "plan" and out.plan is not None
    assert out.plan.steps[0].kwargs == {"combo": "ctrl+b"}


def test_end_phrase_exits_and_publishes(tmp_path: Path) -> None:
    bus = _FakeBus()
    s = ModeSession(bus, _registry(tmp_path))
    s.try_enter("video")
    out = s.handle_utterance("video end")
    assert out.kind == "exit"
    assert s.active is False
    assert bus.events[-1][0] == "mode.exit"
    assert bus.events[-1][1] == {"name": "video", "reason": "end_phrase"}


def test_unknown_in_mode_is_miss_and_stays(tmp_path: Path) -> None:
    s = ModeSession(_FakeBus(), _registry(tmp_path))
    s.try_enter("video")
    assert s.handle_utterance("kerfuffle").kind == "miss"
    assert s.active is True  # stays in mode on a miss


def test_reset_exits_silently_with_event(tmp_path: Path) -> None:
    bus = _FakeBus()
    s = ModeSession(bus, _registry(tmp_path))
    s.try_enter("video")
    s.reset()
    assert s.active is False
    assert bus.events[-1][0] == "mode.exit"
    assert bus.events[-1][1] == {"name": "video", "reason": "session_ended"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_modes_session.py -v`
Expected: FAIL — `ModuleNotFoundError: ... modes.session`.

- [ ] **Step 3: Implement**

```python
# src/voice_commander/modes/session.py
from __future__ import annotations

from typing import Protocol

from ..verb_router import _normalize_spoken
from .compile import build_base_primitive_router
from .registry import ModeRegistry
from .router import ModeRouter
from .types import ModeDefinition, ModeOutcome


class _Bus(Protocol):
    def publish(self, event_type: str, data: dict | None = None) -> None: ...


class ModeSession:
    """Tracks the active named mode and routes in-mode utterances."""

    def __init__(self, bus: _Bus, registry: ModeRegistry) -> None:
        self._bus = bus
        self._registry = registry
        self._active: ModeDefinition | None = None
        self._router: ModeRouter | None = None

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def active_mode(self) -> str | None:
        return self._active.name if self._active else None

    def try_enter(self, transcript: str) -> ModeDefinition | None:
        """In normal mode: enter a mode if *transcript* is a registered trigger."""
        if self._active is not None:
            return None
        d = self._registry.by_trigger(_normalize_spoken(transcript))
        if d is None:
            return None
        self._active = d
        self._router = ModeRouter(d, build_base_primitive_router())
        self._bus.publish("mode.enter", {"name": d.name, "badge": d.badge})
        return d

    def handle_utterance(self, transcript: str) -> ModeOutcome:
        """While active: end-phrase exits; else route against the mode catalog."""
        if self._active is None or self._router is None:
            return ModeOutcome(kind="miss")
        if _normalize_spoken(transcript) == self._active.end_phrase:
            self.exit("end_phrase")
            return ModeOutcome(kind="exit")
        plan = self._router.route(transcript)
        if plan is None:
            return ModeOutcome(kind="miss")
        return ModeOutcome(kind="plan", plan=plan)

    def exit(self, reason: str) -> None:
        if self._active is None:
            return
        name = self._active.name
        self._active = None
        self._router = None
        self._bus.publish("mode.exit", {"name": name, "reason": reason})

    def reset(self) -> None:
        """Force-exit on session close (Scroll Lock)."""
        self.exit("session_ended")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_modes_session.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/modes/session.py tests/unit/test_modes_session.py
git commit -m "feat(modes): ModeSession state machine + SSE events"
```

---

### Task 8: Feedback chimes for mode enter/exit

**Files:**
- Modify: `src/voice_commander/feedback.py` (Protocol ~line 17-26; `WindowsFeedbackSink` ~line 83-131)
- Test: `tests/unit/test_feedback_modes.py`

Reuse existing sound assets: enter → the recording-start sound, exit → the recording-stop sound (distinct, no new binaries). Add the methods to the `FeedbackSink` Protocol and **every** concrete implementer.

- [ ] **Step 1: Find all implementers**

Run: `grep -rn "FeedbackSink" src/voice_commander | grep -i "class\|Sink)"`
Note every concrete sink class (e.g. `WindowsFeedbackSink`, and any null/logging sink). Add the two new methods to each.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_feedback_modes.py
from pathlib import Path

from voice_commander.feedback import WindowsFeedbackSink


def test_mode_enter_exit_play_existing_sounds(tmp_path: Path, monkeypatch) -> None:
    played: list[Path] = []
    sink = WindowsFeedbackSink(sounds_dir=tmp_path)
    monkeypatch.setattr(sink, "_play", lambda p: played.append(p))
    sink.on_mode_enter()
    sink.on_mode_exit()
    assert played[0] == sink._start
    assert played[1] == sink._stop
```

> If `WindowsFeedbackSink.__init__` requires more args than `sounds_dir`, construct it the way the existing feedback tests do (check `tests/unit/test_feedback*.py` and copy the constructor call).

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/test_feedback_modes.py -v`
Expected: FAIL — `AttributeError: 'WindowsFeedbackSink' object has no attribute 'on_mode_enter'`.

- [ ] **Step 4: Implement**

Add to the `FeedbackSink` Protocol:

```python
    def on_mode_enter(self) -> None: ...
    def on_mode_exit(self) -> None: ...
```

Add to `WindowsFeedbackSink` (reusing the existing start/stop paths):

```python
    def on_mode_enter(self) -> None:
        self._play(self._start)

    def on_mode_exit(self) -> None:
        self._play(self._stop)
```

Add no-op (or log) implementations to any other concrete sink found in Step 1.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_feedback_modes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/feedback.py tests/unit/test_feedback_modes.py
git commit -m "feat(modes): enter/exit feedback chimes"
```

---

### Task 9: Daemon wiring (sub-state slot + trigger intercept + reset + factory)

**Files:**
- Modify: `src/voice_commander/daemon.py`
  - imports (top of file)
  - `Daemon.__init__` signature (~line 152) + storage (~line 276)
  - `_process_utterance` insertion **after the picker block (~line 802), before the word-count gate (~line 804)**
  - `_close_voice_session` reset (~line 441, next to the elements cancel)
  - `build_streaming_daemon` factory: registry+session construction (~line 1459) and pass to the `StreamingDaemon(...)` call (~line 1557)
- Test: `tests/integration/test_modes_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_modes_wiring.py
"""Smoke test: build_streaming_daemon wires a ModeSession when modes/ exists."""
from __future__ import annotations

from pathlib import Path

import pytest

from voice_commander.config import Config


@pytest.mark.integration
def test_daemon_has_mode_session_when_enabled(tmp_path: Path, monkeypatch) -> None:
    modes = tmp_path / "modes"
    modes.mkdir()
    (modes / "video.toml").write_text(
        '[mode]\nbadge="🎬 VIDEO"\n\n[[command]]\nphrases=["split"]\naction="press ctrl+b"\n',
        encoding="utf-8",
    )
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'[modes]\nenabled = true\ndir = "{modes.as_posix()}"\n', encoding="utf-8")
    cfg = Config.load(cfg_path)

    from voice_commander.daemon import build_streaming_daemon

    monkeypatch.chdir(tmp_path)
    daemon = build_streaming_daemon(cfg, config_path=cfg_path)
    assert daemon._mode_session is not None
    assert daemon._mode_session.active is False
    assert daemon._mode_session._registry.by_trigger("video") is not None
```

> If `build_streaming_daemon` cannot run headless (loads the Whisper model, audio devices, etc.), instead assert wiring at the unit level: import the factory, and verify the `ModeRegistry`/`ModeSession` construction block exists by testing the smaller helper you extract in Step 3 (a module-level `build_mode_session(cfg, bus) -> ModeSession | None`). Prefer the helper approach if the full factory is too heavy to instantiate in a test.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_modes_wiring.py -v`
Expected: FAIL — `AttributeError: ... '_mode_session'` (or factory wiring missing).

- [ ] **Step 3: Implement — imports**

At the top of `daemon.py` with the other local imports:

```python
from .modes.registry import ModeRegistry
from .modes.session import ModeSession
```

- [ ] **Step 4: Implement — `__init__` param + storage**

Add a keyword-only parameter to `Daemon.__init__` (next to `elements_session`):

```python
        mode_session: ModeSession | None = None,
```

Store it (next to `self._elements_session = elements_session`):

```python
        self._mode_session = mode_session
```

- [ ] **Step 5: Implement — pipeline slot**

In `_process_utterance`, immediately **after** the picker block's final `return` (~line 802) and **before** the `# Gate: word-count` block (~line 804), insert:

```python
        # Mode sub-state (named modes): while a mode is active, utterances
        # route against that mode's catalog + primitives only; the end phrase
        # exits. A miss stays in the mode.
        if self._mode_session is not None and self._mode_session.active:
            outcome = self._mode_session.handle_utterance(result.text)
            if outcome.kind == "exit":
                self._feedback.on_mode_exit()
                run.set_status("ok")
                return
            if outcome.kind == "plan" and outcome.plan is not None:
                self._dispatcher.run_plan(result.text, outcome.plan, self._registry)
                return
            run.set_status("miss")
            self._feedback.on_miss(result.text, ())
            _publish_miss(result.text)
            return

        # Mode-trigger intercept (normal mode only): a trigger word enters a mode.
        if self._mode_session is not None:
            entered = self._mode_session.try_enter(result.text)
            if entered is not None:
                self._feedback.on_mode_enter()
                run.set_status("ok")
                return
```

> Confirm the local names `run`, `_publish_miss`, and `self._registry` are in scope here (they are used by the picker/normal blocks just above). If `_publish_miss` is defined later in the function than this insertion point, move the insertion to just before the first use of `_publish_miss` in the normal path, keeping it after the picker block.

- [ ] **Step 6: Implement — reset on session close**

In `_close_voice_session`, next to the elements-cancel (~line 440-441):

```python
        if self._mode_session is not None and self._mode_session.active:
            self._mode_session.reset()
```

- [ ] **Step 7: Implement — factory wiring**

In `build_streaming_daemon`, after the elements session is created (~line 1459) and before the `daemon = StreamingDaemon(...)` call:

```python
    mode_session: ModeSession | None = None
    if cfg.modes.enabled:
        modes_dir = Path(cfg.modes.dir)
        mode_registry = ModeRegistry(modes_dir)
        mode_registry.load_all()
        mode_session = ModeSession(event_bus, mode_registry)
        logger.info("modes: loaded %d mode(s) from %s", len(mode_registry.all()), modes_dir)
```

Pass it to the constructor call (next to `elements_session=elements_session,`):

```python
        mode_session=mode_session,
```

> `Path` is already imported in `daemon.py` (used for `config_path`). If `event_bus` is named differently at this point in the factory, use the local variable that holds the `EventBus` (Agent mapping: `event_bus` created ~line 1392).

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/integration/test_modes_wiring.py -v`
Expected: PASS (or, if using the helper fallback from Step 1, the helper test passes).

- [ ] **Step 9: Lint + commit**

```bash
ruff check src tests --line-length 100 && ruff format src tests && mypy src tests --python-version 3.11
git add src/voice_commander/daemon.py tests/integration/test_modes_wiring.py
git commit -m "feat(modes): wire ModeSession into daemon pipeline + factory"
```

---

### Task 10: Sprite — persistent mode badge

**Files:**
- Modify: `src/voice_sprite/state_machine.py` (`__init__` ~line 82-99; `on_event` ~line 101-166)
- Modify: `src/voice_sprite/__main__.py` (helper ~line 57-86; call site ~line 370)
- Modify: `src/voice_sprite/window.py` (fields ~line 157-162; new method after `set_processing`; on_draw badge block after the PROCESSING block ~line 333)
- Test: `tests/unit/test_sprite_mode_badge.py`

The badge draws at **top-centre** (anchor_y="top", y=height-2) so it never overlaps the bottom-centre DICTATING/PROCESSING/CANCELLED badges. Persistent (PROCESSING-style), not auto-clear.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sprite_mode_badge.py
from voice_sprite.state_machine import StateMachine


def test_mode_enter_sets_badge() -> None:
    sm = StateMachine()
    sm.on_event("mode.enter", {"name": "video", "badge": "🎬 VIDEO"})
    assert sm.active_mode_badge == "🎬 VIDEO"


def test_mode_exit_clears_badge() -> None:
    sm = StateMachine()
    sm.on_event("mode.enter", {"name": "video", "badge": "🎬 VIDEO"})
    sm.on_event("mode.exit", {"name": "video", "reason": "end_phrase"})
    assert sm.active_mode_badge is None


def test_mode_enter_falls_back_to_name_when_no_badge() -> None:
    sm = StateMachine()
    sm.on_event("mode.enter", {"name": "mouseless"})
    assert sm.active_mode_badge == "MOUSELESS"
```

Also test the apply helper:

```python
# append to tests/unit/test_sprite_mode_badge.py
class _FakeWindow:
    def __init__(self) -> None:
        self.badge: str | None = "sentinel"

    def set_mode_badge(self, text: str | None) -> None:
        self.badge = text


def test_apply_mode_badge_pushes_to_window() -> None:
    from voice_sprite.__main__ import _apply_mode_badge

    sm = StateMachine()
    win = _FakeWindow()
    _apply_mode_badge(sm, win)
    assert win.badge is None  # no mode active
    sm.on_event("mode.enter", {"name": "video", "badge": "🎬 VIDEO"})
    _apply_mode_badge(sm, win)
    assert win.badge == "🎬 VIDEO"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sprite_mode_badge.py -v`
Expected: FAIL — `AttributeError: ... 'active_mode_badge'`.

- [ ] **Step 3: Implement — state machine**

Add the field in `StateMachine.__init__` (next to `self.cancelled_cue`):

```python
        self.active_mode_badge: str | None = None
```

In `on_event`, before the `EVENT_STATE_MAP.get(...)` lookup, add:

```python
        if event_type == "mode.enter":
            badge = data.get("badge") or str(data.get("name", "")).upper()
            self.active_mode_badge = badge
            return None
        if event_type == "mode.exit":
            self.active_mode_badge = None
            return None
```

- [ ] **Step 4: Implement — apply helper + call site**

Add the helper in `__main__.py` (near `_apply_processing_state`):

```python
def _apply_mode_badge(sm: Any, window: Any) -> None:
    """Push the persistent mode badge (or clear it) onto *window*."""
    window.set_mode_badge(sm.active_mode_badge)
```

Add the call right after the `_apply_cancelled_cue(...)` call (~line 370):

```python
    _apply_mode_badge(sm, window)
```

- [ ] **Step 5: Implement — window field, setter, render**

Add fields (next to `self._processing_badge_label`):

```python
        self._mode_badge_label: pyglet.text.Label | None = None
        self._active_mode_badge: str | None = None
```

Add the setter (after `set_processing`):

```python
    def set_mode_badge(self, text: str | None) -> None:
        """Show (str) or hide (None) the persistent named-mode badge."""
        self._active_mode_badge = text
```

Add the render block in `on_draw`, after the PROCESSING badge block:

```python
        if self._active_mode_badge:
            if self._mode_badge_label is None:
                self._mode_badge_label = pyglet.text.Label(
                    self._active_mode_badge,
                    font_name="Segoe UI",
                    font_size=9,
                    weight="bold",
                    x=self.width // 2,
                    y=self.height - 2,
                    anchor_x="center",
                    anchor_y="top",
                    color=(120, 220, 140, 255),
                )
            else:
                self._mode_badge_label.text = self._active_mode_badge
            # Re-centre + re-top every frame: CursorDock resizes the window.
            self._mode_badge_label.x = self.width // 2
            self._mode_badge_label.y = self.height - 2
            self._mode_badge_label.draw()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/unit/test_sprite_mode_badge.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add src/voice_sprite/state_machine.py src/voice_sprite/__main__.py src/voice_sprite/window.py tests/unit/test_sprite_mode_badge.py
git commit -m "feat(modes): persistent sprite mode badge"
```

---

### Task 11: Vendor DaVinci research + ship `modes/video.toml`

**Files:**
- Create: `docs/references/davinci-resolve-shortcuts.md`
- Create: `modes/video.toml`
- Test: `tests/unit/test_modes_video_starter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_modes_video_starter.py
from pathlib import Path

from voice_commander.modes.compile import build_base_primitive_router
from voice_commander.modes.loader import parse_mode_file
from voice_commander.modes.router import ModeRouter

VIDEO = Path(__file__).resolve().parents[2] / "modes" / "video.toml"


def test_video_starter_loads_and_routes() -> None:
    d = parse_mode_file(VIDEO, build_base_primitive_router())
    assert d.trigger == "video"
    r = ModeRouter(d, build_base_primitive_router())
    cases = {
        "split": {"combo": "ctrl+b"},
        "undo": {"combo": "ctrl+z"},
        "redo": {"combo": "ctrl+shift+z"},
        "one": {"combo": "1"},
        "two": {"combo": "2"},
        "three": {"combo": "3"},
        "ripple": {"combo": "delete"},
        "back": {"combo": "up"},
        "forward": {"combo": "down"},
        "play": {"combo": "space"},
        "pause": {"combo": "space"},
    }
    for phrase, kwargs in cases.items():
        plan = r.route(phrase)
        assert plan is not None, phrase
        assert plan.steps[0].name == "press"
        assert plan.steps[0].kwargs == kwargs, phrase
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_modes_video_starter.py -v`
Expected: FAIL — `FileNotFoundError: ... modes/video.toml`.

- [ ] **Step 3: Create `modes/video.toml`**

```toml
[mode]
trigger    = "video"
end_phrase = "video end"
badge      = "🎬 VIDEO"

# DaVinci Resolve — Edit page, default Windows shortcuts.
# See docs/references/davinci-resolve-shortcuts.md for the source mapping.

[[command]]
phrases = ["split", "blade", "razor", "cut clip"]
action  = "press ctrl+b"

[[command]]
phrases = ["undo"]
action  = "press ctrl+z"

[[command]]
phrases = ["redo"]
action  = "press ctrl+shift+z"

[[command]]
phrases = ["one"]
action  = "press 1"

[[command]]
phrases = ["two"]
action  = "press 2"

[[command]]
phrases = ["three"]
action  = "press 3"

[[command]]
phrases = ["ripple", "ripple delete"]
action  = "press delete"

[[command]]
phrases = ["back", "previous", "last cut"]
action  = "press up"

[[command]]
phrases = ["forward", "next", "next cut"]
action  = "press down"

[[command]]
phrases = ["play"]
action  = "press space"

[[command]]
phrases = ["pause", "stop"]
action  = "press space"
```

- [ ] **Step 4: Create `docs/references/davinci-resolve-shortcuts.md`**

```markdown
# DaVinci Resolve — Edit Page Default Windows Shortcuts (vendored)

Researched 2026-05-23 for `modes/video.toml`. DaVinci Resolve 18–20, Edit page,
default Windows bindings. Shortcuts are user-customisable (Ctrl+Alt+K) and vary
slightly across versions; this is the default set the starter mode targets.

| Action | Combo | Notes |
|---|---|---|
| Split / blade / razor at playhead | `ctrl+b` | Splits across all tracks |
| Undo | `ctrl+z` | |
| Redo | `ctrl+shift+z` | Resolve uses Ctrl+Shift+Z, not Ctrl+Y |
| Ripple delete (close gap) | `delete` | Windows **forward Delete** ripples; **Backspace** lifts (leaves a gap) |
| Lift / delete (leave gap) | `backspace` | for reference; not bound in the starter |
| Previous edit point | `up` | |
| Next edit point | `down` | |
| Play / pause | `space` | single toggle (JKL: J reverse, K stop, L play forward) |
| Number keys 1/2/3 | `1` / `2` / `3` | literal — the user has bound custom shortcuts to these |

Sources:
- Blackmagic DaVinci Resolve manual (JKL transport; Undo/Redo).
- Evercast / Simon Says AI / StoryBlocks / Pixflow shortcut references.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_modes_video_starter.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add modes/video.toml docs/references/davinci-resolve-shortcuts.md tests/unit/test_modes_video_starter.py
git commit -m "feat(modes): ship DaVinci video.toml starter + vendor research"
```

---

### Task 12: Hot-reload watcher for `modes/*.toml`

**Files:**
- Create: `src/voice_commander/modes/watcher.py`
- Modify: `src/voice_commander/daemon.py` (start the watcher in `build_streaming_daemon` next to the ConfigWatcher wiring ~line 633-645; add a `_on_modes_changed` callback method)
- Test: `tests/unit/test_modes_watcher.py`

Mirrors `ConfigWatcher` but watches the modes **directory** for `*.toml` (watchdog `PatternMatchingEventHandler(patterns=["*.toml"])`, non-recursive), debounced. The callback calls `registry.reload()` and publishes `modes_reloaded`. An active in-flight `ModeSession` keeps its captured router (Task 7), so reload is safe mid-mode.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_modes_watcher.py
import time
from pathlib import Path

from voice_commander.modes.watcher import ModesWatcher


def test_watcher_fires_on_new_file(tmp_path: Path) -> None:
    fired: list[Path] = []
    w = ModesWatcher(tmp_path, lambda p: fired.append(p), debounce_ms=50)
    w.start()
    try:
        (tmp_path / "video.toml").write_text('[[command]]\nphrases=["x"]\naction="press a"\n', "utf-8")
        deadline = time.monotonic() + 5.0
        while not fired and time.monotonic() < deadline:
            time.sleep(0.05)
        assert fired, "watcher did not fire within 5s"
    finally:
        w.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_modes_watcher.py -v`
Expected: FAIL — `ModuleNotFoundError: ... modes.watcher`.

- [ ] **Step 3: Implement**

Open `src/voice_commander/config_watcher.py` and copy its structure. Then:

```python
# src/voice_commander/modes/watcher.py
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, PatternMatchingEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _Handler(PatternMatchingEventHandler):
    def __init__(self, on_change: Callable[[Path], None], debounce_ms: int) -> None:
        super().__init__(patterns=["*.toml"], ignore_directories=True, case_sensitive=False)
        self._on_change = on_change
        self._debounce_s = debounce_ms / 1000.0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule(self, path: Path) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._on_change, args=(path,))
            self._timer.daemon = True
            self._timer.start()

    def on_modified(self, event: FileSystemEvent) -> None:
        self._schedule(Path(str(event.src_path)))

    def on_created(self, event: FileSystemEvent) -> None:
        self._schedule(Path(str(event.src_path)))

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._schedule(Path(str(event.src_path)))

    def on_moved(self, event: FileSystemEvent) -> None:
        self._schedule(Path(str(event.dest_path)))


class ModesWatcher:
    """Watches a modes directory for *.toml changes and fires *on_change*."""

    def __init__(self, modes_dir: Path, on_change: Callable[[Path], None], debounce_ms: int = 500) -> None:
        self._dir = Path(modes_dir)
        self._handler = _Handler(on_change, debounce_ms)
        self._observer: Observer | None = None  # type: ignore[valid-type]

    def start(self) -> None:
        if self._observer is not None or not self._dir.is_dir():
            return
        self._observer = Observer()
        self._observer.schedule(self._handler, str(self._dir), recursive=False)
        self._observer.start()
        logger.info("modes hot-reload: watching %s", self._dir)

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=2.0)
        self._observer = None
```

> Match `ConfigWatcher`'s debounce mechanism exactly if it differs (it may use a watchdog-native debounce rather than `threading.Timer`). Prefer copying its proven approach over the Timer above if they diverge.

- [ ] **Step 4: Wire into the daemon factory**

In `build_streaming_daemon`, where the mode registry/session were created (Task 9 Step 7), after building them:

```python
        from .modes.watcher import ModesWatcher

        def _on_modes_changed(_path: Path) -> None:
            try:
                mode_registry.reload()
                if event_bus is not None:
                    event_bus.publish("modes_reloaded", {"count": len(mode_registry.all())})
                logger.info("modes hot-reload: %d mode(s)", len(mode_registry.all()))
            except Exception:
                logger.exception("modes hot-reload failed")

        daemon_modes_watcher = ModesWatcher(modes_dir, _on_modes_changed)
        daemon_modes_watcher.start()
```

Attach the watcher to the daemon so it is stopped on shutdown (mirror how `daemon._config_watcher` is stored ~line 637): `daemon._modes_watcher = daemon_modes_watcher` after `daemon` is constructed, and stop it wherever `_config_watcher.stop()` is called.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_modes_watcher.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/modes/watcher.py src/voice_commander/daemon.py tests/unit/test_modes_watcher.py
git commit -m "feat(modes): hot-reload watcher for modes/*.toml"
```

---

### Task 13: Visual E2E — mode badge

**Files:**
- Create: `scripts/mode_visual_e2e.py`

Copy `scripts/picker_visual_e2e.py` Phase A wholesale and adapt: emit `warmup_done` → `session_started` → `mode.enter {"name":"video","badge":"🎬 VIDEO"}`; find the sprite window; PrintWindow capture; assert the badge's green pixels `(120, 220, 140)` are present near the **top** of the frame; then emit `mode.exit` and assert the green pixels are gone.

- [ ] **Step 1: Read the template**

Read `scripts/picker_visual_e2e.py` in full. Reuse `_free_port`, `_SSEHandler`, `_start_sse_server`, `_emit`, `_write_temp_config`, `_capture_window`, and the sprite-subprocess launch from Phase A verbatim.

- [ ] **Step 2: Write the harness**

```python
# scripts/mode_visual_e2e.py
"""Visual E2E: assert the sprite renders a persistent mode badge on mode.enter
and clears it on mode.exit.

Reuses the SSE-server + sprite-subprocess + PrintWindow scaffolding from
picker_visual_e2e.py.

Usage: python scripts/mode_visual_e2e.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Import the reusable scaffolding from the picker E2E.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from picker_visual_e2e import (  # type: ignore
    _capture_window,
    _emit,
    _start_sse_server,
    _write_temp_config,
)

_BADGE_RGB = (120, 220, 140)


def _has_badge_pixels(png_path: Path, *, top_fraction: float = 0.35) -> bool:
    from PIL import Image

    img = Image.open(png_path).convert("RGB")
    w, h = img.size
    px = img.load()
    band = int(h * top_fraction)
    for y in range(0, band):
        for x in range(0, w):
            r, g, b = px[x, y]
            if abs(r - _BADGE_RGB[0]) < 40 and abs(g - _BADGE_RGB[1]) < 40 and abs(b - _BADGE_RGB[2]) < 40:
                return True
    return False


def main() -> int:
    # 1) Launch SSE server + sprite subprocess (see picker_visual_e2e.phase_a
    #    for the exact subprocess.Popen call and the _find_*_hwnd helper; copy it).
    #    Pseudocode for the assertions specific to this harness:
    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    srv, port = _start_sse_server()
    cfg_path = _write_temp_config(port)
    # ... spawn sprite subprocess pointing at cfg_path (copy from picker_visual_e2e) ...
    # ... obtain the sprite window hwnd (copy _find_modal_hwnd / equivalent) ...
    hwnd = ...  # filled in by copying the picker harness window-discovery code

    _emit(srv, "warmup_done", {})
    _emit(srv, "session_started", {})
    _emit(srv, "mode.enter", {"name": "video", "badge": "🎬 VIDEO"})
    time.sleep(1.0)
    on_png = out_dir / "mode_badge_on.png"
    _capture_window(hwnd, on_png)
    assert _has_badge_pixels(on_png), "mode badge pixels NOT found after mode.enter"

    _emit(srv, "mode.exit", {"name": "video", "reason": "end_phrase"})
    time.sleep(1.0)
    off_png = out_dir / "mode_badge_off.png"
    _capture_window(hwnd, off_png)
    assert not _has_badge_pixels(off_png), "mode badge pixels STILL present after mode.exit"

    print("PASS: mode badge shown on enter, cleared on exit", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> The `...` placeholders are the subprocess-launch and window-discovery lines you copy verbatim from `picker_visual_e2e.py` Phase A — they are identical mechanics (spawn the sprite with a temp config, poll for the window hwnd). Fill them in from that file; do not invent new mechanics.

- [ ] **Step 3: Run the harness (human-validated gate)**

Run: `python scripts/mode_visual_e2e.py`
Expected: prints `PASS: mode badge shown on enter, cleared on exit`, writes `outputs/mode_badge_on.png` (badge visible top-centre) and `outputs/mode_badge_off.png` (no badge). Per the project's visual-E2E protocol, a human eyeballs both PNGs.

- [ ] **Step 4: Commit**

```bash
git add scripts/mode_visual_e2e.py
git commit -m "test(modes): visual E2E for sprite mode badge"
```

---

### Task 14: Documentation (ADR, CLAUDE.md, decisions table, overview, index)

**Files:**
- Create: `docs/decisions/0100-named-command-modes.md`
- Create: `docs/modes.md`
- Modify: `docs/index.md` (add a link to `docs/modes.md`)
- Modify: `docs/agents/technical-decisions.md` (add a row)
- Modify: `CLAUDE.md` (extend the **Current state** section)

- [ ] **Step 1: Write the ADR**

Create `docs/decisions/0100-named-command-modes.md` covering: context (need contextual command scopes); decision (per-mode TOML catalogs; actions compiled to primitive Plans via a base-primitives router; `ModeRouter` phrase-map + primitive fallthrough; `ModeSession` sub-state; persistent top-centre sprite badge; enter/exit reuse start/stop chimes; must-end-first; Scroll Lock hard-exit; hot-reload via `ModesWatcher`); the refinement vs spec §2 (no per-mode `ToolRegistry`; dispatch reuses the global registry because actions are primitive calls); consequences; alternatives rejected (scoped-registry filter; phrase→Plan-only without primitives). Follow the format of an existing ADR (e.g. `docs/decisions/0099-*.md`).

- [ ] **Step 2: Write `docs/modes.md`**

Overview: what a mode is; the `modes/<name>.toml` format (with the `[mode]` + `[[command]]` example); the action grammar (explicit verb, base transcript); enter/exit/must-end-first; in-mode scope (mode commands + primitives, normal commands suppressed); feedback (badge + chimes); the shipped `video.toml`; how to author a new mode; hot-reload. Link to `docs/references/davinci-resolve-shortcuts.md`.

- [ ] **Step 3: Link from `docs/index.md`**

Add a bullet/row pointing to `docs/modes.md` ("Named command modes — scoped voice-switchable catalogs").

- [ ] **Step 4: Add the technical-decisions row**

Append a one-line row to `docs/agents/technical-decisions.md` matching the table's existing format, e.g.:
`| Named command modes | Per-mode TOML catalogs, ModeSession sub-state, primitive-Plan compilation, persistent sprite badge | ADR 0100 |`
(Match the actual column layout in that file.)

- [ ] **Step 5: Extend CLAUDE.md Current state**

Add a paragraph to the **Current state** section of `CLAUDE.md` describing named modes (trigger enters, `<mode> end` exits, sub-state of session, mode commands + primitives only, normal commands suppressed, per-mode `modes/*.toml`, actions compiled to primitive plans, persistent badge, `modes/video.toml` DaVinci pack, hot-reload, ADR 0100).

- [ ] **Step 6: Commit**

```bash
git add docs/decisions/0100-named-command-modes.md docs/modes.md docs/index.md docs/agents/technical-decisions.md CLAUDE.md
git commit -m "docs(modes): ADR 0100 + overview + current-state (named command modes)"
```

---

### Task 15: Full-suite green + final lint

- [ ] **Step 1: Run the whole suite**

Run: `pytest tests -ra --strict-markers`
Expected: all pass (existing + new). Investigate and fix any regression (especially feedback Protocol implementers and daemon construction).

- [ ] **Step 2: Lint + types**

Run: `ruff check src tests --line-length 100 && ruff format --check src tests && mypy src tests --python-version 3.11`
Expected: clean.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore(modes): suite green + lint clean"
```

---

## Self-review

**Spec coverage:**
- §1 catalog format → Tasks 2, 4, 11. Explicit-verb actions → Task 3 (compiler). ✔
- §2 routing/precedence/state machine → Tasks 6 (router), 7 (session), 9 (daemon slot + trigger intercept + Scroll-Lock reset). Must-end-first → Task 7 (`try_enter` returns None while active; trigger intercept only runs in normal mode). ✔
- §3 feedback → Task 8 (chimes), Task 10 (persistent badge). SSE `mode.enter`/`mode.exit` → Task 7 (publish) + auto-serialized by the existing `/events` endpoint (no web change needed). ✔
- §4 config/discovery → Task 1 (config), Task 5 (registry scan), Task 12 (hot-reload), loud-but-isolated load errors → Tasks 4/5. ✔
- §5 DaVinci starter → Task 11 (with the confirmed 1/2/3 literal + space play/pause). ✔
- §6 testing/E2E → unit tests in Tasks 1-8,10,11; wiring in Task 9; visual E2E in Task 13; full suite in Task 15. ✔
- §7 docs → Task 14 + Task 11 (vendored reference). ✔

**Placeholder scan:** The only intentional `...` are in Task 13, where the harness explicitly copies named functions from `picker_visual_e2e.py` (the mechanics already exist and are cited); every other code step is complete and runnable.

**Type consistency:** `ModeDefinition`/`ModeCommand`/`ModeOutcome` (Task 2) are used identically in Tasks 4-7,10. `ModeRegistry.by_trigger/all/load_all/reload` (Task 5) match call sites in Tasks 7,9,12. `ModeSession.try_enter/handle_utterance/exit/reset` + `.active/.active_mode` (Task 7) match daemon call sites (Task 9). `set_mode_badge`/`active_mode_badge` (Task 10) consistent across state machine, helper, window. Event payloads `{"name","badge"}` / `{"name","reason"}` consistent between Task 7 (publish) and Task 10 (consume) and Task 13 (emit).

## Risks / notes for the implementer

- **Daemon insertion point precision (Task 9 Step 5):** verify `run`, `_publish_miss`, `self._registry` are in scope at the chosen line; the citations are from a snapshot and line numbers may drift. Anchor on the picker block's end and the word-count gate's start, not raw line numbers.
- **`build_streaming_daemon` may be too heavy to instantiate in a unit test** — Task 9 Step 1 provides the helper-extraction fallback.
- **`ConfigWatcher` debounce** — copy its proven mechanism if it differs from the `threading.Timer` shown in Task 12.
- **Synthetic-intercept guard** keeps bare `dictate`/`focus` from doing surprising things inside a mode; documented as a deliberate limitation (chaining mode commands and in-mode pickers are out of scope for v1).
