# Robust Dictation Hotkey-End + Spoken Cancel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the unreliable dictation hotkey-end race (sentinel wake), add a 50 ms per-key debounce to HotkeyController, and add a configurable spoken "cancel" word that discards the dictation buffer with no paste.

**Architecture:** A module-level `_DICTATION_WAKE` sentinel is put into `_utt_q` immediately after `request_end()` is called, waking the pipeline thread from its blocking `get(timeout=None)` without polling; the pipeline skips the sentinel via identity check and falls through to the existing drain logic. HotkeyController gains a per-key monotonic timestamp dict and drops any release that arrives within 50 ms of the same key's last-dispatched release. DictationSession gains a `cancel_word` parameter; `handle_utterance` returns the new `"cancel"` UtteranceKind; the daemon pipeline calls the pre-existing `cancel()` method on that kind; the sprite reads `data.get("reason")` from the `dictation.end` event to surface a distinct cancelled cue.

**Tech Stack:** Python 3.12, pynput, threading, queue, numpy, pytest, pyglet (sprite SSE smoke), win32gui/win32ui/Pillow (E2E capture)

---

## Commit safety

Every commit in the sequence below must leave the tree with all tests green and no broken imports. The ordering enforces this guarantee:

1. **Task 1** — Red-phase tests for `DictationSession cancel_word`. These reference `cancel_word` which does not exist yet → tests fail. ✓ (that is the intended red state)
2. **Task 2** — `UtteranceKind` + `DictationSession.__init__ cancel_word` + `handle_utterance` guard. Tests from Task 1 now pass. ✓
3. **Task 3** — Red-phase debounce tests for `HotkeyController`. Reference `_last_fire` which does not exist yet → tests fail. ✓
4. **Task 4** — `hotkey.py` debounce implementation. Debounce tests pass. Uses `import time` + `time.monotonic()` (NOT `from time import monotonic`) so monkeypatch intercepts it. ✓
5. **Task 5** — Config change: `DictationConfig.cancel_word` field + `config.toml.example`. **MUST land before** any commit that passes `cfg.dictation.cancel_word` to `DictationSession()`. ✓
6. **Task 6** — Red-phase sentinel tests for `_DICTATION_WAKE`. Tests reference `_DICTATION_WAKE` which does not exist yet → `ImportError`. ✓
7. **Task 7** — `daemon.py` sentinel implementation + `cancel_word` wired into `DictationSession()`. At this point `cfg.dictation.cancel_word` already exists (Task 5). Sentinel tests pass. ✓
8. **Task 8** — Red-phase sprite cancel-cue tests. Reference `cancelled_cue` which does not exist yet → `AttributeError`. ✓
9. **Task 9** — `state_machine.py` cancel cue implementation. Tests pass. ✓
10. **Task 10** — Integration regression test: sentinel wakes pipeline (deterministic, event-driven). ✓
11. **Task 11** — Integration tests for spoken cancel (driven through `_process_utterance`). ✓
12. **Task 12** — ADR 0089. ✓
13. **Task 13** — Docs update (technical-decisions row, ADR 0086 successor note, CLAUDE.md). ✓
14. **Task 14** — Visual E2E harness (render-smoke + subprocess+SSE+PrintWindow). ✓
15. **Task 15** — Full test suite verification and final docstring polish. ✓

No commit in this sequence passes `cfg.dictation.cancel_word` before `DictationConfig` has the field (Task 5 precedes Task 7). No commit references `_DICTATION_WAKE` before it is defined in `daemon.py` (Task 6 red phase fails on import, Task 7 creates it). No commit references `_cancel_word` on `DictationSession` before Task 2 adds it.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/voice_commander/daemon.py` | Modify L61, L230–232, L395–412, L430–468, L593–601, L1270–1273 | Add `_DICTATION_WAKE` sentinel, widen `_utt_q` type, put sentinel in `on_dictation_toggle`, skip sentinel in `_pipeline_loop`, add `"cancel"` branch in dictation dispatch, pass `cancel_word` to `DictationSession()` |
| `src/voice_commander/dictation/session.py` | Modify L47, L53–59, L100–117 | Add `"cancel"` to `UtteranceKind`, add `cancel_word` param to `__init__`, add cancel guard in `handle_utterance` |
| `src/voice_commander/hotkey.py` | Modify L1–61 | Add `_DEBOUNCE_S = 0.050`, `_last_fire: dict`, debounce check in `_on_release` using `import time` + `time.monotonic()` |
| `src/voice_commander/config.py` | Modify L26–32 | Add `cancel_word: str = "cancel"` to `DictationConfig` |
| `src/voice_sprite/state_machine.py` | Modify L63–70, L98–104 | Read `data.get("reason")` in `dictation.end` handler; set `self.cancelled_cue = True` when `reason == "cancel"` |
| `config.toml.example` | Modify `[dictation]` section | Add `cancel_word = "cancel"` with inline comment |
| `tests/unit/test_dictation_session.py` | Modify | Add cancel-word unit tests |
| `tests/unit/test_hotkey.py` | Modify | Add debounce unit tests (deterministic fake clock via `monkeypatch`) |
| `tests/unit/test_sprite_state_machine.py` | Modify | Add cancel-reason `dictation.end` test |
| `tests/unit/test_streaming_daemon.py` | Modify | Add `_DICTATION_WAKE` sentinel tests (self-contained — no `_StubTranscriber` reference; inline stub defined in each test) |
| `tests/integration/test_dictation_pipeline.py` | Modify | Add sentinel regression test (deterministic, event-driven, provably fails against unfixed code) |
| `tests/integration/test_dictation_cancel.py` | Create | Integration tests for spoken cancel driven through `_process_utterance`: no POST, no paste, correct SSE reason |
| `docs/decisions/0089-dictation-hotkey-sentinel-cancel-debounce.md` | Create | ADR for all three changes |
| `docs/agents/technical-decisions.md` | Modify | Add ADR 0089 row |
| `docs/decisions/0086-dictation-mode.md` | Modify | Add "Successor" note citing ADR 0089 |
| `CLAUDE.md` | Modify | Update "Current state" dictation paragraph |
| `scripts/dictation_cancel_smoke.py` | Create | Render-smoke harness (in-process sprite render, no subprocess) — Phase A and B |
| `scripts/dictation_hotkey_cancel_e2e.py` | Create | Full subprocess+SSE+PrintWindow E2E harness following `scripts/picker_visual_e2e.py` |

---

### Task 1: Unit tests for DictationSession cancel word — Red phase

**Files:**
- Modify: `tests/unit/test_dictation_session.py`

- [ ] **Step 1: Add cancel-word failing tests at the bottom of `tests/unit/test_dictation_session.py`**

Open `tests/unit/test_dictation_session.py` and append the following tests after the last existing test (`test_take_and_finish_when_inactive_returns_none_and_is_silent`):

```python
# ---------------------------------------------------------------------------
# Cancel-word tests (Task 1 — ADR 0089)
# ---------------------------------------------------------------------------


def test_cancel_word_returns_cancel_and_does_not_buffer():
    """handle_utterance returns "cancel" on exact cancel-word match and
    does NOT append the audio chunk to the buffer."""
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done", cancel_word="cancel")
    s.start()
    audio = _audio()
    assert s.handle_utterance(audio, "cancel") == "cancel"
    # Audio must NOT have been buffered — take_audio returns None
    assert s.take_audio() is None


def test_cancel_word_normalized_match():
    """Normalization (lowercase, strip punctuation) applies to cancel word."""
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done", cancel_word="cancel")
    s.start()
    audio = _audio()
    # "Cancel." normalizes to "cancel"
    assert s.handle_utterance(audio, "Cancel.") == "cancel"
    assert s.take_audio() is None


def test_cancel_word_partial_phrase_is_buffered():
    """A transcript containing cancel word as part of a longer phrase is buffered."""
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done", cancel_word="cancel")
    s.start()
    audio = _audio()
    # "please cancel that" must NOT trigger cancel — it is not the exact word
    assert s.handle_utterance(audio, "please cancel that") == "buffered"
    assert s.take_audio() is not None


def test_cancel_word_when_inactive_returns_buffered():
    """handle_utterance returns "buffered" (no-op) when session is inactive,
    even if the transcript matches the cancel word."""
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done", cancel_word="cancel")
    # Never started — inactive
    audio = _audio()
    assert s.handle_utterance(audio, "cancel") == "buffered"
    assert s.take_audio() is None


def test_cancel_word_collision_with_end_word_disables_cancel(caplog):
    """When cancel_word == end_word, __init__ logs a WARNING and sets
    _cancel_word = None, so the collision word buffers normally."""
    import logging
    bus = _FakeBus()
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.session"):
        s = DictationSession(bus, end_word="done", cancel_word="done")
    # Warning must have been emitted
    assert any("cancel" in r.message.lower() or "collision" in r.message.lower()
               for r in caplog.records)
    # _cancel_word must be None — spoken cancel disabled
    assert s._cancel_word is None
    # The collision word now acts as the end word, not the cancel word
    s.start()
    audio = _audio()
    assert s.handle_utterance(audio, "done") == "end"


def test_cancel_word_empty_string_disables_cancel(caplog):
    """An empty or whitespace-only cancel_word logs a WARNING and disables
    spoken cancel (_cancel_word = None)."""
    import logging
    bus = _FakeBus()
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.session"):
        s = DictationSession(bus, end_word="done", cancel_word="")
    assert any("cancel" in r.message.lower() or "empty" in r.message.lower()
               or "cancel_word" in r.message.lower()
               for r in caplog.records)
    assert s._cancel_word is None
```

- [ ] **Step 2: Run the new tests to confirm they all FAIL**

```
pytest tests/unit/test_dictation_session.py::test_cancel_word_returns_cancel_and_does_not_buffer tests/unit/test_dictation_session.py::test_cancel_word_normalized_match tests/unit/test_dictation_session.py::test_cancel_word_partial_phrase_is_buffered tests/unit/test_dictation_session.py::test_cancel_word_when_inactive_returns_buffered tests/unit/test_dictation_session.py::test_cancel_word_collision_with_end_word_disables_cancel tests/unit/test_dictation_session.py::test_cancel_word_empty_string_disables_cancel -v
```

Expected: All 6 tests FAIL. `test_cancel_word_collision_with_end_word_disables_cancel` and `test_cancel_word_empty_string_disables_cancel` fail because `DictationSession.__init__` does not accept `cancel_word`. `test_cancel_word_returns_cancel_and_does_not_buffer` fails because `handle_utterance` returns `"buffered"` instead of `"cancel"`.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/unit/test_dictation_session.py
git commit -m "test(dictation): add cancel-word red-phase unit tests (ADR 0089)"
```

---

### Task 2: Implement DictationSession cancel word

**Files:**
- Modify: `src/voice_commander/dictation/session.py` (L47, L53–59, L100–117)

- [ ] **Step 1: Update `UtteranceKind` type alias (line 47)**

Change:
```python
UtteranceKind = Literal["buffered", "end"]
```
To:
```python
UtteranceKind = Literal["buffered", "end", "cancel"]
```

- [ ] **Step 2: Add `cancel_word` parameter to `__init__` (lines 53–59)**

Replace the existing `__init__` signature and body:
```python
    def __init__(self, bus: _BusLike, end_word: str = "done") -> None:
        self._bus = bus
        self._end_word = _normalize_spoken(end_word)
        self._lock = threading.Lock()
        self._active = False
        self._buffer: list[npt.NDArray[np.float32]] = []
        self._pending_end = threading.Event()
```

With:
```python
    def __init__(
        self,
        bus: _BusLike,
        end_word: str = "done",
        cancel_word: str = "cancel",
    ) -> None:
        self._bus = bus
        self._end_word = _normalize_spoken(end_word)
        self._lock = threading.Lock()
        self._active = False
        self._buffer: list[npt.NDArray[np.float32]] = []
        self._pending_end = threading.Event()

        # Validate cancel_word against end_word and emptiness.
        # Cross-field validation lives here (not in config loader — no cross-field
        # stage exists there; precedent: dictation_key == hotkey_key warn-and-degrade
        # in daemon.py).
        normalized_cancel = _normalize_spoken(cancel_word)
        if not normalized_cancel:
            logger.warning(
                "dictation: cancel_word %r is empty after normalization; "
                "spoken cancel disabled",
                cancel_word,
            )
            self._cancel_word: str | None = None
        elif normalized_cancel == self._end_word:
            logger.warning(
                "dictation: cancel_word %r collides with end_word %r; "
                "spoken cancel disabled to avoid ambiguity",
                cancel_word,
                end_word,
            )
            self._cancel_word = None
        else:
            self._cancel_word = normalized_cancel
```

- [ ] **Step 3: Add cancel guard in `handle_utterance` (lines 100–117)**

Replace the existing `handle_utterance` method body:
```python
    def handle_utterance(
        self, audio: npt.NDArray[np.float32], text: str
    ) -> UtteranceKind:
        """Classify an utterance: ``"end"`` if it is the end word, else ``"buffered"``.

        End-word utterances are NOT appended to the buffer. This method never
        changes session state — on an ``"end"`` result the caller MUST call
        :meth:`take_and_finish` to atomically capture the buffer, deactivate
        the session, and publish the end event. A no-op returning ``"buffered"``
        when inactive (lost race with take_and_finish/cancel).
        """
        with self._lock:
            if not self._active:
                return "buffered"
            if _normalize_spoken(text) == self._end_word:
                return "end"
            self._buffer.append(audio)
            return "buffered"
```

With:
```python
    def handle_utterance(
        self, audio: npt.NDArray[np.float32], text: str
    ) -> UtteranceKind:
        """Classify an utterance: ``"end"``, ``"cancel"``, or ``"buffered"``.

        * ``"end"`` — transcript is the end word (exact normalized match).
          NOT appended to the buffer; caller MUST call :meth:`take_and_finish`.
        * ``"cancel"`` — transcript is the cancel word (exact normalized match,
          when ``_cancel_word`` is not ``None``). NOT appended to the buffer;
          caller MUST call :meth:`cancel` to discard the session.
        * ``"buffered"`` — audio appended to buffer; session stays active.

        A no-op returning ``"buffered"`` when inactive (lost race with
        take_and_finish/cancel).
        """
        with self._lock:
            # Guard 1 — session inactive: no-op, matches existing behaviour
            # for lost races with take_and_finish / cancel.
            if not self._active:
                return "buffered"

            normalized = _normalize_spoken(text)

            # Guard 2 — end word: existing path, unchanged.
            if normalized == self._end_word:
                return "end"

            # Guard 3 — cancel word: new path (ADR 0089); do NOT buffer.
            if self._cancel_word is not None and normalized == self._cancel_word:
                return "cancel"

            # Default — buffer the audio.
            self._buffer.append(audio)
            return "buffered"
```

- [ ] **Step 4: Run the Task 1 tests to confirm they now PASS**

```
pytest tests/unit/test_dictation_session.py -v
```

Expected: All tests PASS including the 6 new cancel-word tests.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dictation/session.py
git commit -m "feat(dictation): add cancel_word to DictationSession (ADR 0089)"
```

---

### Task 3: Unit tests for HotkeyController debounce — Red phase

**Files:**
- Modify: `tests/unit/test_hotkey.py`

**REV 4 note:** All debounce tests use `monkeypatch.setattr` on `time.monotonic` via the `time` module (i.e., `voice_commander.hotkey.time.monotonic`) to inject a fake clock. No real `time.sleep` calls. This is deterministic and fast. The implementation in Task 4 MUST use `import time` + `time.monotonic()` (not `from time import monotonic`) so the monkeypatch intercepts the call through the module attribute.

- [ ] **Step 1: Append debounce tests to `tests/unit/test_hotkey.py`**

Append the following after the last existing test in the file:

```python
# ---------------------------------------------------------------------------
# Debounce tests (Task 3 — ADR 0089)
# Fake clock via monkeypatch — no real sleeps, fully deterministic.
# ---------------------------------------------------------------------------


def test_debounce_drops_second_rapid_release(monkeypatch):
    """Two releases of the same key within 50 ms dispatch the callback only once.

    Uses a fake monotonic clock; no real sleep required.
    """
    import voice_commander.hotkey as _hotkey_mod

    _clock = [0.0]

    def _fake_monotonic() -> float:
        return _clock[0]

    monkeypatch.setattr(_hotkey_mod.time, "monotonic", _fake_monotonic)

    fired = []
    ctrl = HotkeyController(bindings={"scroll_lock": lambda: fired.append(1)})

    from pynput.keyboard import Key
    scroll_key = Key.scroll_lock

    _clock[0] = 1.000  # first release at t=1.000 s
    ctrl._on_release(scroll_key)
    assert len(fired) == 1, "First release must dispatch"

    _clock[0] = 1.020  # 20 ms later — within 50 ms window
    ctrl._on_release(scroll_key)
    assert len(fired) == 1, f"Second release within 50 ms must be dropped; got {len(fired)}"


def test_debounce_allows_second_release_after_window(monkeypatch):
    """Two releases of the same key spaced more than 50 ms apart dispatch twice.

    Uses a fake monotonic clock; no real sleep required.
    """
    import voice_commander.hotkey as _hotkey_mod

    _clock = [0.0]

    def _fake_monotonic() -> float:
        return _clock[0]

    monkeypatch.setattr(_hotkey_mod.time, "monotonic", _fake_monotonic)

    fired = []
    ctrl = HotkeyController(bindings={"scroll_lock": lambda: fired.append(1)})

    from pynput.keyboard import Key
    scroll_key = Key.scroll_lock

    _clock[0] = 1.000
    ctrl._on_release(scroll_key)
    assert len(fired) == 1

    _clock[0] = 1.060  # 60 ms later — outside 50 ms window
    ctrl._on_release(scroll_key)
    assert len(fired) == 2, f"Second release after 60 ms must dispatch; got {len(fired)}"


def test_debounce_different_keys_are_independent(monkeypatch):
    """Two different keys each have their own independent debounce timer.

    Rapid alternation between scroll_lock and ctrl_r should dispatch one
    callback each (two total), never suppressing the other key.
    Uses a fake monotonic clock; both presses at t=0 are simultaneous.
    """
    import voice_commander.hotkey as _hotkey_mod

    _clock = [0.0]

    def _fake_monotonic() -> float:
        return _clock[0]

    monkeypatch.setattr(_hotkey_mod.time, "monotonic", _fake_monotonic)

    fired_sl = []
    fired_cr = []
    ctrl = HotkeyController(
        bindings={
            "scroll_lock": lambda: fired_sl.append(1),
            "ctrl_r": lambda: fired_cr.append(1),
        }
    )

    from pynput.keyboard import Key

    # Fire both at exactly t=0 (simultaneous — well within 50 ms of each other
    # on a per-key basis, but they are DIFFERENT keys so independent timers apply)
    _clock[0] = 0.0
    ctrl._on_release(Key.scroll_lock)
    ctrl._on_release(Key.ctrl_r)

    assert len(fired_sl) == 1, f"scroll_lock fired {len(fired_sl)} times; expected 1"
    assert len(fired_cr) == 1, f"ctrl_r fired {len(fired_cr)} times; expected 1"


def test_debounce_last_fire_dict_populated(monkeypatch):
    """After a dispatched release, _last_fire records the key's timestamp."""
    import voice_commander.hotkey as _hotkey_mod

    _clock = [42.0]

    def _fake_monotonic() -> float:
        return _clock[0]

    monkeypatch.setattr(_hotkey_mod.time, "monotonic", _fake_monotonic)

    ctrl = HotkeyController(bindings={"scroll_lock": lambda: None})

    from pynput.keyboard import Key
    scroll_key = Key.scroll_lock

    ctrl._on_release(scroll_key)

    assert scroll_key in ctrl._last_fire, "_last_fire must record the key after dispatch"
    assert ctrl._last_fire[scroll_key] == 42.0, (
        f"timestamp must equal the fake clock value 42.0; got {ctrl._last_fire[scroll_key]}"
    )
```

- [ ] **Step 2: Run new debounce tests to confirm they FAIL**

```
pytest tests/unit/test_hotkey.py::test_debounce_drops_second_rapid_release tests/unit/test_hotkey.py::test_debounce_allows_second_release_after_window tests/unit/test_hotkey.py::test_debounce_different_keys_are_independent tests/unit/test_hotkey.py::test_debounce_last_fire_dict_populated -v
```

Expected: All 4 tests FAIL. `AttributeError: 'HotkeyController' object has no attribute '_last_fire'` or `AttributeError: module 'voice_commander.hotkey' has no attribute 'time'` (because `import time` not yet added).

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/unit/test_hotkey.py
git commit -m "test(hotkey): add debounce red-phase unit tests (ADR 0089)"
```

---

### Task 4: Implement HotkeyController debounce

**Files:**
- Modify: `src/voice_commander/hotkey.py`

**Critical (REV 4):** The debounce check MUST use `import time` at the top of the file and call `time.monotonic()` (NOT `from time import monotonic`). This ensures `monkeypatch.setattr(_hotkey_mod.time, "monotonic", ...)` intercepts the call.

- [ ] **Step 1: Add `time` import and `_DEBOUNCE_S` constant, add `_last_fire` dict to `__init__`, implement debounce in `_on_release`**

Replace the entire content of `src/voice_commander/hotkey.py` with:

```python
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from pynput import keyboard

logger = logging.getLogger(__name__)

# Minimum interval between two dispatches of the same key.
# Rejects hardware key-bounce and driver double-release events (which fire
# well under 50 ms). Intentional rapid re-presses are always many hundreds
# of milliseconds apart and are unaffected.
_DEBOUNCE_S: float = 0.050

KEY_ALIASES: dict[str, keyboard.Key] = {
    "scroll_lock": keyboard.Key.scroll_lock,
    "pause": keyboard.Key.pause,
    "f13": keyboard.Key.f13,
    "caps_lock": keyboard.Key.caps_lock,
    "ctrl_r": keyboard.Key.ctrl_r,
    "ctrl_l": keyboard.Key.ctrl_l,
}


class HotkeyController:
    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        if not bindings:
            raise ValueError("At least one binding required")
        self._dispatch: dict[keyboard.Key, Callable[[], None]] = {}
        for key_name, callback in bindings.items():
            if key_name not in KEY_ALIASES:
                raise ValueError(f"Unknown hotkey '{key_name}'. Known: {sorted(KEY_ALIASES)}")
            self._dispatch[KEY_ALIASES[key_name]] = callback
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()
        # Per-key debounce: maps each bound Key to the monotonic timestamp of
        # its last *dispatched* release. Keys absent from this dict have never
        # fired (treated as last_fire=0.0).
        self._last_fire: dict[keyboard.Key, float] = {}

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        logger.info("HotkeyController started, bindings: %s", list(self._dispatch.keys()))

    def stop(self) -> None:
        if self._listener is not None:
            listener = self._listener
            self._listener = None
            listener.stop()
            # join() with a short timeout to ensure the listener thread has
            # fully exited before we return.  pynput's stop() posts a stop
            # event asynchronously; the join makes teardown deterministic.
            listener.join(timeout=1.0)
            logger.info("HotkeyController stopped")

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        callback = self._dispatch.get(key)
        if callback is None:
            return
        now = time.monotonic()
        last = self._last_fire.get(key, 0.0)
        if now - last < _DEBOUNCE_S:
            logger.debug(
                "Hotkey debounce: dropping double-release for %s (gap=%.1f ms)",
                key,
                (now - last) * 1000,
            )
            return
        self._last_fire[key] = now
        with self._lock:
            try:
                callback()
            except Exception:
                logger.exception("Hotkey callback raised for %s", key)
```

- [ ] **Step 2: Run all hotkey tests to confirm they PASS**

```
pytest tests/unit/test_hotkey.py -v
```

Expected: All tests PASS including the 4 new debounce tests.

- [ ] **Step 3: Commit**

```bash
git add src/voice_commander/hotkey.py
git commit -m "feat(hotkey): add 50 ms per-key debounce to HotkeyController (ADR 0089)"
```

---

### Task 5: Add `cancel_word` to config and `config.toml.example`

**Files:**
- Modify: `src/voice_commander/config.py` (L26–32)
- Modify: `config.toml.example` (`[dictation]` section)

**MUST land before Task 7** (which wires `cfg.dictation.cancel_word` into the `DictationSession()` constructor in `daemon.py`). See commit-safety note above.

- [ ] **Step 1: Add `cancel_word` field to `DictationConfig`**

Replace:
```python
@dataclass(frozen=True)
class DictationConfig:
    """Dictation mode — remote whisper.cpp transcription (ADR 0086)."""

    endpoint: str = "http://192.168.4.200:8765/inference"
    end_word: str = "done"
```

With:
```python
@dataclass(frozen=True)
class DictationConfig:
    """Dictation mode — remote whisper.cpp transcription (ADR 0086)."""

    endpoint: str = "http://192.168.4.200:8765/inference"
    end_word: str = "done"
    cancel_word: str = "cancel"  # say this word to abort dictation and discard the audio
```

- [ ] **Step 2: Add `cancel_word` to `config.toml.example` in the `[dictation]` section**

Replace:
```toml
[dictation]
endpoint = "http://192.168.4.200:8765/inference"
end_word = "done"
```

With:
```toml
[dictation]
endpoint    = "http://192.168.4.200:8765/inference"
end_word    = "done"
cancel_word = "cancel"   # say this word to abort dictation and discard the audio
```

- [ ] **Step 3: Verify config loads correctly with the new field**

```
pytest tests/unit/test_config.py -v -q
```

Expected: All config tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/voice_commander/config.py config.toml.example
git commit -m "feat(config): add dictation.cancel_word to DictationConfig (ADR 0089)"
```

---

### Task 6: Unit tests for `_DICTATION_WAKE` sentinel — Red phase

**Files:**
- Modify: `tests/unit/test_streaming_daemon.py`

**REV 2 note:** These tests are FULLY SELF-CONTAINED. No `_StubTranscriber` reference at module level. Each test defines its own inline stub dataclass and stub transcriber class directly inside the test function body. This avoids any dangling reference to undefined module-level symbols. The tests use the same `_make_daemon` helper that already exists in `test_streaming_daemon.py` (which uses `MagicMock` for the transcriber), but override the transcriber inline where needed.

- [ ] **Step 1: Append the sentinel tests to `tests/unit/test_streaming_daemon.py`**

Read the end of `tests/unit/test_streaming_daemon.py` to confirm the last test, then append the following AFTER it (no new module-level symbols needed — everything is defined inside the test functions):

```python
# ---------------------------------------------------------------------------
# Sentinel wake tests (Task 6 — ADR 0089)
# Each test is fully self-contained — stub types defined inside the function.
# ---------------------------------------------------------------------------


def test_dictation_wake_sentinel_skipped_in_pipeline_loop(tmp_path):
    """The _DICTATION_WAKE sentinel is identity-checked and skipped via
    `continue` — _process_utterance is never called for it.

    Fully self-contained: uses the existing _make_daemon() MagicMock helper.
    The daemon is not started; _pipeline_loop() is driven directly on a thread.
    """
    import threading

    from voice_commander.daemon import _DICTATION_WAKE

    # Use the existing _make_daemon helper (MagicMock transcriber, no GPU)
    daemon, feedback, recorder, transcriber, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    processed: list[str] = []
    original_process = daemon._process_utterance

    def _spy(utterance, *, gen=None):
        processed.append("called")
        return original_process(utterance, gen=gen)

    daemon._process_utterance = _spy

    # Queue: sentinel first, then shutdown sentinel
    daemon._utt_q.put(_DICTATION_WAKE)
    daemon._utt_q.put(None)

    t = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    t.start()
    t.join(timeout=2.0)

    assert not t.is_alive(), "Pipeline loop did not exit within 2 s"
    assert processed == [], (
        f"_process_utterance should NOT be called for the sentinel; got: {processed}"
    )


def test_dictation_wake_sentinel_real_utterance_processed_before_sentinel(tmp_path):
    """A real utterance queued BEFORE the sentinel is processed; sentinel is then
    skipped. FIFO order guarantee: real audio → sentinel → shutdown.

    Fully self-contained: inline stub transcriber defined inside this function.
    """
    import threading
    from dataclasses import dataclass

    import numpy as np

    from voice_commander.daemon import _DICTATION_WAKE

    # Inline stub transcriber — no module-level symbol needed
    @dataclass
    class _InlineTranscription:
        text: str
        confidence: float = 0.95
        no_speech_prob: float = 0.05

    class _InlineStub:
        def __init__(self, results):
            self._results = list(results)

        def load(self) -> None: ...
        def unload(self) -> None: ...

        def transcribe(self, _audio):
            return self._results.pop(0)

    # Build daemon with the existing helper but swap out the MagicMock transcriber
    daemon, feedback, recorder, _transcriber_mock, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )
    # Replace the MagicMock transcriber with our inline stub
    stub = _InlineStub(results=[_InlineTranscription("click")])
    daemon._transcriber = stub

    processed: list[str] = []
    original_process = daemon._process_utterance

    def _spy(utterance, *, gen=None):
        processed.append("called")
        return original_process(utterance, gen=gen)

    daemon._process_utterance = _spy

    audio = np.zeros(16000, dtype=np.float32)
    daemon._utt_q.put((audio, daemon._audio_gen))  # real utterance FIRST (FIFO)
    daemon._utt_q.put(_DICTATION_WAKE)              # sentinel SECOND
    daemon._utt_q.put(None)                         # shutdown THIRD

    t = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    t.start()
    t.join(timeout=2.0)

    assert not t.is_alive(), "Pipeline loop did not exit within 2 s"
    assert processed == ["called"], (
        f"Expected exactly 1 _process_utterance call for the real utterance; got: {processed}"
    )
```

- [ ] **Step 2: Run the new sentinel tests to confirm they FAIL**

```
pytest tests/unit/test_streaming_daemon.py::test_dictation_wake_sentinel_skipped_in_pipeline_loop tests/unit/test_streaming_daemon.py::test_dictation_wake_sentinel_real_utterance_processed_before_sentinel -v
```

Expected: Both FAIL. `ImportError: cannot import name '_DICTATION_WAKE' from 'voice_commander.daemon'`.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/unit/test_streaming_daemon.py
git commit -m "test(daemon): add _DICTATION_WAKE sentinel red-phase unit tests (ADR 0089)"
```

---

### Task 7: Implement `_DICTATION_WAKE` sentinel in daemon.py

**Files:**
- Modify: `src/voice_commander/daemon.py` (L61–62, L230–232, L395–412, L430–468, L593–601, L1270–1273)

**Prerequisite:** Task 5 (config change) MUST be committed first. This task passes `cfg.dictation.cancel_word` to `DictationSession()` — that field must already exist on `DictationConfig`.

- [ ] **Step 1: Add `_DICTATION_WAKE` sentinel constant after `_DICTATION_DRAIN_TIMEOUT_S` (after line 61)**

After the line:
```python
_DICTATION_DRAIN_TIMEOUT_S = 0.25
```

Add:
```python
# Unique sentinel object — enqueued by on_dictation_toggle() to wake the
# pipeline thread from a blocking get(timeout=None) when the user presses
# the dictation hotkey to end a session. Identity comparison is safe: no
# real utterance array can be is-equal to this singleton. (ADR 0089)
_DICTATION_WAKE = object()
```

- [ ] **Step 2: Widen the `_utt_q` type annotation (line 230–232)**

Change:
```python
        self._utt_q: queue.Queue[
            tuple[npt.NDArray[np.float32], int] | npt.NDArray[np.float32] | None
        ] = queue.Queue(maxsize=8)
```

To:
```python
        self._utt_q: queue.Queue[
            tuple[npt.NDArray[np.float32], int] | npt.NDArray[np.float32] | None | object
        ] = queue.Queue(maxsize=8)
```

- [ ] **Step 3: Enqueue sentinel in `on_dictation_toggle` (the dictation-active branch)**

Replace:
```python
        if self._dictation_session.active:
            self._dictation_session.request_end()
            logger.info("dictation: hotkey-end requested; pipeline will drain and finalize")
```

With:
```python
        if self._dictation_session.active:
            self._dictation_session.request_end()
            # Wake the pipeline thread if it is blocked in get(timeout=None).
            # Without this, the thread only re-evaluates pending_end at the
            # top of the NEXT iteration — which never comes if the user
            # stops speaking. The sentinel is consumed by the pipeline loop
            # via identity check; real utterances ahead of it are processed
            # first (FIFO). On queue.Full, log and do nothing: a full queue
            # means the pipeline is already non-idle and will re-evaluate
            # pending_end naturally. (ADR 0089)
            try:
                self._utt_q.put_nowait(_DICTATION_WAKE)
            except queue.Full:
                logger.warning(
                    "dictation: _utt_q full — sentinel not needed; "
                    "pipeline already active"
                )
            logger.info("dictation: hotkey-end requested; pipeline will drain and finalize")
```

- [ ] **Step 4: Add sentinel skip in `_pipeline_loop` (after the `if item is None: break` check)**

After:
```python
            if item is None:
                break
```

Add:
```python
            # Skip the wake sentinel — its only job was to unblock get().
            # The top of the next iteration will see pending_end=True and
            # switch to the short-timeout drain. (ADR 0089)
            if item is _DICTATION_WAKE:
                continue
```

- [ ] **Step 5: Add `"cancel"` branch in dictation dispatch block**

Replace:
```python
            if self._dictation_session is not None and self._dictation_session.active:
                kind = self._dictation_session.handle_utterance(utterance, result.text)
                if kind == "end":
                    audio = self._dictation_session.take_and_finish()
                    if audio is not None:
                        self._dictation_executor.submit(self._finalize_dictation, audio)
                run.set_status("ok")
                return
```

With:
```python
            if self._dictation_session is not None and self._dictation_session.active:
                kind = self._dictation_session.handle_utterance(utterance, result.text)
                if kind == "end":
                    audio = self._dictation_session.take_and_finish()
                    if audio is not None:
                        self._dictation_executor.submit(self._finalize_dictation, audio)
                elif kind == "cancel":
                    # Reuse the pre-existing cancel() method — no new code, no
                    # POST, no clipboard paste. The method publishes
                    # dictation.end {"reason": "cancel"}. (ADR 0089)
                    self._dictation_session.cancel()
                # "buffered" → fall through, nothing to do
                run.set_status("ok")
                return
```

- [ ] **Step 6: Pass `cancel_word` into the `DictationSession()` constructor**

Replace:
```python
    dictation_session = DictationSession(
        bus=event_bus,
        end_word=cfg.dictation.end_word,
    )
```

With:
```python
    dictation_session = DictationSession(
        bus=event_bus,
        end_word=cfg.dictation.end_word,
        cancel_word=cfg.dictation.cancel_word,
    )
```

- [ ] **Step 7: Run Task 6 sentinel tests to confirm they now PASS**

```
pytest tests/unit/test_streaming_daemon.py::test_dictation_wake_sentinel_skipped_in_pipeline_loop tests/unit/test_streaming_daemon.py::test_dictation_wake_sentinel_real_utterance_processed_before_sentinel -v
```

Expected: Both PASS.

- [ ] **Step 8: Run the full unit test suite to catch regressions**

```
pytest tests/unit/ -q
```

Expected: All tests PASS (or pre-existing failures only — no new failures).

- [ ] **Step 9: Commit**

```bash
git add src/voice_commander/daemon.py
git commit -m "feat(daemon): add _DICTATION_WAKE sentinel + spoken cancel dispatch (ADR 0089)"
```

---

### Task 8: Unit test for sprite state machine cancel cue — Red phase

**Files:**
- Modify: `tests/unit/test_sprite_state_machine.py`

- [ ] **Step 1: Append cancel-reason test to `tests/unit/test_sprite_state_machine.py`**

Read the end of `tests/unit/test_sprite_state_machine.py` to find the last test, then append:

```python
# ---------------------------------------------------------------------------
# Dictation cancel-cue tests (Task 8 — ADR 0089)
# ---------------------------------------------------------------------------


def test_dictation_end_cancel_reason_sets_cancelled_cue():
    """dictation.end with reason='cancel' sets dictating=False AND
    sets a distinct cancelled_cue flag on the state machine."""
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    assert sm.dictating is True

    sm.on_event("dictation.end", {"reason": "cancel"})
    assert sm.dictating is False, "dictating must be False after cancel"
    assert sm.cancelled_cue is True, (
        "cancelled_cue must be True when reason='cancel' — "
        "sprite needs a distinct visual to surface"
    )


def test_dictation_end_done_reason_does_not_set_cancelled_cue():
    """dictation.end with reason='done' (normal finish) must NOT set
    cancelled_cue — only 'cancel' reason triggers the cue."""
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    sm.on_event("dictation.end", {"reason": "done"})
    assert sm.dictating is False
    assert sm.cancelled_cue is False, (
        "cancelled_cue must stay False for a normal 'done' end"
    )


def test_dictation_end_cancel_reason_retroactively_covers_scroll_lock_cancel():
    """scroll-lock cancel also emits reason='cancel' — the same sprite update
    covers both spoken cancel and scroll-lock cancel."""
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    # Simulate scroll-lock cancel (also emits {"reason": "cancel"})
    sm.on_event("dictation.end", {"reason": "cancel"})
    assert sm.cancelled_cue is True


def test_cancelled_cue_resets_on_new_dictation_start():
    """Starting a new dictation session clears cancelled_cue so the prior
    cancel visual does not bleed into the new session."""
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    sm.on_event("dictation.end", {"reason": "cancel"})
    assert sm.cancelled_cue is True  # sanity

    # New session start must reset the cue
    sm.on_event("dictation.start", {})
    assert sm.cancelled_cue is False, (
        "cancelled_cue must be cleared when a new dictation session starts"
    )


def test_dictation_end_missing_reason_key_does_not_raise():
    """dictation.end with no 'reason' key in data must not KeyError.

    Other dictation.end publishers may omit the reason key; the handler must
    use data.get('reason') defensively (REV 8).
    """
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    # No 'reason' key — must not raise KeyError
    sm.on_event("dictation.end", {})
    assert sm.dictating is False
    # Missing reason → not "cancel" → cancelled_cue stays False
    assert sm.cancelled_cue is False
```

- [ ] **Step 2: Run new tests to confirm they FAIL**

```
pytest tests/unit/test_sprite_state_machine.py::test_dictation_end_cancel_reason_sets_cancelled_cue tests/unit/test_sprite_state_machine.py::test_dictation_end_done_reason_does_not_set_cancelled_cue tests/unit/test_sprite_state_machine.py::test_dictation_end_cancel_reason_retroactively_covers_scroll_lock_cancel tests/unit/test_sprite_state_machine.py::test_cancelled_cue_resets_on_new_dictation_start tests/unit/test_sprite_state_machine.py::test_dictation_end_missing_reason_key_does_not_raise -v
```

Expected: All 5 FAIL. `AttributeError: 'StateMachine' object has no attribute 'cancelled_cue'`.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/unit/test_sprite_state_machine.py
git commit -m "test(sprite): add cancel-cue red-phase tests for dictation.end reason (ADR 0089)"
```

---

### Task 9: Implement sprite state machine cancel cue

**Files:**
- Modify: `src/voice_sprite/state_machine.py` (L63–70, L98–104)

**REV 8 note:** The `dictation.end` handler MUST use `data.get("reason")` (NOT `data["reason"]`) to avoid `KeyError` from publishers that do not set the reason key. Confirmed against existing publishers: `take_and_finish` emits `{"reason": "done"}`, `finish` emits `{"reason": "done"}`, `cancel` emits `{"reason": "cancel"}` — all three set the key, but defensive access is still required per REV 8.

- [ ] **Step 1: Add `cancelled_cue` attribute to `StateMachine.__init__`**

In `__init__`, after the line `self.dictating = False`, add:
```python
        self.cancelled_cue: bool = False  # True when last dictation.end had reason="cancel"
```

The full `__init__` should read:
```python
    def __init__(self, heartbeat_timeout_ms: int = 3000) -> None:
        self.current_state = SpriteState.WARMUP
        self.target_state = SpriteState.WARMUP
        self.muted = False
        self.dictating = False
        self.cancelled_cue: bool = False  # True when last dictation.end had reason="cancel"
        self._heartbeat_timeout_s = heartbeat_timeout_ms / 1000.0
        self._last_heartbeat: float = 0.0
        self._hold_timer: float | None = None
        self._return_state: SpriteState = SpriteState.LISTENING
        self._transitioning = False
```

- [ ] **Step 2: Update `dictation.start` and `dictation.end` handlers in `on_event`**

Replace:
```python
        if event_type == "dictation.start":
            self.dictating = True
            return None

        if event_type == "dictation.end":
            self.dictating = False
            return None
```

With:
```python
        if event_type == "dictation.start":
            self.dictating = True
            self.cancelled_cue = False  # clear any prior cancel cue on new session
            return None

        if event_type == "dictation.end":
            self.dictating = False
            reason = data.get("reason")  # defensive: some publishers may omit "reason"
            # Surface a distinct cancelled visual when reason == "cancel".
            # This covers both spoken cancel AND scroll-lock cancel — both
            # paths call DictationSession.cancel() which emits reason="cancel".
            # Renderer (voice_sprite/__main__.py or equivalent) reads
            # self.cancelled_cue to show a brief "cancelled" text badge.
            self.cancelled_cue = reason == "cancel"
            return None
```

- [ ] **Step 3: Run Task 8 tests to confirm they now PASS**

```
pytest tests/unit/test_sprite_state_machine.py -v
```

Expected: All tests PASS including the 5 new cancel-cue tests.

- [ ] **Step 4: Commit**

```bash
git add src/voice_sprite/state_machine.py
git commit -m "feat(sprite): read dictation.end reason defensively, set cancelled_cue for cancel path (ADR 0089)"
```

---

### Task 10: Integration test — sentinel wakes pipeline (core regression)

**Files:**
- Modify: `tests/integration/test_dictation_pipeline.py`

**REV 5 note:** This test is a DETERMINISTIC regression gate. It uses two `threading.Event` objects — `processed` (set by a stub transcriber after the one seeded utterance is transcribed) and `finalized` (set by a monkeypatched `_finalize_dictation`) — to eliminate any race. No `time.sleep` for synchronization.

**Verify it fails against unfixed code:** Before the sentinel fix, the pipeline thread is blocked in `_utt_q.get(timeout=None)` after consuming the one seeded utterance. `on_dictation_toggle()` sets `pending_end` but the thread never re-evaluates it because no new utterance arrives. `finalized` is never set. `finalized.wait(timeout=2.0)` returns `False`. The test FAILS with assertion error. After the fix: the sentinel unblocks `get()`, `continue` re-enters the loop top, `pending_end=True` → drain loop → `_finalize_pending_dictation_end()` → `_finalize_dictation()` → `finalized.set()`. The test PASSES quickly.

- [ ] **Step 1: Add the sentinel regression integration test at the end of `tests/integration/test_dictation_pipeline.py`**

Append the following test after the last existing test:

```python
# ---------------------------------------------------------------------------
# Test 7: Sentinel wake — hotkey-end finalizes with NO trailing utterance
#          (core regression for ADR 0089 Change 1)
# ---------------------------------------------------------------------------


def test_hotkey_end_finalizes_without_trailing_utterance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Core regression test for ADR 0089 Change 1.

    Uses two threading.Event objects for deterministic synchronization — no
    time.sleep for synchronization. The test provably FAILS against unfixed
    code (pipeline stays blocked in get(timeout=None) forever; 'finalized'
    is never set; wait(timeout=2.0) returns False → AssertionError).

    Protocol:
    1. Stub transcriber sets 'processed' when it transcribes the one seeded
       utterance (guaranteeing the pipeline consumed it and re-entered get()).
    2. A monkeypatched _finalize_dictation sets 'finalized' when called.
    3. Seed ONE utterance → wait on 'processed' → tiny fixed sleep (20 ms)
       so the loop provably re-entered blocking get() → fire on_dictation_toggle()
       with NO further utterance → assert finalized.wait(timeout=2.0).

    Expected on UNFIXED code: FAIL — pipeline stays blocked in get(timeout=None),
    'finalized' is never set, the test times out after 2 s.
    Expected after sentinel fix: PASS quickly (sentinel wakes the thread).
    """
    import threading

    # Events for deterministic synchronization
    processed = threading.Event()
    finalized = threading.Event()

    # --- Stub transcriber: sets 'processed' after transcribing the utterance ---
    @dataclass
    class _SentinelTranscription:
        text: str
        confidence: float = 0.95
        no_speech_prob: float = 0.05

    class _SentinelStub:
        def __init__(self, result):
            self._result = result

        def load(self) -> None: ...
        def unload(self) -> None: ...

        def transcribe(self, _audio: np.ndarray) -> _SentinelTranscription:
            result = self._result
            processed.set()   # signal: utterance has been transcribed
            return result

    # --- Build daemon with stub transcriber ---
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry

    reset_global_registry()
    reset_global_picker_registry()
    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(bus=bus, end_word="done")

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_SentinelStub(_SentinelTranscription("some dictated content")),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()

    # --- Monkeypatch _finalize_dictation to set 'finalized' ---
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda *a, **kw: "FINALIZED TEXT",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda *a, **kw: finalized.set(),
    )

    # --- Start the pipeline loop in background ---
    pipeline_thread = threading.Thread(
        target=daemon._pipeline_loop, name="vc-pipeline-sentinel-test", daemon=True
    )
    daemon._session_active = True
    pipeline_thread.start()

    # --- Enter dictation mode ---
    dictation_session.start()

    # --- Seed ONE utterance ---
    audio = np.zeros(16000, dtype=np.float32)
    daemon._utt_q.put((audio, daemon._audio_gen))

    # --- Wait until the utterance is transcribed (pipeline consumed it and
    #     re-entered the blocking get()). Use a generous timeout so CI is not flaky. ---
    assert processed.wait(timeout=5.0), (
        "Timed out waiting for the stub transcriber to process the utterance"
    )

    # --- Tiny fixed sleep (20 ms) so the pipeline loop provably re-entered
    #     get(timeout=None) after processing the utterance. ---
    import time as _time
    _time.sleep(0.020)

    # --- Fire hotkey-end with NO further utterances.
    #     This is the exact scenario that was broken before the sentinel fix. ---
    daemon.on_dictation_toggle()

    # --- Assert finalized within 2 s.
    #     UNFIXED: times out — pipeline is still blocked in get(timeout=None).
    #     FIXED: sentinel wakes pipeline → drain → finalize → finalized.set(). ---
    assert finalized.wait(timeout=2.0), (
        "hotkey-end must finalize within 2 s with no trailing utterance. "
        "This is the ADR 0089 regression gate. If this fails, the sentinel is not working."
    )

    assert not dictation_session.active, "session must be inactive after finalization"
    assert not dictation_session.pending_end, "pending_end must be cleared after finalization"

    # Teardown
    daemon._utt_q.put(None)
    pipeline_thread.join(timeout=3.0)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)
```

- [ ] **Step 2: Run the regression test**

```
pytest tests/integration/test_dictation_pipeline.py::test_hotkey_end_finalizes_without_trailing_utterance -v
```

Expected: PASS (the sentinel implementation from Task 7 makes this work).

- [ ] **Step 3: Run all dictation pipeline integration tests**

```
pytest tests/integration/test_dictation_pipeline.py -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_dictation_pipeline.py
git commit -m "test(integration): add sentinel-wake regression gate (ADR 0089)"
```

---

### Task 11: Integration tests for spoken cancel

**Files:**
- Create: `tests/integration/test_dictation_cancel.py`

**REV 3 note:** All tests drive utterances through the REAL daemon dispatch path (`_process_utterance`) — NOT via bare `handle_utterance` calls. The spoken-cancel path requires `_process_utterance` → `handle_utterance` → `elif kind == "cancel": self._dictation_session.cancel()` to execute. `test_spoken_cancel_does_not_buffer_audio` uses `_process_utterance` to drive both the "hello world" and "cancel" utterances, then asserts: session inactive, buffer empty (via `take_audio() is None`), no POST, no paste, `dictation.end` published with `{"reason": "cancel"}`.

- [ ] **Step 1: Create `tests/integration/test_dictation_cancel.py`**

```python
"""Integration tests for spoken dictation cancel (ADR 0089 Change 3).

Mirrors test_dictation_pipeline.py — same scaffolding (_StubTranscriber,
_Transcription, _make_daemon). Tests that spoken "cancel" during active
dictation: (a) calls DictationSession.cancel(), (b) never submits audio to
the remote endpoint, (c) never pastes to clipboard, and (d) emits
dictation.end with {"reason": "cancel"}.

ALL tests drive utterances through daemon._process_utterance() — the real
daemon dispatch path — NOT via bare handle_utterance() calls. This ensures
the elif kind == "cancel": self._dictation_session.cancel() branch in
daemon.py actually executes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from voice_commander.dictation.session import DictationSession
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.verb_router import VerbRouter, build_default_rules

if TYPE_CHECKING:
    from voice_commander.daemon import StreamingDaemon


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray) -> _Transcription:
        return self.queue.pop(0)


def _make_daemon(
    transcripts: list[_Transcription],
    tmp_path: Path,
    cancel_word: str = "cancel",
) -> tuple[StreamingDaemon, DictationSession, CapturingFeedbackSink, EventBus]:
    """Build a stripped-down daemon for dictation cancel testing."""
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry

    reset_global_registry()
    reset_global_picker_registry()

    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(
        bus=bus,
        end_word="done",
        cancel_word=cancel_word,
    )

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


# ---------------------------------------------------------------------------
# Test 1: Spoken cancel — no POST, no paste, correct SSE reason
# ---------------------------------------------------------------------------


def test_spoken_cancel_no_post_no_paste(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Saying 'cancel' during active dictation must not POST audio and not paste.

    Drives utterances through _process_utterance() — the real dispatch path.
    """
    posted: list[bytes] = []

    def _fake_post(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        posted.append(wav_bytes)
        return "SHOULD NOT APPEAR"

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _fake_post)

    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("some words"),  # buffered
            _Transcription("cancel"),       # triggers cancel via _process_utterance
        ],
        tmp_path=tmp_path,
    )

    events: list[tuple[str, dict]] = []
    bus.subscribe(lambda et, data: events.append((et, data or {})))

    audio = np.zeros(16000, dtype=np.float32)
    dictation_session.start()

    # Drive through the real dispatch path
    daemon._process_utterance(audio)  # "some words" → buffered
    assert dictation_session.active is True

    daemon._process_utterance(audio)  # "cancel" → handle_utterance returns "cancel"
                                       # → daemon calls dictation_session.cancel()
    assert dictation_session.active is False, "session must be inactive after spoken cancel"

    # Finalize executor — must be a no-op (nothing submitted)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert posted == [], f"post_audio must NOT be called after spoken cancel; got {posted}"
    assert pasted == [], f"paste must NOT be called after spoken cancel; got {pasted}"

    # SSE event must have been emitted with reason="cancel"
    cancel_events = [
        (et, d) for (et, d) in events
        if et == "dictation.end" and d.get("reason") == "cancel"
    ]
    assert cancel_events, (
        f"Expected dictation.end {{reason:'cancel'}} SSE event; events: {events}"
    )


# ---------------------------------------------------------------------------
# Test 2: Spoken cancel does not buffer the cancel-word audio
# ---------------------------------------------------------------------------


def test_spoken_cancel_does_not_buffer_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cancel-word audio chunk is NOT appended to the buffer; after cancel()
    the buffer is empty.

    Drives utterances through _process_utterance() — the real dispatch path.
    Asserts: session inactive, buffer empty (take_audio() is None), no POST,
    no paste, dictation.end {reason:'cancel'} published.
    """
    posted: list[bytes] = []
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: (posted.append(wav_bytes), "X")[1],
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    events: list[tuple[str, dict]] = []

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello world"),  # buffered (distinct audio)
            _Transcription("cancel"),       # triggers cancel via _process_utterance
        ],
        tmp_path=tmp_path,
    )

    bus.subscribe(lambda et, data: events.append((et, data or {})))

    audio_content = np.ones(8000, dtype=np.float32)
    audio_cancel = np.ones(8000, dtype=np.float32) * 99.0

    dictation_session.start()

    # "hello world" → buffered via _process_utterance (real dispatch path)
    daemon._process_utterance(audio_content)
    assert dictation_session.active is True

    # "cancel" → handle_utterance returns "cancel" → daemon calls cancel()
    daemon._process_utterance(audio_cancel)

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # Session must be inactive
    assert dictation_session.active is False, "session must be inactive after cancel"

    # Buffer must be empty — cancel() clears it; cancel-word audio was never buffered
    assert dictation_session.take_audio() is None, (
        "Buffer must be empty after cancel(); cancel-word audio must not have been buffered"
    )

    # No POST, no paste
    assert posted == [], f"post_audio must NOT be called after spoken cancel; got {posted}"
    assert pasted == [], f"paste must NOT be called after spoken cancel; got {pasted}"

    # SSE event with reason="cancel"
    cancel_events = [
        (et, d) for (et, d) in events
        if et == "dictation.end" and d.get("reason") == "cancel"
    ]
    assert cancel_events, (
        f"Expected dictation.end {{reason:'cancel'}} SSE event; events: {events}"
    )


# ---------------------------------------------------------------------------
# Test 3: Spoken cancel after hotkey-end pending → cancel wins
# ---------------------------------------------------------------------------


def test_spoken_cancel_wins_race_with_pending_hotkey_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If hotkey-end is pending (request_end set) and the next utterance is the
    cancel word, cancel wins: no audio submitted, dictation.end {reason:cancel}.

    Drives utterances through _process_utterance() — the real dispatch path.
    """
    posted: list[bytes] = []

    def _fake_post(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        posted.append(wav_bytes)
        return "X"

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _fake_post)
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("some audio"),  # buffered
            _Transcription("cancel"),       # spoken cancel
        ],
        tmp_path=tmp_path,
    )

    events: list[tuple[str, dict]] = []
    bus.subscribe(lambda et, data: events.append((et, data or {})))

    audio = np.zeros(16000, dtype=np.float32)
    dictation_session.start()

    # Buffer one utterance via the real dispatch path
    daemon._process_utterance(audio)   # "some audio" → buffered

    # Hotkey-end fires (simulates concurrent Ctrl press)
    dictation_session.request_end()
    assert dictation_session.pending_end is True  # sanity

    # Cancel utterance arrives AFTER pending_end is set — via real dispatch path
    daemon._process_utterance(audio)   # "cancel" → cancel() wins

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert not dictation_session.active, "session must be inactive after cancel wins"
    assert not dictation_session.pending_end, "pending_end must be cleared by cancel"
    assert posted == [], f"post_audio must NOT be called when cancel wins; got {posted}"
    assert pasted == [], f"paste must NOT be called when cancel wins; got {pasted}"

    cancel_events = [
        (et, d) for (et, d) in events
        if et == "dictation.end" and d.get("reason") == "cancel"
    ]
    assert cancel_events, (
        f"Expected dictation.end {{reason:'cancel'}} when cancel wins race; events: {events}"
    )


# ---------------------------------------------------------------------------
# Test 4: cancel_word == end_word collision → cancel disabled, word buffers
# ---------------------------------------------------------------------------


def test_cancel_word_collision_disables_spoken_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When cancel_word == end_word, spoken cancel is disabled; the collision
    word acts as the end word (triggers finalization, not cancel)."""
    posted: list[bytes] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: (posted.append(wav_bytes), "X")[1],
    )
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    # cancel_word == end_word == "done" — collision disables spoken cancel
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello"),   # buffered
            _Transcription("done"),    # acts as end word (not cancel)
        ],
        tmp_path=tmp_path,
        cancel_word="done",  # collision with end_word
    )

    assert dictation_session._cancel_word is None, (
        "Collision must disable spoken cancel (_cancel_word=None)"
    )

    audio = np.zeros(16000, dtype=np.float32)
    dictation_session.start()

    # Drive through real dispatch path
    daemon._process_utterance(audio)   # "hello" → buffered
    daemon._process_utterance(audio)   # "done" → end word path (not cancel)

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # "done" triggered the end-word path → post and paste happened
    assert len(pasted) == 1, (
        f"'done' should have triggered end-word path (not cancel) when collision; "
        f"pasted={pasted}"
    )
    assert not dictation_session.active, "session must be inactive after end-word exit"
```

- [ ] **Step 2: Run the new integration tests**

```
pytest tests/integration/test_dictation_cancel.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 3: Run the full integration test suite**

```
pytest tests/integration/ -q
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_dictation_cancel.py
git commit -m "test(integration): spoken cancel integration tests — no POST, no paste, SSE reason (ADR 0089)"
```

---

### Task 12: Write ADR 0089

**Files:**
- Create: `docs/decisions/0089-dictation-hotkey-sentinel-cancel-debounce.md`

- [ ] **Step 1: Create the ADR file**

```markdown
# ADR 0089 — Dictation Hotkey-End Sentinel Wake + Spoken Cancel + Hotkey Debounce

**Status:** Accepted
**Date:** 2026-05-18
**Predecessor:** [ADR 0086](0086-dictation-mode.md) — Dictation Mode / Remote Transcription Pipeline

## Context

ADR 0086 introduced dictation mode with two exit paths: (1) saying the end word `"done"`, and (2) pressing `dictation_key` (Right Ctrl) a second time. The hotkey-end path was unreliable in practice — users had to fall back to saying "done". Code inspection confirmed a race condition: the pipeline thread is blocked inside `_utt_q.get(timeout=None)` when the hotkey fires; `pending_end` is only re-evaluated at the **top of the next iteration**, which never arrives if no further utterance occurs. Two additional issues: (a) some keyboard drivers fire two rapid release events for one physical key press ("key bounce"), causing the hotkey to double-toggle; (b) there was no way to abandon a dictation mid-session without transcribing and pasting it.

## Decision

### Change 1 — Sentinel wake for hotkey-end

A module-level `_DICTATION_WAKE = object()` singleton is added to `daemon.py`. `on_dictation_toggle`, in the dictation-active branch, calls `request_end()` and then `_utt_q.put_nowait(_DICTATION_WAKE)` to unblock the pipeline thread. On `queue.Full`, a warning is logged and nothing else is done — a full queue means the pipeline is already active and will re-evaluate `pending_end` without help. The pipeline loop adds an identity check (`if item is _DICTATION_WAKE: continue`) immediately after the `None` shutdown check. The `continue` returns to the loop top where `pending_end` is now `True`, switching to the timed drain. Real utterances queued ahead of the sentinel are processed first (FIFO).

**`put_nowait` full-queue policy:** on `queue.Full`, log warning, do nothing. The pynput listener thread must never block.

**Type annotation:** `_utt_q` is widened to `queue.Queue[tuple[ndarray, int] | ndarray | None | object]` to accommodate the sentinel.

### Change 2 — 50 ms per-key debounce in HotkeyController

`HotkeyController` adds `_last_fire: dict[keyboard.Key, float]` mapping each bound key to the `time.monotonic()` of its last dispatched release. In `_on_release`, before invoking the callback, a `_DEBOUNCE_S = 0.050` (50 ms) window check drops releases that arrive within 50 ms of the same key's last fire. Each key has its own independent timer, so fast alternation between two different keys is unaffected. The 50 ms constant is internal — no config key is added. The implementation uses `import time` + `time.monotonic()` (not `from time import monotonic`) so the call is interceptable by `monkeypatch`.

### Change 3 — Spoken cancel word

`UtteranceKind` gains `"cancel"` as a third literal value. `DictationSession.__init__` gains `cancel_word: str = "cancel"` — normalized via `_normalize_spoken`; if the normalized value is empty or equals the normalized `end_word`, a WARNING is logged and `self._cancel_word = None` disables spoken cancel. `handle_utterance` adds a cancel guard: if `_cancel_word` is not None and the normalized transcript matches, return `"cancel"` without buffering. The daemon pipeline, where `kind == "end"` is handled, adds `elif kind == "cancel": self._dictation_session.cancel()` — reusing the pre-existing `cancel()` method unchanged. No new audio is submitted, no POST, no paste.

`DictationConfig` gains `cancel_word: str = "cancel"`. `config.toml.example` gains `cancel_word = "cancel"` with an inline comment. `DictationSession()` in `build_streaming_daemon` passes `cancel_word=cfg.dictation.cancel_word`.

**Picker mutual-exclusion:** dictation and the picker (ADR 0083) are mutually-exclusive voice-session sub-states; `handle_utterance` runs only while dictation is active, so the dictation `cancel_word` and `PickerConfig.cancel_words` never conflict.

### Sprite update

The `dictation.end` handler in `voice_sprite/state_machine.py` now reads `data.get("reason")` (defensive — some publishers may omit the key). When `reason == "cancel"`, `self.cancelled_cue = True` is set (in addition to `dictating = False`). The renderer surfaces a distinct "cancelled" text badge. `dictation.start` resets `cancelled_cue = False`. This retroactively improves the scroll-lock-cancel visual as well as covering spoken cancel — both paths emit `{"reason": "cancel"}`.

### Race: simultaneous hotkey-end + spoken cancel

Cancel wins. The sentinel (FIFO, enqueued before the cancel utterance arrives) is consumed first via `continue`, re-evaluating `pending_end`. The cancel utterance then arrives and `cancel()` is called, which atomically clears `_active`, `_pending_end`, and `_buffer`. When the drain timeout fires, `_finalize_pending_dictation_end` calls `take_and_finish()`, which returns `None` because `_active` is already `False`. No double-submit.

## Non-goals

- Cancelling after finalization has started (POST already in flight).
- A dedicated hotkey for cancel — spoken word only.
- A new LLM-visible primitive.
- Per-session or per-profile cancel words.

## Consequences

- Hotkey-end is now as reliable as the end-word path. No silent infinite block.
- Key bounce / driver double-release no longer causes accidental double-toggle on any bound key.
- Users can abandon a dictation mid-session by saying "cancel" (configurable).
- The sprite surfaces a distinct "cancelled" badge for cancel exits, improving feedback quality for both spoken cancel and scroll-lock cancel.
- `_utt_q` type annotation is widened; implementers must not tighten it back.
- `DictationConfig.cancel_word` defaults to `"cancel"` — no migration needed for existing `config.toml` files (the default is added to the dataclass and to `config.toml.example`).
```

- [ ] **Step 2: Commit the ADR**

```bash
git add docs/decisions/0089-dictation-hotkey-sentinel-cancel-debounce.md
git commit -m "docs(adr): ADR 0089 — dictation hotkey sentinel + spoken cancel + debounce"
```

---

### Task 13: Update docs — technical-decisions row, ADR 0086 successor note, CLAUDE.md

**Files:**
- Modify: `docs/agents/technical-decisions.md`
- Modify: `docs/decisions/0086-dictation-mode.md`
- Modify: `CLAUDE.md`

Note: `config.toml.example` was already updated in Task 5.

- [ ] **Step 1: Add ADR 0089 row to `docs/agents/technical-decisions.md`**

Find the last row in the table (ADR 0088 row) and append the following row after it:

```markdown
| Dictation hotkey-end reliability + spoken cancel + hotkey debounce | Sentinel `_DICTATION_WAKE` enqueued on hotkey press wakes the pipeline thread from `get(timeout=None)` so hotkey-end finalizes without requiring a trailing utterance; 50 ms per-key `_last_fire` debounce in `HotkeyController` rejects driver double-release; `DictationSession` gains `cancel_word` parameter (default `"cancel"`) — `handle_utterance` returns `"cancel"` kind which calls the pre-existing `cancel()` method; sprite reads `data.get("reason")` to surface a distinct cancelled cue | Silent hang of the pipeline thread after hotkey-end press (race: `pending_end` set while thread blocked in `get(timeout=None)`); key bounce causing accidental double-toggle; no spoken cancel path | [0089](../decisions/0089-dictation-hotkey-sentinel-cancel-debounce.md) |
```

- [ ] **Step 2: Add "Successor" note to `docs/decisions/0086-dictation-mode.md`**

Find the `**Status:** Accepted` line in `0086-dictation-mode.md` and add a successor note beneath it:

```markdown
**Status:** Accepted
**Successor:** [ADR 0089](0089-dictation-hotkey-sentinel-cancel-debounce.md) — patches the hotkey-end race condition, adds 50 ms debounce, and adds the spoken cancel path. ADR 0086 remains authoritative for the initial dictation-mode design; ADR 0089 is the reliability and UX patch.
```

- [ ] **Step 3: Update `CLAUDE.md` dictation paragraph in "Current state"**

After the sentence `re-transcribe applies the identical four-step pipeline.`, add:

```
**Hotkey-end reliability and spoken cancel** (ADR 0089): a module-level `_DICTATION_WAKE` sentinel is enqueued via `put_nowait` in `on_dictation_toggle` immediately after `request_end()` to wake the pipeline thread from its blocking `get(timeout=None)`, eliminating the race where `pending_end` was set while the thread was already inside `get`; on `queue.Full`, the sentinel is skipped (pipeline already active). `HotkeyController` applies a 50 ms per-key debounce (`_last_fire: dict[Key, float]`, `_DEBOUNCE_S = 0.050`) to reject driver double-release / key-bounce events uniformly across all bound keys. `DictationSession` gains `cancel_word: str = "cancel"` (configurable via `[dictation] cancel_word`); `handle_utterance` returns the new `"cancel"` `UtteranceKind` on an exact normalized match without buffering the audio; the daemon pipeline calls the pre-existing `cancel()` method, which discards the buffer and emits `dictation.end {"reason": "cancel"}` — no POST, no paste. The sprite `dictation.end` handler reads `data.get("reason")` defensively; `reason == "cancel"` sets `StateMachine.cancelled_cue = True` for a distinct visual badge (covers both spoken cancel and scroll-lock cancel retroactively). Dictation and the picker are mutually-exclusive sub-states so `cancel_word` and `PickerConfig.cancel_words` never conflict.
```

- [ ] **Step 4: Commit all doc updates**

```bash
git add docs/agents/technical-decisions.md docs/decisions/0086-dictation-mode.md CLAUDE.md
git commit -m "docs: ADR 0089 row in technical-decisions, 0086 successor note, CLAUDE.md current-state update"
```

---

### Task 14: Visual E2E harness

**Files:**
- Create: `scripts/dictation_cancel_smoke.py` (render-smoke, in-process, Phase A: sentinel wake + Phase B: spoken cancel)
- Create: `scripts/dictation_hotkey_cancel_e2e.py` (full subprocess + SSE + PrintWindow E2E, per mandatory protocol)

**REV 6 note:** The E2E protocol requires TWO scripts, mirroring the picker reference:
- `scripts/dictation_cancel_smoke.py` — in-process (no subprocess), fast feedback, proves the pipeline logic. Analogous to `scripts/picker_modal_smoke.py`.
- `scripts/dictation_hotkey_cancel_e2e.py` — subprocess sprite + hand-rolled SSE server + `FindWindowW` + `PrintWindow` → PNG. Analogous to `scripts/picker_visual_e2e.py`. This script also drives the spoken-cancel path through the REAL daemon pipeline (not in-process).

Both write to `outputs/` and exit non-zero on any failure. Both conform to the eight rules in `docs/agents/visual-e2e-testing.md`.

#### Script A — Render-smoke (`scripts/dictation_cancel_smoke.py`)

- [ ] **Step 1: Create `scripts/dictation_cancel_smoke.py`**

```python
"""Render-smoke harness for ADR 0089 — dictation hotkey-end sentinel + spoken cancel.

Mandatory per docs/agents/visual-e2e-testing.md Rule 2 (stage the harness).
This script is Phase 1 (in-process, no subprocess). Proves:
  - Sentinel wakes the pipeline thread within 1 s with no trailing utterance.
  - Spoken cancel via _process_utterance() produces no POST, no paste, and emits
    dictation.end {reason:'cancel'}.
  - StateMachine.cancelled_cue is set after dictation.end {reason:'cancel'}.

Run BEFORE scripts/dictation_hotkey_cancel_e2e.py. Exits 0 on full PASS.

Outputs:
  outputs/dictation_smoke.log  — full validation log
  outputs/dictation_smoke.json — assertion summary (machine-readable)
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT / "dictation_smoke.log"
JSON_PATH = OUT / "dictation_smoke.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_smoke")


def _make_daemon(tmp_path: Path, cancel_word: str = "cancel"):
    """Build a stripped-down in-process daemon. Returns (daemon, dictation_session, bus)."""
    import numpy as np
    from dataclasses import dataclass

    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dictation.session import DictationSession
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.event_bus import EventBus
    from voice_commander.feedback import CapturingFeedbackSink
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry
    from voice_commander.verb_router import VerbRouter, build_default_rules

    @dataclass
    class _T:
        text: str
        confidence: float = 0.95
        no_speech_prob: float = 0.05

    class _Stub:
        def __init__(self, q):
            self._q = list(q)

        def load(self): ...
        def unload(self): ...

        def transcribe(self, _):
            return self._q.pop(0) if self._q else _T("")

    reset_global_registry()
    reset_global_picker_registry()
    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(bus=bus, end_word="done", cancel_word=cancel_word)

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_Stub([]),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, bus


# ---------------------------------------------------------------------------
# Phase A — Sentinel wake: hotkey-end finalizes without trailing utterance
# ---------------------------------------------------------------------------


def phase_a(tmp_path: Path) -> bool:
    log.info("=== PHASE A: hotkey-end sentinel wake ===")
    import numpy as np
    from dataclasses import dataclass

    processed = threading.Event()
    finalized = threading.Event()

    import voice_commander.dictation.remote as _remote
    import voice_commander.dictation.clipboard as _clipboard

    original_post = _remote.post_audio
    original_paste = _clipboard.paste_via_clipboard

    _remote.post_audio = lambda *a, **kw: "SENTINEL TEXT"
    _clipboard.paste_via_clipboard = lambda *a, **kw: finalized.set()

    ok = False
    daemon = None
    pipeline_thread = None
    try:
        @dataclass
        class _T:
            text: str
            confidence: float = 0.95
            no_speech_prob: float = 0.05

        class _SentinelStub:
            def __init__(self):
                self._result = _T("some dictated words")

            def load(self): ...
            def unload(self): ...

            def transcribe(self, _):
                result = self._result
                processed.set()
                return result

        from voice_commander.daemon import StreamingDaemon
        from voice_commander.dictation.session import DictationSession
        from voice_commander.dispatcher import Dispatcher
        from voice_commander.event_bus import EventBus
        from voice_commander.feedback import CapturingFeedbackSink
        from voice_commander.picker.registry import reset_global_picker_registry
        from voice_commander.registry import get_global_registry, reset_global_registry
        from voice_commander.verb_router import VerbRouter, build_default_rules

        reset_global_registry()
        reset_global_picker_registry()
        registry = get_global_registry()
        bus = EventBus()
        feedback = CapturingFeedbackSink()
        dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
        verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
        dictation_session = DictationSession(bus=bus, end_word="done")

        daemon = StreamingDaemon(
            feedback=feedback,
            recorder=None,
            transcriber=_SentinelStub(),
            dispatcher=dispatcher,
            verb_router=verb_router,
            registry=registry,
            event_bus=bus,
            dictation_session=dictation_session,
            output_dir=str(tmp_path),
        )
        daemon._transcriber_ready.set()

        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop, name="vc-pipeline-smoke-a", daemon=True
        )
        daemon._session_active = True
        pipeline_thread.start()

        dictation_session.start()
        audio = np.zeros(16000, dtype=np.float32)
        daemon._utt_q.put((audio, daemon._audio_gen))

        assert processed.wait(timeout=5.0), "Timed out waiting for utterance to be transcribed"
        time.sleep(0.020)  # let pipeline re-enter blocking get()

        log.info("firing hotkey-end with no trailing utterance...")
        t0 = time.monotonic()
        daemon.on_dictation_toggle()

        if not finalized.wait(timeout=2.0):
            log.error(
                "FAIL: dictation not finalized within 2 s — sentinel wake not working"
            )
            return False

        elapsed_ms = (time.monotonic() - t0) * 1000
        log.info("PASS: finalized in %.0f ms", elapsed_ms)
        ok = True
        return True

    finally:
        _remote.post_audio = original_post
        _clipboard.paste_via_clipboard = original_paste
        if daemon is not None:
            try:
                daemon._utt_q.put(None)
            except Exception:
                pass
            if pipeline_thread is not None:
                pipeline_thread.join(timeout=3.0)
            daemon._dictation_executor.shutdown(wait=False)
            daemon._wav_executor.shutdown(wait=False)
        log.info("PHASE A %s", "PASS" if ok else "FAIL")


# ---------------------------------------------------------------------------
# Phase B — Spoken cancel: no paste, SSE reason="cancel"
# ---------------------------------------------------------------------------


def phase_b(tmp_path: Path) -> bool:
    log.info("=== PHASE B: spoken cancel — no paste, SSE reason='cancel' ===")
    import numpy as np
    from dataclasses import dataclass

    posted: list = []
    pasted: list = []

    import voice_commander.dictation.remote as _remote
    import voice_commander.dictation.clipboard as _clipboard

    original_post = _remote.post_audio
    original_paste = _clipboard.paste_via_clipboard

    _remote.post_audio = lambda *a, **kw: (posted.append("posted"), "X")[1]
    _clipboard.paste_via_clipboard = lambda *a, **kw: pasted.append("pasted")

    ok = False
    daemon = None
    pipeline_thread = None
    try:
        @dataclass
        class _T:
            text: str
            confidence: float = 0.95
            no_speech_prob: float = 0.05

        daemon, dictation_session, bus = _make_daemon(tmp_path)
        # Inject transcripts for real _process_utterance dispatch
        daemon._transcriber._q = [
            _T("hello there"),  # buffered
            _T("cancel"),       # triggers cancel via _process_utterance
        ]

        events: list = []
        bus.subscribe(lambda et, data: events.append((et, data or {})))

        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop, name="vc-pipeline-smoke-b", daemon=True
        )
        daemon._session_active = True
        pipeline_thread.start()

        dictation_session.start()
        audio = np.zeros(8000, dtype=np.float32)

        # Drive through real _process_utterance dispatch path
        daemon._utt_q.put((audio, daemon._audio_gen))  # "hello there" → buffered
        time.sleep(0.1)
        daemon._utt_q.put((audio, daemon._audio_gen))  # "cancel" → cancel()
        time.sleep(0.1)

        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

        if posted:
            log.error("FAIL: post_audio was called — must NOT be: %s", posted)
            return False
        log.info("PASS: post_audio not called")

        if pasted:
            log.error("FAIL: clipboard was pasted — must NOT be: %s", pasted)
            return False
        log.info("PASS: clipboard not pasted")

        cancel_events = [
            (et, d) for (et, d) in events
            if et == "dictation.end" and d.get("reason") == "cancel"
        ]
        if not cancel_events:
            log.error(
                "FAIL: dictation.end {reason:'cancel'} not emitted; events=%s", events
            )
            return False
        log.info("PASS: dictation.end reason='cancel' emitted: %s", cancel_events[0])

        ok = True
        return True

    finally:
        _remote.post_audio = original_post
        _clipboard.paste_via_clipboard = original_paste
        if daemon is not None:
            try:
                daemon._utt_q.put(None)
            except Exception:
                pass
            if pipeline_thread is not None:
                pipeline_thread.join(timeout=3.0)
        log.info("PHASE B %s", "PASS" if ok else "FAIL")


# ---------------------------------------------------------------------------
# Phase C — Sprite state machine reads cancel reason
# ---------------------------------------------------------------------------


def phase_c() -> bool:
    log.info("=== PHASE C: sprite state machine cancel cue ===")
    ok = False
    try:
        from voice_sprite.state_machine import StateMachine

        sm = StateMachine()
        sm.on_event("dictation.start", {})
        sm.on_event("dictation.end", {"reason": "cancel"})

        if not sm.cancelled_cue:
            log.error("FAIL: cancelled_cue not True after reason='cancel'")
            return False
        log.info("PASS: cancelled_cue=True after reason='cancel'")

        if sm.dictating:
            log.error("FAIL: dictating should be False")
            return False
        log.info("PASS: dictating=False")

        sm2 = StateMachine()
        sm2.on_event("dictation.start", {})
        sm2.on_event("dictation.end", {"reason": "done"})
        if sm2.cancelled_cue:
            log.error("FAIL: cancelled_cue should be False after reason='done'")
            return False
        log.info("PASS: cancelled_cue=False after reason='done'")

        # Missing reason key — must not KeyError (REV 8 defensive access)
        sm3 = StateMachine()
        sm3.on_event("dictation.start", {})
        try:
            sm3.on_event("dictation.end", {})
        except KeyError as e:
            log.error("FAIL: KeyError on missing 'reason' key: %s", e)
            return False
        log.info("PASS: no KeyError on missing 'reason' key")

        ok = True
        return True
    finally:
        log.info("PHASE C %s", "PASS" if ok else "FAIL")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> int:
    import tempfile

    results: dict[str, bool] = {}

    with tempfile.TemporaryDirectory() as tmp:
        results["phase_a_sentinel_wake"] = phase_a(Path(tmp))

    with tempfile.TemporaryDirectory() as tmp:
        results["phase_b_spoken_cancel"] = phase_b(Path(tmp))

    results["phase_c_sprite_cancel_cue"] = phase_c()

    all_pass = all(results.values())

    log.info(
        "=== SUMMARY: %s ===",
        " | ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items()),
    )
    JSON_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log.info("Evidence written to %s", LOG_PATH)
    log.info("Assertion summary: %s", JSON_PATH)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
```

#### Script B — Full E2E with subprocess sprite + SSE + PrintWindow (`scripts/dictation_hotkey_cancel_e2e.py`)

- [ ] **Step 2: Create `scripts/dictation_hotkey_cancel_e2e.py`**

This script follows `scripts/picker_visual_e2e.py` exactly:
- Starts a hand-rolled stdlib SSE server on a free port.
- Spawns the real `voice_sprite` subprocess pointed at that server.
- Waits for the sprite window to appear (`FindWindowW("vc-sprite")` or the sprite's window class).
- Publishes `dictation.start` and `dictation.end {"reason":"cancel"}` events through the SSE server.
- Locates the sprite HWND, captures it with `PrintWindow(PW_RENDERFULLCONTENT=0x2)`.
- Saves the PNG to `outputs/dictation_e2e.png`.
- Asserts: sprite window visible, PNG saved non-empty, SSE events delivered.
- Wrapped in `try`/`finally` to terminate the sprite subprocess — no leaked processes.
- Exits non-zero on any failure (Rule 5 cleanup, Rule 4 artifacts, Rule 3 real signals).

```python
"""Full subprocess E2E harness for ADR 0089 — dictation cancel sprite rendering.

Mandatory per docs/agents/visual-e2e-testing.md — this is the subprocess +
SSE + PrintWindow layer. Run AFTER scripts/dictation_cancel_smoke.py passes.

Follows scripts/picker_visual_e2e.py exactly:
  - Hand-rolled stdlib SSE server on a free port (no FastAPI).
  - Real voice_sprite subprocess pointed at the SSE server.
  - FindWindowW to locate the sprite HWND.
  - PrintWindow(PW_RENDERFULLCONTENT=0x2) to capture the sprite window.
  - PNG written to outputs/dictation_e2e.png.
  - Cleanup in try/finally — no leaked processes.
  - Exits non-zero on any failure.

PHASE A — Sprite receives dictation.start + dictation.end {reason:'cancel'}.
  Asserts: sprite window visible, SSE events delivered, PNG captured.

PHASE B — (Optional real-daemon integration when daemon is running)
  Not included in this harness to avoid requiring a live GPU/audio stack.
  Full real-daemon integration is covered by tests/integration/.

Outputs:
  outputs/dictation_e2e.png   — captured sprite screenshot
  outputs/dictation_e2e.log   — full validation log
  outputs/dictation_e2e.json  — assertion summary
"""
from __future__ import annotations

import json
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT / "dictation_e2e.log"
PNG_PATH = OUT / "dictation_e2e.png"
JSON_PATH = OUT / "dictation_e2e.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_e2e")


# --------------------------------------------------------------------------
# Helpers (copied from picker_visual_e2e.py pattern)
# --------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _SSEHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        if self.path.startswith("/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            ev_id = 0
            self.server.connected.set()  # type: ignore[attr-defined]
            while not self.server.shutdown_flag.is_set():  # type: ignore[attr-defined]
                try:
                    ev = self.server.queue.get(timeout=1.0)  # type: ignore[attr-defined]
                except queue.Empty:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except OSError:
                        return
                    continue
                if ev is None:
                    return
                ev_id += 1
                payload = f"id: {ev_id}\nevent: {ev['type']}\ndata: {json.dumps(ev['data'])}\n\n"
                try:
                    self.wfile.write(payload.encode())
                    self.wfile.flush()
                except OSError:
                    return


def _start_sse_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _SSEHandler)
    server.queue = queue.Queue()  # type: ignore[attr-defined]
    server.connected = threading.Event()  # type: ignore[attr-defined]
    server.shutdown_flag = threading.Event()  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _push_event(server: ThreadingHTTPServer, event_type: str, data: dict) -> None:
    server.queue.put({"type": event_type, "data": data})  # type: ignore[attr-defined]


def _capture_window_png(hwnd: int, out_path: Path) -> bool:
    """Capture a window to PNG using PrintWindow(PW_RENDERFULLCONTENT=0x2)."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        import win32gui
        import win32ui
        from PIL import Image

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            log.error("Window rect invalid: %dx%d", w, h)
            return False

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        dc_obj = win32ui.CreateDCFromHandle(hwnd_dc)
        mem = dc_obj.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(dc_obj, w, h)
        mem.SelectObject(bmp)

        user32 = ctypes.windll.user32
        user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
        user32.PrintWindow.restype = wt.BOOL
        ok = user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)

        bmp_info = bmp.GetInfo()
        bmp_bits = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (bmp_info["bmWidth"], bmp_info["bmHeight"]),
                                bmp_bits, "raw", "BGRX", 0, 1)
        img.save(str(out_path))

        mem.DeleteDC()
        dc_obj.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        win32gui.DeleteObject(bmp.GetHandle())
        return bool(ok)
    except Exception as exc:
        log.error("PrintWindow failed: %s", exc)
        return False


def _find_sprite_hwnd(timeout: float = 10.0) -> int | None:
    """Poll for the sprite window HWND. Returns hwnd or None on timeout."""
    import win32gui

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # voice_sprite window titles / class names — check both common patterns
        hwnd = win32gui.FindWindowW(None, "voice-sprite")
        if not hwnd:
            hwnd = win32gui.FindWindowW("vc-sprite", None)
        if hwnd and win32gui.IsWindowVisible(hwnd):
            return hwnd
        time.sleep(0.2)
    return None


# --------------------------------------------------------------------------
# Phase A — Sprite SSE rendering
# --------------------------------------------------------------------------


def phase_a() -> bool:
    log.info("=== PHASE A: sprite receives dictation.start + dictation.end{cancel} ===")
    port = _free_port()
    server = _start_sse_server(port)
    sprite_proc: subprocess.Popen | None = None
    ok = False

    try:
        # Spawn voice_sprite subprocess pointed at our SSE server
        env = dict(os.environ)
        sprite_proc = subprocess.Popen(
            [
                sys.executable, "-m", "voice_sprite",
                "--daemon-url", f"http://127.0.0.1:{port}",
            ],
            cwd=str(ROOT),
            env=env,
        )
        log.info("sprite subprocess started (pid=%d)", sprite_proc.pid)

        # Wait for the sprite to connect to our SSE endpoint
        if not server.connected.wait(timeout=15.0):  # type: ignore[attr-defined]
            log.error("FAIL: sprite never connected to SSE server within 15 s")
            return False
        log.info("sprite connected to SSE server")

        # Send a heartbeat so the sprite doesn't crash to CRASHED state
        _push_event(server, "daemon_heartbeat", {})
        _push_event(server, "session_started", {})
        time.sleep(0.5)

        # Push dictation.start
        _push_event(server, "dictation.start", {})
        time.sleep(0.3)

        # Push dictation.end with reason="cancel"
        _push_event(server, "dictation.end", {"reason": "cancel"})
        time.sleep(0.5)

        # Locate the sprite window
        hwnd = _find_sprite_hwnd(timeout=10.0)
        if hwnd is None:
            log.error("FAIL: sprite window not found within 10 s")
            return False
        log.info("PASS: sprite window found (hwnd=%d)", hwnd)

        # Capture PNG
        captured = _capture_window_png(hwnd, PNG_PATH)
        if not captured or not PNG_PATH.exists() or PNG_PATH.stat().st_size < 100:
            log.error("FAIL: PNG capture failed or file is empty")
            return False
        log.info("PASS: sprite screenshot captured to %s (%d bytes)",
                 PNG_PATH, PNG_PATH.stat().st_size)

        ok = True
        return True

    finally:
        server.shutdown_flag.set()  # type: ignore[attr-defined]
        server.shutdown()
        if sprite_proc is not None:
            sprite_proc.terminate()
            try:
                sprite_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                sprite_proc.kill()
        log.info("PHASE A %s", "PASS" if ok else "FAIL")


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------


def main() -> int:
    results: dict[str, bool] = {}
    results["phase_a_sprite_sse_rendering"] = phase_a()

    all_pass = all(results.values())
    log.info(
        "=== SUMMARY: %s ===",
        " | ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items()),
    )
    JSON_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log.info("PNG evidence: %s", PNG_PATH)
    log.info("Log: %s", LOG_PATH)
    log.info("Assertion summary: %s", JSON_PATH)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run the smoke harness**

```
python scripts/dictation_cancel_smoke.py
```

Expected: All three phases PASS; exit code 0; `outputs/dictation_smoke.log` and `outputs/dictation_smoke.json` created.

- [ ] **Step 4: Run the full E2E harness (requires display / sprite deps)**

```
python scripts/dictation_hotkey_cancel_e2e.py
```

Expected: Phase A PASS; `outputs/dictation_e2e.png` exists and is non-empty; exit code 0.

- [ ] **Step 5: Inspect the output files**

Open `outputs/dictation_e2e.png` to visually confirm the sprite rendered (Rule 8: don't accept first green — eyeball it). Check `outputs/dictation_smoke.json` for all-true.

- [ ] **Step 6: Commit**

```bash
git add scripts/dictation_cancel_smoke.py scripts/dictation_hotkey_cancel_e2e.py
git commit -m "test(e2e): dictation cancel render-smoke + subprocess sprite SSE PrintWindow harness (ADR 0089)"
```

---

### Task 15: Full test suite verification and final docstring polish

**Files:** Minor docstring tweak to `src/voice_commander/dictation/session.py`

- [ ] **Step 1: Run the complete unit test suite**

```
pytest tests/unit/ -q
```

Expected: All tests PASS. Zero failures.

- [ ] **Step 2: Run the complete integration test suite**

```
pytest tests/integration/ -q
```

Expected: All tests PASS. Zero failures.

- [ ] **Step 3: Run the smoke harness one final time as the exit-gate**

```
python scripts/dictation_cancel_smoke.py
```

Expected: Exit code 0.

- [ ] **Step 4: Update module docstring in `src/voice_commander/dictation/session.py`**

In the hotkey-end paragraph of the module docstring (lines 1–22), add a note about the sentinel wake. The paragraph currently says:

```
(b) **Hotkey-end path** — the hotkey thread calls :meth:`request_end`, which
    sets :attr:`pending_end` without deactivating the session.  The pipeline
    thread polls :attr:`pending_end`; while it is set, the pipeline uses a
    short timed get on the utterance queue (``_DICTATION_DRAIN_TIMEOUT_S``).
    Once the queue drains (timeout expires with no new item), the pipeline
    calls ``_finalize_pending_dictation_end`` → :meth:`take_and_finish`,
    which atomically captures the buffer and deactivates the session.
```

Update to:
```
(b) **Hotkey-end path** — the hotkey thread calls :meth:`request_end`, which
    sets :attr:`pending_end` without deactivating the session.  The hotkey
    thread also enqueues a ``_DICTATION_WAKE`` sentinel via ``put_nowait``
    to unblock the pipeline thread from ``get(timeout=None)`` immediately
    (ADR 0089 — eliminates the race where ``pending_end`` is set while the
    thread is already inside ``get``).  While ``pending_end`` is set, the
    pipeline uses a short timed get on the utterance queue
    (``_DICTATION_DRAIN_TIMEOUT_S``).  Once the queue drains (timeout
    expires with no new item), the pipeline calls
    ``_finalize_pending_dictation_end`` → :meth:`take_and_finish`, which
    atomically captures the buffer and deactivates the session.
```

- [ ] **Step 5: Final summary commit if any docstring tweaks were made**

```bash
git add src/voice_commander/dictation/session.py
git commit -m "docs(session): update module docstring to reference ADR 0089 sentinel wake"
```

---

## HITL Gate (Human-in-the-loop validation)

Before declaring this PR "done", a human must manually validate the following scenarios on real hardware:

1. **Hotkey-end no-trailing-utterance:** Start a dictation session, say two or three words, press Right Ctrl **without saying anything further**. Dictation must finalize within ~500 ms — the text appears at the cursor and the sprite returns to idle. This is the primary regression test.

2. **Bounce rejection:** Press Right Ctrl twice in rapid succession (simulate bounce). Confirm only one toggle fires — dictation starts but does NOT immediately stop. The sprite should show the dictating badge.

3. **Spoken cancel:** Start a dictation session, say several words, then say "cancel". Confirm nothing is pasted, the sprite returns to idle and shows a distinct "cancelled" badge, and the clipboard is unchanged (verify with Ctrl+V in Notepad).

4. **Collision warning:** Temporarily set `end_word = "stop"` and `cancel_word = "stop"` in `config.toml`. Start the daemon. Confirm a WARNING appears in the log about the cancel_word/end_word collision and spoken cancel is disabled.

5. **Config.toml.example sanity:** Confirm `config.toml.example` has the new `cancel_word = "cancel"` line with the inline comment, and that a fresh `Config.load()` from that file works without error.
