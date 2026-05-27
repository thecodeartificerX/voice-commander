# Dictation Backend Selector (internal vs external / Wispr Flow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `[dictation] backend` config selector with two values — `internal` (today's record→WS→paste pipeline, default) and `external` (a passthrough sub-state that plays the same chime + DICTATING animation, mutes VC's own routing, and records/transcribes/pastes nothing so an external tool like Wispr Flow bound to the same Right Ctrl does the real dictation).

**Architecture:** No backend abstraction class (YAGNI) — one config flag (`DictationConfig.backend`) plus a single daemon boolean (`_passthrough_active`) that gates a handful of branches in `on_dictation_toggle`, the `__dictation.start` intercept, `_process_utterance`, and `_close_voice_session`. External mode reuses the existing dictation sub-state machinery wholesale: it publishes the same `dictation.start` / `dictation.end` events the sprite already consumes, deliberately never publishing `dictation.processing` (no server round-trip). Every new branch is inert under the default, so internal behaviour is byte-for-byte unchanged.

**Tech Stack:** Python 3.11+, frozen dataclasses (`config.py`), `tomllib`, `pytest` + `unittest.mock`, the in-process `EventBus`, and the Windows visual-E2E harness pattern (`subprocess` sprite + SSE server + `PrintWindow` capture).

**ADR number:** This feature is **ADR 0102** (the next free number — the highest existing ADR is `0101`). The design spec said "0103"; that was a miscount. Use **0102** everywhere.

---

## Spec reference

Design spec: [`docs/superpowers/specs/2026-05-25-dictation-backend-selector-design.md`](../specs/2026-05-25-dictation-backend-selector-design.md). Read it before starting — this plan implements §§1–9 of that spec.

## Key facts established by code review (do not re-derive)

- `DictationConfig` is `@dataclass(frozen=True)` in `src/voice_commander/config.py` (lines 26–43). Fields today: `ws_url`, `end_word`, `cancel_word`, `idle_timeout_seconds`, `max_dictation_s`. **No `Literal` import; no `backend` field.**
- `Config.load()` (lines 197–253) parses each section via the generic `_section()` helper, which **raises** `ValueError`/`TypeError` on unknown keys or type mismatches — it has **no warn-and-fallback for values**. Existing warn-and-fallback is done by mutating the raw dict *before* `_section()` (see the `[llm]` and `mute_key` warnings at lines 207–229). We follow that pattern. We keep `backend` as a plain `str` (not `Literal`) precisely because `_section` would otherwise raise on a bad value — we want degrade, not block.
- Daemon state lives in `StreamingDaemon.__init__` (`src/voice_commander/daemon.py`): `self._session_opened_by_dictation: bool = False` is line 253. The live config snapshot `self._cfg` is set **after construction** by `build_streaming_daemon` (`daemon._cfg = cfg`, line 1698) — we mirror that pattern for the cached backend value.
- `dictation.start` is published with payload `{}` (from `DictationSession.start()`, `dictation/session.py:201`). `dictation.end` is published with `{"reason": "done"}` (`session.py:293`). `dictation.processing` is `{}` (`session.py:273`). External mode publishes `dictation.start`/`dictation.end` **directly from the daemon** (there is no `DictationSession` involved) so the payloads must match exactly.
- The daemon's publish wrapper is `self._publish(event_type: str, data: dict | None = None)` (`daemon.py:303`).
- Sprite badges (no sprite code changes needed): DICTATING is amber text `● DICTATING`, color `(245, 194, 66, 255)`, drawn bottom-centre (`y=2`) (`window.py:311–328`). PROCESSING is light-blue `⏳ PROCESSING…`, color `(100, 200, 255, 255)`, bottom-centre (`window.py:330–345`). The DICTATING badge is driven by `window.set_dictating(sm.dictating)` from `voice_sprite/__main__.py:374`; `sm.dictating` is set by the state machine on `dictation.start`.
- Transcription in the hot path is `self._transcriber.transcribe(utterance)` (`daemon.py:701`). `_write_utterance_async(utterance)` is `daemon.py:648` (the first executable line of `_process_utterance` after the docstring).
- Unit-test construction idiom: copy `_make_daemon` from `tests/unit/test_daemon_session_helpers.py` (lines 34–63). Event-assertion idiom: `q = bus.subscribe()` → run action → drain `q.get_nowait().type` into a list → assert membership/order.
- Visual-E2E pattern: copy structure + snippets from `scripts/sprite_dim_e2e.py` (subprocess sprite with `src/` injected onto `PYTHONPATH` for worktree-safety, SSE server, `PrintWindow` capture with `PW_RENDERFULLCONTENT = 0x2`, brightness metric).

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/voice_commander/config.py` | `DictationConfig.backend` field + `Config.load()` validation | Modify |
| `src/voice_commander/daemon.py` | `_passthrough_active` + `_dictation_backend` state; `_enter_passthrough`/`_exit_passthrough` helpers; `on_dictation_toggle` external branch; `__dictation.start` external branch; `_process_utterance` mute early-return; `_close_voice_session` passthrough reset; `build_streaming_daemon` backend wiring | Modify |
| `config.toml.example` | document the `backend` key | Modify |
| `tests/unit/test_config.py` | config parse/validate coverage | Modify |
| `tests/unit/test_dictation_backend.py` | toggle / intercept / mute / reset / regression coverage | Create |
| `tests/integration/test_daemon_factory.py` | `build_streaming_daemon` wires `backend` onto the daemon | Modify |
| `scripts/dictation_external_e2e.py` | visual-E2E harness (DICTATING + bright, no PROCESSING, restore) | Create |
| `docs/decisions/0102-dictation-backend-selector.md` | ADR | Create |
| `docs/agents/technical-decisions.md` | summary row → ADR 0102 | Modify |
| `CLAUDE.md` | extend dictation paragraph in *Current state* | Modify |
| `docs/dictation-streaming.md` | external/passthrough backend subsection + config row | Modify |

---

## Task 1: Config — `backend` field + validation

**Files:**
- Modify: `src/voice_commander/config.py:43` (add field) and `src/voice_commander/config.py:231` (add validation before `return cls(`, and change the `dictation=` line at 250)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_config.py` (near the other dictation config tests, e.g. after the `cancel_word` test around line 285):

```python
def test_dictation_backend_defaults_to_internal() -> None:
    from voice_commander.config import DictationConfig

    assert DictationConfig().backend == "internal"


def test_dictation_backend_parses_external(tmp_path) -> None:
    from voice_commander.config import Config

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[dictation]\nbackend = "external"\n', encoding="utf-8")
    cfg = Config.load(cfg_file)
    assert cfg.dictation.backend == "external"


def test_dictation_backend_unknown_value_warns_and_falls_back(tmp_path, caplog) -> None:
    import logging

    from voice_commander.config import Config

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[dictation]\nbackend = "wispr"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="voice_commander.config"):
        cfg = Config.load(cfg_file)
    assert cfg.dictation.backend == "internal"
    assert any("backend" in r.message and "internal" in r.message for r in caplog.records)


def test_dictation_backend_empty_string_falls_back(tmp_path) -> None:
    from voice_commander.config import Config

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[dictation]\nbackend = ""\n', encoding="utf-8")
    cfg = Config.load(cfg_file)
    assert cfg.dictation.backend == "internal"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_config.py -k dictation_backend -v`
Expected: FAIL — `DictationConfig` has no attribute `backend` (and/or `_section` raises `ValueError: Unknown config key 'backend'`).

- [ ] **Step 3: Add the `backend` field to `DictationConfig`**

In `src/voice_commander/config.py`, after line 43 (`    max_dictation_s: int = 300`), add:

```python
    # Dictation backend selector (ADR 0102).  "internal" = our record->WS->paste
    # pipeline (default).  "external" = passthrough: VC mutes + animates only,
    # an external tool (e.g. Wispr Flow, bound to the same Right Ctrl) does the
    # real dictation.  Validated in Config.load(); an unknown/empty value
    # degrades to "internal" with a WARNING.  Kept as a plain ``str`` (not
    # ``Literal``) because the generic _section parser raises on type mismatch
    # and we want degrade-not-block.
    backend: str = "internal"
```

- [ ] **Step 4: Add validation in `Config.load()`**

In `src/voice_commander/config.py`, immediately before line 231 (`        return cls(`), insert:

```python
        # [dictation] backend selector (ADR 0102) — degrade, never block.  An
        # unknown/empty value falls back to "internal" with a WARNING, matching
        # the repo's "bad config degrades" convention (same as [llm]/mute_key
        # handling above).  Copy the table so we never mutate the parsed raw.
        dictation_raw = dict(raw.get("dictation", {}))
        backend = dictation_raw.get("backend", "internal")
        if backend not in ("internal", "external"):
            logger.warning(
                "[dictation] backend=%r is not 'internal' or 'external'; "
                "falling back to 'internal'",
                backend,
            )
            dictation_raw["backend"] = "internal"

```

Then change line 250 from:

```python
            dictation=_section(DictationConfig, raw.get("dictation", {})),
```

to:

```python
            dictation=_section(DictationConfig, dictation_raw),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_config.py -k dictation_backend -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Run the full config test module (regression)**

Run: `python -m pytest tests/unit/test_config.py -q`
Expected: PASS (all existing config tests still green).

- [ ] **Step 7: Commit**

```bash
git add src/voice_commander/config.py tests/unit/test_config.py
git commit -m "feat(dictation): add [dictation] backend config selector (ADR 0102)"
```

---

## Task 2: Daemon state + passthrough helpers + `on_dictation_toggle` external branch

**Files:**
- Modify: `src/voice_commander/daemon.py` — `__init__` (after line 253), new helpers (before `def on_dictation_toggle`, line 506), `on_dictation_toggle` external branch (before line 518)
- Test: `tests/unit/test_dictation_backend.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dictation_backend.py`:

```python
"""Unit tests for ADR 0102 — dictation backend selector (internal vs external).

The external backend is a passthrough sub-state: VC plays the same chime +
DICTATING animation, mutes its own routing, and records/transcribes/pastes
nothing.  Right Ctrl enters/exits; an external tool (Wispr Flow) does the real
dictation.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from voice_commander.daemon import StreamingDaemon
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.plan import Plan, ToolCall
from voice_commander.verb_router import VerbRouter


def _make_daemon(
    *,
    event_bus: EventBus | None = None,
    backend: str = "external",
) -> tuple[StreamingDaemon, CapturingFeedbackSink, MagicMock]:
    """Return (daemon, feedback, recorder) with all hardware mocked.

    Mirrors tests/unit/test_daemon_session_helpers.py::_make_daemon.
    """
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_open = False
    transcriber = MagicMock()
    dispatcher = MagicMock()
    verb_router = MagicMock(spec=VerbRouter)
    verb_router.route.return_value = Plan(
        steps=(ToolCall(name="press", kwargs={"combo": "ctrl+c"}),),
        raw_response={"router": "verb"},
    )
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=MagicMock(),
        output_dir="outputs",
        event_bus=event_bus,
        dictation_session=None,
    )
    daemon._transcriber_ready.set()
    daemon._dictation_backend = backend
    return daemon, feedback, recorder


def _drain(q) -> list[str]:
    out = []
    while not q.empty():
        out.append(q.get_nowait().type)
    return out


def test_external_enter_from_idle_opens_owned_session_and_sets_flag() -> None:
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, recorder = _make_daemon(event_bus=bus)
    assert daemon._session_active is False

    daemon.on_dictation_toggle()

    assert daemon._passthrough_active is True
    assert daemon._session_active is True
    assert daemon._session_opened_by_dictation is True
    recorder.open_session.assert_called_once()
    events = _drain(q)
    assert "session_started" in events
    assert "dictation.start" in events


def test_external_exit_from_idle_closes_owned_session_and_clears_flag() -> None:
    bus = EventBus()
    daemon, _, recorder = _make_daemon(event_bus=bus)
    daemon.on_dictation_toggle()  # enter (owned session)
    q = bus.subscribe()

    daemon.on_dictation_toggle()  # exit

    assert daemon._passthrough_active is False
    assert daemon._session_active is False
    assert daemon._session_opened_by_dictation is False
    recorder.close_session.assert_called_once()
    events = _drain(q)
    assert "dictation.end" in events
    assert "session_stopped" in events


def test_external_enter_with_session_open_does_not_open_session() -> None:
    bus = EventBus()
    daemon, _, recorder = _make_daemon(event_bus=bus)
    daemon._session_active = True  # simulate Scroll Lock session already open
    daemon._session_opened_by_dictation = False
    q = bus.subscribe()

    daemon.on_dictation_toggle()  # enter passthrough as a sub-state

    assert daemon._passthrough_active is True
    assert daemon._session_active is True
    recorder.open_session.assert_not_called()
    events = _drain(q)
    assert "dictation.start" in events
    assert "session_started" not in events


def test_external_exit_with_scroll_lock_session_keeps_session_open() -> None:
    bus = EventBus()
    daemon, _, recorder = _make_daemon(event_bus=bus)
    daemon._session_active = True
    daemon._session_opened_by_dictation = False
    daemon.on_dictation_toggle()  # enter
    q = bus.subscribe()

    daemon.on_dictation_toggle()  # exit

    assert daemon._passthrough_active is False
    assert daemon._session_active is True
    recorder.close_session.assert_not_called()
    events = _drain(q)
    assert "dictation.end" in events
    assert "session_stopped" not in events


def test_external_enter_aborts_when_recorder_open_fails() -> None:
    daemon, _, recorder = _make_daemon()
    recorder.open_session.side_effect = RuntimeError("device busy")

    daemon.on_dictation_toggle()  # enter attempt from idle

    assert daemon._passthrough_active is False
    assert daemon._session_active is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_dictation_backend.py -v`
Expected: FAIL — `AttributeError: 'StreamingDaemon' object has no attribute '_dictation_backend'` (set in the helper) and the toggle has no external branch.

- [ ] **Step 3: Add the state fields to `__init__`**

In `src/voice_commander/daemon.py`, find line 253:

```python
        self._session_opened_by_dictation: bool = False
```

Replace it with:

```python
        self._session_opened_by_dictation: bool = False
        # External dictation backend (ADR 0102).  When True, VC is muted and an
        # external tool (e.g. Wispr Flow) drives dictation; this flag gates every
        # new passthrough branch.  Only ever True when _dictation_backend ==
        # "external", and mutually exclusive with an active DictationSession.
        self._passthrough_active: bool = False
        # Cached at startup from cfg.dictation.backend by build_streaming_daemon;
        # intentionally NOT updated on config hot-reload (ADR 0102 — a backend
        # change needs a daemon restart).
        self._dictation_backend: str = "internal"
```

- [ ] **Step 4: Add the passthrough helpers**

In `src/voice_commander/daemon.py`, find line 506:

```python
    def on_dictation_toggle(self) -> None:
```

Insert the two helpers immediately *before* it:

```python
    def _enter_passthrough(self) -> None:
        """External-backend dictation ENTER (ADR 0102): mute + animate, no capture.

        Sets the mute flag and publishes ``dictation.start`` (same payload as
        the internal path) so the sprite shows the DICTATING badge + bright cat.
        Caller is responsible for opening a voice session first if none is open.
        """
        self._passthrough_active = True
        self._publish("dictation.start", {})
        logger.info("dictation: entered external passthrough (mic muted)")

    def _exit_passthrough(self) -> None:
        """External-backend dictation EXIT (ADR 0102): unmute + restore pose.

        Clears the mute flag and publishes ``dictation.end {reason: "done"}``
        (same payload as the internal finish path) so the sprite restores its
        pre-dictation pose per ADR 0099.  Caller closes any owned session after.
        """
        self._passthrough_active = False
        self._publish("dictation.end", {"reason": "done"})
        logger.info("dictation: exited external passthrough (mic unmuted)")

    def on_dictation_toggle(self) -> None:
```

- [ ] **Step 5: Add the external branch at the top of `on_dictation_toggle`**

In `src/voice_commander/daemon.py`, find (lines 516–518):

```python
        Behaviour is unchanged from ADR 0086.
        """
        if not self._session_active:
```

Replace with:

```python
        Behaviour is unchanged from ADR 0086.

        External backend (ADR 0102): a passthrough toggle — enter/exit mute +
        animation only, no DictationSession, no capture.  Right Ctrl is the only
        exit (VC transcribes nothing in this mode, so spoken "done"/cancel can't
        be heard).
        """
        if self._dictation_backend == "external":
            if not self._passthrough_active:
                # ENTER
                if not self._session_active:
                    # Case 1: no session open — open an owned one (so brightness,
                    # _session_active, and Scroll Lock toggling stay consistent).
                    if not self._open_voice_session():
                        return  # recorder failed; error already surfaced
                    self._session_opened_by_dictation = True
                # Case 2: a Scroll Lock session is already open — enter as a
                # sub-state, leaving the session untouched.
                self._enter_passthrough()
            else:
                # EXIT — publish dictation.end BEFORE closing so the sprite
                # restores pose (ADR 0099) before session_stopped dims it.
                self._exit_passthrough()
                if self._session_opened_by_dictation:
                    self._close_voice_session()  # also clears _passthrough_active
                # Case 2: Scroll Lock session stays open; no extra chime (matches
                # internal mode entering/leaving dictation inside an open session).
            return
        if not self._session_active:
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_dictation_backend.py -v`
Expected: PASS (5 passed).

- [ ] **Step 7: Commit**

```bash
git add src/voice_commander/daemon.py tests/unit/test_dictation_backend.py
git commit -m "feat(dictation): external-backend passthrough toggle (ADR 0102)"
```

---

## Task 3: Wire `backend` from config onto the daemon

**Files:**
- Modify: `src/voice_commander/daemon.py` — `build_streaming_daemon`, after `daemon._cfg = cfg` (line 1698)
- Test: `tests/integration/test_daemon_factory.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_daemon_factory.py` (it already imports `DictationConfig`, `Config`, `replace`, and the `base_cfg` fixture — reuse them):

```python
def test_build_streaming_daemon_wires_external_backend(base_cfg) -> None:
    from dataclasses import replace

    from voice_commander.config import DictationConfig
    from voice_commander.daemon import build_streaming_daemon

    cfg = replace(base_cfg, dictation=DictationConfig(backend="external"))
    daemon = build_streaming_daemon(cfg)
    assert daemon._dictation_backend == "external"


def test_build_streaming_daemon_defaults_backend_internal(base_cfg) -> None:
    from voice_commander.daemon import build_streaming_daemon

    daemon = build_streaming_daemon(base_cfg)
    assert daemon._dictation_backend == "internal"
```

> If `build_streaming_daemon(cfg)` in this test file is normally called with extra arguments (check an existing factory test in the same file and match its call signature), use that same call form — the assertion on `daemon._dictation_backend` is what matters.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/integration/test_daemon_factory.py -k backend -v`
Expected: FAIL — `daemon._dictation_backend` is the default `"internal"` for the external case (config not yet wired).

- [ ] **Step 3: Wire the backend after construction**

In `src/voice_commander/daemon.py`, find line 1698:

```python
    daemon._cfg = cfg
```

Replace with:

```python
    daemon._cfg = cfg
    # Cache the dictation backend at startup (ADR 0102).  Read once here rather
    # than on every toggle; a backend change requires a daemon restart.
    daemon._dictation_backend = cfg.dictation.backend
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/integration/test_daemon_factory.py -k backend -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/daemon.py tests/integration/test_daemon_factory.py
git commit -m "feat(dictation): wire backend selector from config onto daemon (ADR 0102)"
```

---

## Task 4: `__dictation.start` intercept — external branch

**Files:**
- Modify: `src/voice_commander/daemon.py` — `_process_utterance`, the `__dictation.start` intercept (lines 891–893)
- Test: `tests/unit/test_dictation_backend.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_dictation_backend.py`. This test exercises the spoken-`"dictate"` path while a Scroll Lock session is open, asserting external mode enters passthrough instead of starting a `DictationSession`. We give the daemon a `MagicMock` dictation session so we can assert `.start` is NOT called.

```python
import numpy as np

from voice_commander.transcriber import TranscriptionResult


def _make_daemon_with_session(
    *, backend: str, event_bus: EventBus
) -> tuple[StreamingDaemon, MagicMock]:
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_open = True
    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(text="dictate", confidence=1.0)
    dispatcher = MagicMock()
    verb_router = MagicMock(spec=VerbRouter)
    verb_router.route.return_value = Plan(
        steps=(ToolCall(name="__dictation.start", kwargs={}),),
        raw_response={"router": "intercept"},
    )
    dictation_session = MagicMock()
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=MagicMock(),
        output_dir="outputs",
        event_bus=event_bus,
        dictation_session=dictation_session,
    )
    daemon._transcriber_ready.set()
    daemon._dictation_backend = backend
    daemon._session_active = True
    return daemon, dictation_session


def test_dictate_intercept_external_enters_passthrough_not_session() -> None:
    bus = EventBus()
    q = bus.subscribe()
    daemon, dictation_session = _make_daemon_with_session(backend="external", event_bus=bus)

    daemon._process_utterance(np.zeros(16000, dtype=np.float32))

    dictation_session.start.assert_not_called()
    assert daemon._passthrough_active is True
    assert "dictation.start" in _drain(q)


def test_dictate_intercept_internal_starts_session() -> None:
    bus = EventBus()
    daemon, dictation_session = _make_daemon_with_session(backend="internal", event_bus=bus)

    daemon._process_utterance(np.zeros(16000, dtype=np.float32))

    dictation_session.start.assert_called_once()
    assert daemon._passthrough_active is False
```

> If `TranscriptionResult`'s constructor differs (check `tests/unit/test_daemon_session_helpers.py` imports — it imports `TranscriptionResult` from `voice_commander.transcriber`), match the real field names. The integration helper uses a `_Transcription(text)` stub; either works as long as `transcribe()` returns an object with a `.text` attribute equal to `"dictate"`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_dictation_backend.py -k intercept -v`
Expected: FAIL — `test_dictate_intercept_external...` fails because the intercept still calls `dictation_session.start()` regardless of backend.

- [ ] **Step 3: Add the external branch to the intercept**

In `src/voice_commander/daemon.py`, find (lines 891–893):

```python
            if len(plan.steps) == 1 and plan.steps[0].name == "__dictation.start":
                if self._dictation_session is not None:
                    self._dictation_session.start(self._load_vocab())
```

Replace with:

```python
            if len(plan.steps) == 1 and plan.steps[0].name == "__dictation.start":
                if self._dictation_backend == "external":
                    # ADR 0102: spoken "dictate" in external mode enters
                    # passthrough (mute + animate), not a DictationSession.
                    # Reachable only with a Scroll Lock session already open, so
                    # no session is opened here (case 2).
                    self._enter_passthrough()
                elif self._dictation_session is not None:
                    self._dictation_session.start(self._load_vocab())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_dictation_backend.py -k intercept -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/daemon.py tests/unit/test_dictation_backend.py
git commit -m "feat(dictation): spoken-dictate intercept enters passthrough in external mode (ADR 0102)"
```

---

## Task 5: Mute — `_process_utterance` early return

**Files:**
- Modify: `src/voice_commander/daemon.py` — top of `_process_utterance`, before line 648 (`self._write_utterance_async(utterance)`)
- Test: `tests/unit/test_dictation_backend.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_dictation_backend.py`:

```python
def test_passthrough_active_drops_utterance_no_transcribe_no_route() -> None:
    daemon, _, _ = _make_daemon(backend="external")
    daemon._session_active = True
    daemon._passthrough_active = True

    daemon._process_utterance(np.zeros(16000, dtype=np.float32))

    daemon._transcriber.transcribe.assert_not_called()
    daemon._verb_router.route.assert_not_called()


def test_passthrough_inactive_processes_normally() -> None:
    daemon, _, _ = _make_daemon(backend="external")
    daemon._session_active = True
    daemon._passthrough_active = False
    daemon._transcriber.transcribe.return_value = TranscriptionResult(
        text="copy", confidence=1.0
    )

    daemon._process_utterance(np.zeros(16000, dtype=np.float32))

    daemon._transcriber.transcribe.assert_called_once()
```

> The `_make_daemon` helper from Task 2 uses a `MagicMock()` transcriber, so `transcribe` is a mock with `.assert_not_called()`. Add `import numpy as np` / `from voice_commander.transcriber import TranscriptionResult` at the top if Task 4 hasn't already.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_dictation_backend.py -k passthrough_active -v`
Expected: FAIL — `transcribe` IS called because there is no mute gate yet.

- [ ] **Step 3: Add the mute early-return**

In `src/voice_commander/daemon.py`, find (lines 647–648):

```python
        # Async write for post-mortem debugging
        self._write_utterance_async(utterance)
```

Replace with:

```python
        # External dictation backend (ADR 0102): while passthrough is active VC
        # is muted — an external tool (e.g. Wispr Flow) does the dictation.  Drop
        # the utterance before the debug-WAV write and before transcribe(), so
        # there is no Whisper CPU, no transcript event, no routing, no fire.
        if self._passthrough_active:
            return

        # Async write for post-mortem debugging
        self._write_utterance_async(utterance)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_dictation_backend.py -k passthrough -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/daemon.py tests/unit/test_dictation_backend.py
git commit -m "feat(dictation): mute the pipeline while external passthrough is active (ADR 0102)"
```

---

## Task 6: `_close_voice_session` clears `_passthrough_active`

**Files:**
- Modify: `src/voice_commander/daemon.py` — `_close_voice_session`, both reset sites (lines 432 and 441)
- Test: `tests/unit/test_dictation_backend.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_dictation_backend.py`:

```python
def test_close_voice_session_clears_passthrough_flag() -> None:
    daemon, _, _ = _make_daemon(backend="external")
    daemon._session_active = True
    daemon._passthrough_active = True

    daemon._close_voice_session()

    assert daemon._passthrough_active is False


def test_scroll_lock_close_clears_passthrough_flag() -> None:
    # on_scroll_lock -> _close_voice_session must tear down a dangling mute.
    daemon, _, _ = _make_daemon(backend="external")
    daemon._session_active = True
    daemon._passthrough_active = True

    daemon.on_scroll_lock()  # session active -> closes it

    assert daemon._passthrough_active is False
    assert daemon._session_active is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_dictation_backend.py -k clears_passthrough -v`
Expected: FAIL — `_passthrough_active` stays `True` after close.

- [ ] **Step 3: Reset the flag in both `_close_voice_session` paths**

In `src/voice_commander/daemon.py`, find the recorder-None guard (lines 430–433):

```python
        if self._recorder is None:
            self._session_active = False
            self._session_opened_by_dictation = False
            return
```

Replace with:

```python
        if self._recorder is None:
            self._session_active = False
            self._session_opened_by_dictation = False
            self._passthrough_active = False
            return
```

Then find the main reset (lines 440–442):

```python
        self._session_active = False
        self._session_opened_by_dictation = False
        if cancel_dictation:
```

Replace with:

```python
        self._session_active = False
        self._session_opened_by_dictation = False
        # ADR 0102: tearing down a session must clear any external-passthrough
        # mute so a closed session never leaves a dangling muted pipeline.
        self._passthrough_active = False
        if cancel_dictation:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_dictation_backend.py -k passthrough_flag -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/daemon.py tests/unit/test_dictation_backend.py
git commit -m "fix(dictation): clear passthrough mute when a session is torn down (ADR 0102)"
```

---

## Task 7: Internal-mode regression guard

**Files:**
- Test: `tests/unit/test_dictation_backend.py`

- [ ] **Step 1: Write the test**

Add to `tests/unit/test_dictation_backend.py`:

```python
def test_internal_backend_never_sets_passthrough_on_toggle() -> None:
    # Default backend = internal: the external branch must be inert.  Toggle from
    # idle should follow the ADR 0090 owned-session path and leave the
    # passthrough flag False throughout.
    daemon, _, recorder = _make_daemon(backend="internal")
    # No dictation_session wired -> internal toggle opens session then returns
    # at the `if self._dictation_session is None: return` guard (ADR 0090).
    daemon.on_dictation_toggle()

    assert daemon._passthrough_active is False
    assert daemon._dictation_backend == "internal"
    recorder.open_session.assert_called_once()
```

- [ ] **Step 2: Run the test to verify it passes immediately**

Run: `python -m pytest tests/unit/test_dictation_backend.py -k internal_backend_never -v`
Expected: PASS (the implementation from Tasks 2–6 is already correct; this test pins the invariant against future regressions).

- [ ] **Step 3: Run the whole new module + the existing dictation/daemon suites (full regression)**

Run:
```bash
python -m pytest tests/unit/test_dictation_backend.py tests/unit/test_daemon_session_helpers.py tests/unit/test_config.py tests/integration/test_dictation_opens_session.py tests/integration/test_daemon_factory.py -q
```
Expected: PASS — all green. Internal dictation behaviour unchanged.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_dictation_backend.py
git commit -m "test(dictation): pin internal-mode passthrough-inert invariant (ADR 0102)"
```

---

## Task 8: Visual E2E harness

**Files:**
- Create: `scripts/dictation_external_e2e.py`

**Protocol:** Read [`docs/agents/visual-e2e-testing.md`](../../agents/visual-e2e-testing.md) before writing this — it is MANDATORY for any user-visible feature. This harness drives the sprite with the *exact event sequence external mode produces* (`session_started` + `dictation.start` … `dictation.end` + `session_stopped`, with **no** `dictation.processing`), captures the sprite window with `PrintWindow`, and asserts on the evidence: DICTATING badge present + cat bright on enter, **no** PROCESSING badge at any phase, badge cleared + cat dim on exit. It copies the structure of `scripts/sprite_dim_e2e.py` (subprocess sprite with `src/` injected, SSE server, `PrintWindow` capture).

- [ ] **Step 1: Write the harness**

Create `scripts/dictation_external_e2e.py`:

```python
"""Visual E2E for ADR 0102 — external dictation backend (passthrough).

External mode reuses the dictation sprite machinery but never publishes
``dictation.processing``.  This harness drives the sprite with the exact event
sequence the daemon emits in external mode and asserts on PrintWindow captures:

  enter  : session_started + unmuted + dictation.start
           -> amber "DICTATING" badge present, cat bright, NO light-blue
              "PROCESSING" badge.
  exit    : dictation.end {reason: done} + session_stopped  (owned-session case)
           -> badge cleared, cat dim, still NO processing badge.

Run:  python scripts/dictation_external_e2e.py
Exit: 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dictation_external_e2e")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _SSEHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence
        pass

    def do_GET(self) -> None:
        srv: Any = self.server
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return
        if self.path != "/events":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        srv.connected.set()
        ev_id = 0
        while not srv.shutdown_flag.is_set():
            try:
                ev = srv.queue.get(timeout=0.5)
            except Exception:
                try:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except Exception:
                    return
                continue
            if ev is None:
                return
            ev_id += 1
            payload = (
                f"id: {ev_id}\nevent: {ev['type']}\ndata: {json.dumps(ev['data'])}\n\n"
            ).encode("utf-8")
            try:
                self.wfile.write(payload)
                self.wfile.flush()
            except Exception:
                return


def _start_sse_server(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), _SSEHandler)
    srv.queue = Queue()  # type: ignore[attr-defined]
    srv.connected = Event()  # type: ignore[attr-defined]
    srv.shutdown_flag = Event()  # type: ignore[attr-defined]
    Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _emit(srv: ThreadingHTTPServer, type_: str, data: dict[str, Any]) -> None:
    srv.queue.put({"type": type_, "data": data})  # type: ignore[attr-defined]


def _write_temp_config(port: int) -> Path:
    cfg = OUT / "_dictation_external_e2e_config.toml"
    cfg.write_text(
        "\n".join(
            [
                "[sprite]",
                f'sse_url = "http://127.0.0.1:{port}/events"',
                "dim_brightness = 0.4",
                "follow_cursor = false",
                "[sprite.hud]",
                "enabled = false",
                # backend is a [dictation] (daemon) key; the sprite ignores it.
                # Recorded here only to document the scenario under test.
                "[dictation]",
                'backend = "external"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return cfg


def _find_window_by_pid(pid: int, timeout_s: float = 10.0) -> int | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    deadline = time.time() + timeout_s
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd: int, _lparam: int) -> bool:
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if (rect.right - rect.left) > 0 and (rect.bottom - rect.top) > 0:
                found.append(hwnd)
                return False
        return True

    while time.time() < deadline:
        found.clear()
        user32.EnumWindows(_cb, 0)
        if found:
            return found[0]
        time.sleep(0.25)
    return None


def _capture_window(hwnd: int, png_path: Path) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        import win32gui  # type: ignore
        import win32ui  # type: ignore
        from PIL import Image  # type: ignore

        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
            log.error("sprite hwnd has non-positive size: %dx%d", w, h)
            return False

        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)

        # PW_RENDERFULLCONTENT (0x2) — required for layered/DWM-composited windows.
        user32 = ctypes.windll.user32
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
        user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)

        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
        )
        img.save(png_path)

        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
        return True
    except Exception:
        log.exception("PrintWindow capture failed")
        return False


def _bright_metric(png_path: Path) -> float:
    """Top-1% luminance of the upper 85% (badge strip excluded). Cat brightness."""
    try:
        from PIL import Image
    except ImportError:
        return 0.0
    try:
        raw = Image.open(png_path).convert("RGB")
    except Exception:
        return 0.0
    w, h = raw.size
    img = raw.crop((0, 0, w, int(h * 0.85)))
    lums = sorted(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in img.getdata())
    if not lums:
        return 0.0
    top = lums[int(len(lums) * 0.99):]
    return sum(top) / max(1, len(top))


def _badge_pixels(png_path: Path, check) -> int:
    """Count pixels in the bottom 18% strip (where badges draw, y=2) matching *check*."""
    try:
        from PIL import Image
    except ImportError:
        return 0
    try:
        raw = Image.open(png_path).convert("RGB")
    except Exception:
        return 0
    w, h = raw.size
    strip = raw.crop((0, int(h * 0.82), w, h))
    return sum(1 for px in strip.getdata() if check(px))


def _is_amber(px) -> bool:  # DICTATING badge (245,194,66)
    r, g, b = px[:3]
    return 200 <= r <= 255 and 160 <= g <= 220 and 30 <= b <= 110


def _is_lightblue(px) -> bool:  # PROCESSING badge (100,200,255)
    r, g, b = px[:3]
    return 60 <= r <= 140 and 170 <= g <= 230 and 215 <= b <= 255


def main() -> int:
    port = _free_port()
    srv = _start_sse_server(port)
    cfg = _write_temp_config(port)

    env = dict(os.environ)
    src_dir = str(ROOT / "src")
    env["PYTHONPATH"] = (
        src_dir + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src_dir
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "voice_sprite", "--config", str(cfg)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        env=env,
    )
    try:
        if not srv.connected.wait(timeout=10):  # type: ignore[attr-defined]
            log.error("sprite never connected to SSE")
            return 1
        hwnd = _find_window_by_pid(proc.pid)
        if hwnd is None:
            log.error("could not locate sprite window")
            return 1

        # Settle to a known idle state.
        _emit(srv, "warmup_done", {})
        time.sleep(1.0)

        # ENTER (external passthrough): same events external mode publishes,
        # WITHOUT dictation.processing.
        _emit(srv, "session_started", {})
        _emit(srv, "unmuted", {})
        _emit(srv, "dictation.start", {})
        time.sleep(1.2)
        enter_png = OUT / "dictation_external_enter.png"
        _capture_window(hwnd, enter_png)
        enter_bright = _bright_metric(enter_png)
        enter_amber = _badge_pixels(enter_png, _is_amber)
        enter_blue = _badge_pixels(enter_png, _is_lightblue)
        log.info(
            "ENTER bright=%.1f amber=%d blue=%d", enter_bright, enter_amber, enter_blue
        )

        # EXIT (owned-session case): dictation.end then session_stopped.
        _emit(srv, "dictation.end", {"reason": "done"})
        _emit(srv, "session_stopped", {})
        time.sleep(1.2)
        exit_png = OUT / "dictation_external_exit.png"
        _capture_window(hwnd, exit_png)
        exit_bright = _bright_metric(exit_png)
        exit_amber = _badge_pixels(exit_png, _is_amber)
        exit_blue = _badge_pixels(exit_png, _is_lightblue)
        log.info(
            "EXIT bright=%.1f amber=%d blue=%d", exit_bright, exit_amber, exit_blue
        )

        ok = True
        if enter_amber < 3:
            log.error("FAIL: DICTATING (amber) badge not visible on enter")
            ok = False
        if enter_blue >= 3:
            log.error("FAIL: PROCESSING (light-blue) badge appeared in external mode")
            ok = False
        if exit_blue >= 3:
            log.error("FAIL: PROCESSING badge appeared at exit")
            ok = False
        if exit_amber >= 3:
            log.error("FAIL: DICTATING badge not cleared on exit")
            ok = False
        if exit_bright <= 0:
            log.error("FAIL: cat not visible at exit (metric<=0)")
            ok = False
        if enter_bright <= exit_bright / 0.6:
            log.error(
                "FAIL: enter not measurably brighter than exit (enter=%.1f exit=%.1f)",
                enter_bright,
                exit_bright,
            )
            ok = False

        if ok:
            log.info("PASS — external mode: DICTATING+bright on enter, no PROCESSING, dim on exit")
            return 0
        return 1
    finally:
        srv.shutdown_flag.set()  # type: ignore[attr-defined]
        srv.queue.put(None)  # type: ignore[attr-defined]
        srv.shutdown()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the harness**

Run: `python scripts/dictation_external_e2e.py`
Expected: exit code 0; log line `PASS — external mode: DICTATING+bright on enter, no PROCESSING, dim on exit`. Evidence PNGs at `outputs/dictation_external_enter.png` (amber badge, bright cat) and `outputs/dictation_external_exit.png` (no badge, dim cat).

> If `_find_window_by_pid` or the SSE config key (`sse_url`) does not match the sprite's actual CLI/config contract, open `scripts/sprite_dim_e2e.py` and copy its exact `_write_temp_config` body + window-finding approach (that harness is the proven reference for this repo's sprite). The assertion logic above stays the same.

- [ ] **Step 3: Commit**

> Do NOT commit the evidence PNGs at `outputs/dictation_external_*.png` — `outputs/` is gitignored and no other harness in this repo commits its evidence. The harness regenerates fresh PNGs on every run, which is sufficient.

```bash
git add scripts/dictation_external_e2e.py
git commit -m "test(dictation): visual E2E for external passthrough backend (ADR 0102)"
```

---

## Task 9: Documentation (ship in the same change)

**Files:**
- Create: `docs/decisions/0102-dictation-backend-selector.md`
- Modify: `docs/agents/technical-decisions.md` (append row at line 121, before the blank line preceding `## ~~LLM Router~~`)
- Modify: `CLAUDE.md` (extend the dictation paragraph in *Current state*, line 25)
- Modify: `config.toml.example` (add `backend` key inside `[dictation]`, lines 7–13)
- Modify: `docs/dictation-streaming.md` (new backend subsection + config row)

- [ ] **Step 1: Write ADR 0102**

Create `docs/decisions/0102-dictation-backend-selector.md`:

```markdown
# ADR 0102 — Dictation Backend Selector (internal vs external / Wispr Flow)

**Status:** Accepted
**Date:** 2026-05-26
**Extends:** [ADR 0086](0086-dictation-mode.md) (dictation lifecycle), [ADR 0090](0090-right-ctrl-opens-session.md) (owned session), [ADR 0096](0096-server-side-dictation.md) (internal transport), [ADR 0099](0099-dictation-end-restores-pose.md) (pose restore)

## Context

The internal dictation pipeline (record → WS → server Whisper+LLM → paste, ADR 0096) is still being hardened. The user already runs Wispr Flow bound to Right Ctrl for real dictation. We want to keep VC's command launcher (Scroll Lock + local Whisper) while opting out of the unfinished internal dictation pipeline — without losing the dictation UX (chime + bright cat + DICTATING badge) that muscle memory depends on.

Wispr Flow shares the Right Ctrl binding independently. VC's pynput listener uses no `suppress`, so a single Right Ctrl press reaches both apps: VC toggles its own state and Wispr Flow toggles its dictation. VC never synthesizes keys for, or otherwise drives, Wispr Flow.

## Decision

Add a `[dictation] backend` config selector with two values:

- **`internal`** (default) — today's behaviour, unchanged.
- **`external`** — a *passthrough* sub-state. Right Ctrl / spoken "dictate" plays the same start chime and shows the same DICTATING animation, but VC records nothing, transcribes nothing, sends nothing, pastes nothing, and mutes its own command routing while active. A second Right Ctrl press exits, restoring the prior state. An external tool (Wispr Flow) does the real dictation.

Implementation is a config flag plus branches — **no backend abstraction class** (YAGNI, two behaviours). A single daemon boolean `_passthrough_active` is the source of truth for "VC mic muted, external tool driving":

- `on_dictation_toggle` gains an external branch: enter opens an owned voice session if none is open (case 1) or enters as a sub-state of an open Scroll Lock session (case 2); exit publishes `dictation.end` then closes the owned session if VC opened it.
- The `__dictation.start` spoken intercept enters passthrough instead of starting a `DictationSession`.
- `_process_utterance` returns early when `_passthrough_active` — before the debug-WAV write and `transcribe()` — so there is no Whisper CPU, no transcript event, no routing, no fire.
- `_close_voice_session` clears `_passthrough_active` so a torn-down session (incl. Scroll Lock during passthrough) never leaves a dangling mute.

The backend is named `backend` (not `mode`) to avoid clashing with named command modes (ADR 0100). It is parsed into `DictationConfig.backend: str` (plain `str`, not `Literal`, because the generic config parser raises on type mismatch and we want degrade-not-block); an unknown/empty value logs a WARNING and falls back to `internal`. The value is cached on the daemon at startup; a change requires a restart (no hot-reload).

External mode reuses the existing dictation sprite machinery: it publishes the same `dictation.start` (`{}`) and `dictation.end` (`{"reason": "done"}`) events, and deliberately never publishes `dictation.processing` (no server round-trip), so no PROCESSING badge appears and no `last_timings.json` is written. No sprite, wire-protocol, or new-dependency changes.

## Consequences

### Positive

- The user keeps VC's command launcher while delegating dictation to Wispr Flow, with identical chime + visual feedback.
- Internal behaviour is byte-for-byte unchanged: every new branch is gated on `backend == "external"` or `_passthrough_active`, both inert under the default.
- No new sprite code, no new wire protocol, no new dependency.

### Negative

- No spoken exit in external mode (VC transcribes nothing) — Right Ctrl is the only exit.
- Backend change needs a daemon restart.

### Neutral

- VC and Wispr Flow share the mic via Windows shared-mode WASAPI; VC simply ignores its capture while muted.
- No key-forwarding/automation of Wispr Flow (explicit non-goal).

## Alternatives rejected

- **Backend abstraction class / strategy object** — overkill for two behaviours; a flag + branches is clearer and fully testable. (YAGNI.)
- **Suppressing Right Ctrl so only VC sees it, then VC drives Wispr Flow** — requires key synthesis/automation of a third-party app; fragile and an explicit non-goal.
- **`[dictation] mode` key** — `mode` already denotes named command modes (ADR 0100); overloading it would confuse.

## References

- Design spec: `docs/superpowers/specs/2026-05-25-dictation-backend-selector-design.md`
- Visual E2E: `scripts/dictation_external_e2e.py`
- Unit tests: `tests/unit/test_dictation_backend.py`, config tests in `tests/unit/test_config.py`
```

- [ ] **Step 2: Append the technical-decisions row**

In `docs/agents/technical-decisions.md`, after the last table row (line 120, the ADR 0101 row) and before the blank line at 121, add:

```markdown
| Dictation backend selector (internal vs external) | New `[dictation] backend` key: `internal` (default — record→WS→paste, ADR 0096) or `external` (passthrough — VC plays the same start chime + DICTATING badge and mutes its own routing, but records/transcribes/pastes nothing so an external tool like Wispr Flow bound to the same Right Ctrl does the real dictation; Right Ctrl is the only exit). One daemon boolean `_passthrough_active` gates branches in `on_dictation_toggle`, the `__dictation.start` intercept, `_process_utterance` (early-return mute), and `_close_voice_session` (reset). External mode publishes the existing `dictation.start`/`dictation.end` events, never `dictation.processing` — no sprite/wire/dependency changes. `backend` is a plain `str` validated in `Config.load()` (unknown → WARNING + `internal`), cached at startup (restart to change). | Keep VC's command launcher while delegating the unfinished dictation pipeline to Wispr Flow, with identical chime + visual feedback; no abstraction class (YAGNI). | [0102](../decisions/0102-dictation-backend-selector.md) |
```

- [ ] **Step 3: Extend the CLAUDE.md dictation paragraph**

In `CLAUDE.md`, find this substring (within the line-25 *Current state* paragraph):

```
enters dictation. **When no Scroll Lock session is open**,
```

Replace it with:

```
enters dictation. A `[dictation] backend` selector (ADR 0102) chooses the dictation engine: `internal` (default — the record→WS→paste pipeline described here) or `external` (a passthrough sub-state: VC plays the same start chime, shows the same DICTATING badge + bright cat, and mutes its own command routing, but records/transcribes/sends/pastes nothing — an external tool such as Wispr Flow bound to the same Right Ctrl does the real dictation; a second Right Ctrl press exits, restoring the prior state). External mode publishes the existing `dictation.start`/`dictation.end` events and never `dictation.processing`, so there is no PROCESSING badge and no timing write; the single daemon flag `_passthrough_active` gates the mute and is read once at startup (restart to change `backend`). **When no Scroll Lock session is open**,
```

- [ ] **Step 4: Document the config key**

In `config.toml.example`, find the `[dictation]` line (line 7) and the `ws_url` line (line 8). Insert the `backend` key immediately after `[dictation]`:

```toml
[dictation]
backend              = "internal"  # "internal" = our record→WS→paste pipeline (default); "external" = passthrough — VC mutes + animates only, an external tool (e.g. Wispr Flow bound to the same Right Ctrl) does the real dictation
ws_url               = "ws://192.168.4.200:8767/ws/transcribe"  # server-side transcription endpoint (ADR 0096)
```

- [ ] **Step 5: Document the backend in `docs/dictation-streaming.md`**

In `docs/dictation-streaming.md`, under the `## Architecture` section, add a new subsection after `### Daemon \`_finalize_dictation\``:

```markdown
### Backend selection — internal vs external (ADR 0102)

`[dictation] backend` selects the dictation engine:

- **`internal`** (default) — everything described above: record VAD-segmented PCM → stream to the WS server → paste the cleaned transcript.
- **`external`** — a *passthrough* sub-state. Right Ctrl / spoken "dictate" plays the same start chime and shows the same DICTATING badge + bright cat, but VC records nothing, transcribes nothing, sends nothing, and pastes nothing; its own command routing is muted while active (`_process_utterance` returns early on the `_passthrough_active` flag). An external tool — e.g. **Wispr Flow, bound by the user to the same Right Ctrl key** — performs the actual dictation. VC does not suppress Right Ctrl (the pynput listener has no `suppress`), so one press reaches both apps. A second Right Ctrl press exits and restores the prior state. External mode publishes the existing `dictation.start`/`dictation.end` events but **never** `dictation.processing` (no server round-trip), so no PROCESSING badge appears and no `last_timings.json` is written. There is no spoken exit in external mode (VC transcribes nothing); Right Ctrl is the only way out. The backend is cached at daemon startup — a change requires a restart.

**Wispr Flow setup:** bind Wispr Flow's dictation toggle to Right Ctrl (`ctrl_r`, VC's default `dictation_key`), set `backend = "external"` in `[dictation]`, and restart the daemon.
```

Then, in the `## Configuration (\`[dictation]\` section in \`config.toml\`)` table, add a row for `backend` (match the existing table's column layout — typically `| key | default | meaning |`):

```markdown
| `backend` | `"internal"` | `"internal"` = local record→WS→paste pipeline; `"external"` = passthrough (mute + animate only; an external tool does the dictation). Unknown value → WARNING + `"internal"`. Restart to change. |
```

> Open the file first to confirm the exact table header/column order and the precise heading text of the `### Daemon _finalize_dictation` subsection; match them.

- [ ] **Step 6: Verify docs render and links resolve**

Run: `python -m pytest tests/unit/test_config.py tests/unit/test_dictation_backend.py -q`
Expected: PASS (sanity that no doc edit broke imports referenced in examples).
Manually confirm `docs/decisions/0102-dictation-backend-selector.md` exists and the technical-decisions row links to it.

- [ ] **Step 7: Commit**

```bash
git add docs/decisions/0102-dictation-backend-selector.md docs/agents/technical-decisions.md CLAUDE.md config.toml.example docs/dictation-streaming.md
git commit -m "docs(dictation): ADR 0102 + backend selector docs"
```

---

## Final verification

- [ ] **Run the full affected suites**

```bash
python -m pytest tests/unit/test_dictation_backend.py tests/unit/test_config.py tests/unit/test_daemon_session_helpers.py tests/integration/test_dictation_opens_session.py tests/integration/test_daemon_factory.py -q
```
Expected: all PASS.

- [ ] **Run the visual E2E**

```bash
python scripts/dictation_external_e2e.py
```
Expected: exit 0, PASS line, two evidence PNGs.

- [ ] **Human validation (phase gate — per CLAUDE.md, a feature is not done until validated):**
  1. Set `backend = "external"` in `config.toml`, restart the daemon.
  2. Press Right Ctrl from idle → start chime + DICTATING badge + bright cat; confirm Wispr Flow's own dictation starts; speak → confirm VC fires no commands (muted) and Wispr Flow types the text.
  3. Press Right Ctrl again → stop chime + badge clears + cat dims; session closes.
  4. Confirm no PROCESSING badge ever appeared and `outputs/dictation/last_timings.json` was not updated.
  5. With a Scroll Lock session open, press Right Ctrl → passthrough sub-state; press again → session stays open and listening.
  6. Set `backend = "garbage"`, restart → WARNING logged, behaves as `internal`.

---

## Self-review notes

- **Spec coverage:** §1 Config → Task 1; §2 Daemon state → Tasks 2–3; §3 entry/exit toggle → Task 2; §4 spoken-dictate intercept → Task 4; §5 mute → Task 5; §6 sprite/feedback (no code, validated) → Task 8 E2E; §7 error/edge (open-session failure, scroll-lock-while-passthrough, restart-to-change, internal regression) → Tasks 2/6/7 + ADR; §8 testing → Tasks 1–8; §9 docs → Task 9.
- **ADR number corrected** from the spec's 0103 to **0102** (next free; highest existing is 0101).
- **Design decision:** `backend` is a plain `str` + explicit `Config.load()` validation rather than `Literal`, because the generic `_section` parser raises on type mismatch and the repo convention is degrade-not-block. Documented in the field comment and the ADR.
- **Type/name consistency:** `_passthrough_active` (bool), `_dictation_backend` (str), `_enter_passthrough()`/`_exit_passthrough()`, event names `dictation.start {}` / `dictation.end {"reason":"done"}`, transcribe call `self._transcriber.transcribe` — all used consistently across tasks and matched to the real code.
```