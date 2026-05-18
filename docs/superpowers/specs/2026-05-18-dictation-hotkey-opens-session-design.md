# Right Ctrl Dictation Hotkey Opens Its Own Voice Session — Design Spec

**Date:** 2026-05-18
**Status:** Design complete — pending implementation plan
**New ADR:** 0090 — Right Ctrl opens a self-contained dictation session
**Prior art in repo:** ADR 0086 (dictation mode), ADR 0089 (hotkey-end sentinel + spoken cancel + debounce), ADR 0025 (mute key, removed), ADR 0048 (event bus).

---

## Context / Problem

Dictation mode (ADR 0086) is a sub-state of an active Scroll Lock voice session. The
entry point is `on_dictation_toggle` in `src/voice_commander/daemon.py` (line 401).
That method's first guard reads:

```python
# daemon.py line 408-411
if not self._session_active:
    logger.info("on_dictation_toggle: no active session — ignoring")
    self._feedback.on_miss("(dictation: no active session)", ())
    return
```

When no Scroll Lock session is open, pressing the dictation hotkey (Right Ctrl,
`dictation_key`, default `ctrl_r`) plays a miss chime and returns. Users expect Right
Ctrl to start transcription directly — no prior Scroll Lock press required. The current
no-op is the defect; the desired behaviour is the spec.

---

## Goals

1. Pressing Right Ctrl with **no Scroll Lock session open** starts a self-contained
   dictation session: opens the audio pipeline, enters dictation mode, and on the next
   Right Ctrl press (or the configured end word `"done"`) finalizes, inserts pasted
   text at the cursor, and closes the session automatically.
2. Saying the cancel word (`"cancel"`, ADR 0089) during a Ctrl-opened dictation aborts
   and also closes the session automatically.
3. Right Ctrl becomes a complete transcribe toggle — no other hotkey required.
4. The Scroll Lock session open/close path is **behaviour-preserving**: a refactor into
   shared helpers must not change any observable state transition.

---

## Non-Goals

- Right Ctrl pressed **during an existing Scroll Lock session** keeps the current
  behaviour exactly: it toggles dictation as a sub-state; when dictation ends, the
  Scroll Lock session stays open. This is a confirmed user decision, not a defect.
- No new LLM-visible primitive. No new `config.toml` key.
- No change to the transcription pipeline (`transcriber.py`, faster-whisper, whisper.cpp
  remote endpoint). The only changes are in session lifecycle management.

---

## Design

### 1. New daemon state field

Add to `Daemon.__init__`, immediately after `self._session_active: bool = False`
(currently line 249 of `src/voice_commander/daemon.py`):

```python
self._session_opened_by_dictation: bool = False
```

This flag is `True` only when the current voice session was opened by a Right Ctrl
press, not by Scroll Lock. Every close route (helper, shutdown, scroll-lock-close)
resets it to `False`.

---

### 2. Extract shared open/close helpers (refactor, not new behaviour)

The OPEN and CLOSE arms of `on_scroll_lock` (lines 388-399 and 372-387 respectively)
are extracted verbatim into two protected helpers. This is a pure refactor — no
observable behaviour changes for Scroll Lock.

#### `_open_voice_session() -> bool`

Extracted from the `on_scroll_lock` OPEN arm:

```python
def _open_voice_session(self) -> bool:
    """Open the audio pipeline and transition to session_active.

    Thread context: hotkey-listener thread only (called from on_scroll_lock and
    on_dictation_toggle, both of which run exclusively on the pynput listener thread).
    Must not block beyond recorder.open_session().
    Returns True on success, False if the recorder failed (error already surfaced).
    """
    if self._recorder is None:
        logger.warning("_open_voice_session: recorder not initialised; ignoring")
        return False
    self._audio_gen += 1
    try:
        self._recorder.open_session()
        self._session_active = True
        self._feedback.on_recording_start()
        self._publish("session_started")
        self._publish("unmuted")
        logger.info("Session opened")
        return True
    except Exception as e:
        self._session_active = False
        self._feedback.on_error("recorder.open_session", e)
        return False
```

#### `_close_voice_session() -> None`

Extracted from the `on_scroll_lock` CLOSE arm, with one addition — it clears the new
flag before returning:

```python
def _close_voice_session(self) -> None:
    """Close the audio pipeline, drain the utterance queue, and reset state.

    Thread context: hotkey-listener thread OR _dictation_executor thread.
    Idempotent: recorder.close_session() is a no-op when already IDLE.
    Always resets _session_opened_by_dictation to False.
    """
    if self._recorder is None:
        return
    self._audio_gen += 1
    try:
        self._recorder.close_session()
    except Exception:
        logger.exception("close_session() failed")
    self._drain_utt_q()
    self._session_active = False
    self._session_opened_by_dictation = False   # ← always cleared
    if self._dictation_session is not None and self._dictation_session.active:
        self._dictation_session.cancel()
    if self._elements_session is not None and self._elements_session.active:
        self._elements_session.cancel()
    self._feedback.on_recording_stop()
    self._publish("muted")
    self._publish("session_stopped")
    logger.info("Session closed")
```

#### `on_scroll_lock` rewritten to call helpers

```python
def on_scroll_lock(self) -> None:
    """Toggle the voice session on/off.  (docstring unchanged — no behavioural change.)"""
    if self._recorder is None:
        logger.warning("on_scroll_lock called but recorder is not yet initialised; ignoring")
        return
    if self._session_active:
        _close_voice_session()          # ← delegates; clears _session_opened_by_dictation
    else:
        _open_voice_session()           # ← delegates; does NOT set the flag
```

`on_scroll_lock` never sets `_session_opened_by_dictation` — a Scroll Lock session
always leaves the flag `False`.

---

### 3. `on_dictation_toggle` — `not _session_active` branch

Replace the current no-op + miss-chime with a proper open-then-start path:

```python
def on_dictation_toggle(self) -> None:
    ...
    if not self._session_active:
        # Right Ctrl with no open session → open one and enter dictation immediately.
        if not self._open_voice_session():
            return   # recorder failed; error already surfaced by the helper
        self._session_opened_by_dictation = True
        if self._dictation_session is None:
            return
        self._dictation_session.start()
        return

    # _session_active == True: existing behaviour, unchanged
    if self._dictation_session is None:
        return
    if self._dictation_session.active:
        self._dictation_session.request_end()
        try:
            self._utt_q.put_nowait(_DICTATION_WAKE)
        except queue.Full:
            logger.warning("dictation: _utt_q full — sentinel not needed; pipeline already active")
        logger.info("dictation: hotkey-end requested; pipeline will drain and finalize")
    else:
        self._dictation_session.start()
```

---

### 4. Auto-close on dictation end — `_end_owned_session_if_needed`

A small helper checks the flag and, if set, submits the close to the dictation
executor so it runs after any in-flight finalize work:

```python
def _end_owned_session_if_needed(self) -> None:
    """If this session was opened by Right Ctrl, close it now.

    Submitted to _dictation_executor so it executes AFTER the preceding
    _finalize_dictation call (FIFO single-worker executor). Safe to call
    even when take_and_finish() returned None (empty buffer / already closed).
    """
    if self._session_opened_by_dictation:
        self._close_voice_session()
```

This helper is called from ALL THREE dictation-end paths AFTER the existing
finalize/cancel handling. The close trigger is independent of whether any audio was
submitted — a Ctrl-open + immediate Ctrl-close with zero speech still closes the
session.

#### End-word path (pipeline thread, `_process_utterance` around line 620)

After the existing `take_and_finish()` + optional `submit(_finalize_dictation)`:

```python
if kind == "end":
    audio = self._dictation_session.take_and_finish()
    if audio is not None:
        self._dictation_executor.submit(self._finalize_dictation, audio)
    self._dictation_executor.submit(self._end_owned_session_if_needed)  # ← new
```

#### Hotkey-end path (`_finalize_pending_dictation_end`, pipeline thread, line 757)

After the existing `take_and_finish()` + optional submit:

```python
audio = self._dictation_session.take_and_finish()
if audio is not None:
    self._dictation_executor.submit(self._finalize_dictation, audio)
self._dictation_executor.submit(self._end_owned_session_if_needed)      # ← new
```

#### Spoken-cancel path (pipeline thread, `_process_utterance` around line 625)

After `self._dictation_session.cancel()`:

```python
elif kind == "cancel":
    self._dictation_session.cancel()
    self._dictation_executor.submit(self._end_owned_session_if_needed)  # ← new
```

**Ordering guarantee:** `_finalize_dictation` and `_end_owned_session_if_needed` are
submitted in order to a FIFO single-worker executor (`max_workers=1`). The paste
(clipboard) and the recorder stop are independent operations, so no sequencing
constraint exists between them — but the FIFO ordering means the close never races
ahead of the paste.

---

## Race / Thread-safety

### Thread map

Five long-lived threads already exist: pynput hotkey-listener, `vc-pipeline` worker,
`vad-worker`, `_dictation_executor` worker(s), heartbeat. `on_scroll_lock` and
`on_dictation_toggle` run exclusively on the hotkey-listener thread. Adding
`_close_voice_session` as a callable on the `_dictation_executor` thread introduces a
third thread that writes `_session_active`, `_audio_gen`, and
`_session_opened_by_dictation`.

`_session_active` and `_audio_gen` are plain Python attributes already mutated across
threads (hotkey-listener writes, pipeline reads) under the GIL. Adding the executor
thread as another writer is consistent with the existing pattern — no new lock is
needed and none is introduced. This is documented explicitly here as a known design
choice, not an oversight.

### Race: Scroll Lock pressed during a Ctrl-opened dictation

Scroll Lock `on_scroll_lock` calls `_close_voice_session()` (hotkey-listener thread),
which clears `_session_opened_by_dictation = False`, cancels the dictation sub-state,
and closes the recorder. The later dictation-end `_end_owned_session_if_needed` (on
the executor thread) then sees the flag `False` → returns immediately, a no-op.
`recorder.close_session()` is a no-op when already IDLE (guarded by its `_state_lock`
inside `StreamingRecorder`). Any redundant `session_stopped` events or queue drains
are harmless. **Result: benign.**

### Double-close (executor close racing a hotkey close)

Both paths call `_close_voice_session`, which calls `recorder.close_session()`.
`recorder.close_session()` is idempotent when not in the OPEN state — it is a no-op.
Redundant queue drains (already-empty queue) are also harmless. **Result: benign.**

### `recorder.close_session()` blocking join on the executor thread

`recorder.close_session()` joins the `vad-worker` thread with up to a 5 s timeout.
When called from the `_dictation_executor` thread this is safe: the join target is the
`vad-worker`, not the pipeline thread. The pipeline thread continues draining `_utt_q`
so the VAD worker can finish enqueuing and exit on its own sentinel. No circular wait,
no deadlock.

### `_open_voice_session()` called from the hotkey-listener thread

`_open_voice_session()` was already run on the hotkey-listener thread inside
`on_scroll_lock`; calling it from `on_dictation_toggle` (also on the hotkey-listener
thread) is identical. pynput serialises key callbacks so concurrent invocations of
either method cannot occur.

---

## Validation

### Unit tests

**New field and helpers (`tests/unit/test_daemon.py` or `tests/unit/test_daemon_dictation.py`):**

- `on_dictation_toggle` with `_session_active=False`:
  - calls `_open_voice_session()`;
  - on success: sets `_session_opened_by_dictation = True`, calls
    `_dictation_session.start()`, plays NO miss chime;
  - on `_open_voice_session()` failure: returns early, `_session_opened_by_dictation`
    remains `False`, dictation does not start.
- `on_scroll_lock` open path: does NOT set `_session_opened_by_dictation`.
- `_close_voice_session()`: always resets `_session_opened_by_dictation = False`
  regardless of prior value.
- `_open_voice_session()` / `_close_voice_session()`: behaviour identical to the old
  inline `on_scroll_lock` logic (regression guard for the refactor).
- `_end_owned_session_if_needed`:
  - flag `True` → calls `_close_voice_session`;
  - flag `False` → no-op, `_close_voice_session` not called.

### Integration tests

**`tests/integration/test_dictation_opens_session.py`:**

- **Ctrl-open → dictate → "done" → finalize + session closes**: `recorder.close_session`
  called, `_session_active = False`, `session_stopped` published.
- **Ctrl-open → Ctrl-end with empty buffer → session closes**: `recorder.close_session`
  called even when `take_and_finish()` returned `None`.
- **Ctrl-open → "cancel" → aborts + session closes**: no audio submitted, no paste,
  `dictation.end {"reason": "cancel"}` published, `session_stopped` published.
- **Scroll Lock session + Ctrl dictation + "done" → session STAYS open** (regression
  guard for the non-goal): `session_stopped` is NOT published after dictation ends.
- **Scroll Lock open/close path unchanged** (regression guard for the refactor):
  `_session_opened_by_dictation` stays `False`; `session_started` / `session_stopped`
  events are published in the same order as before.

### Visual E2E (mandatory per `docs/agents/visual-e2e-testing.md`)

`scripts/dictation_opens_session_e2e.py` — a subprocess harness following the
mandatory protocol. This feature touches hotkeys, daemon ↔ sprite ↔ web-UI IPC, and
the system clipboard — all three triggers for the visual E2E requirement.

The harness must:

1. Start the daemon + sprite (subprocess); verify the sprite window is visible
   (`PrintWindow` / `GetForegroundWindow`).
2. **Ctrl-open + end-word test**: assert no session is open; inject Right Ctrl via
   `SendInput` / pynput; assert `session_started` SSE event arrives; inject dictation
   audio + `"done"` transcript; assert `dictation.end` + `session_stopped` arrive and
   clipboard is populated.
3. **Ctrl-open + empty-buffer Ctrl-close**: inject Right Ctrl (open); immediately
   inject Right Ctrl again (hotkey-end with no audio); assert `session_stopped` arrives
   within 1 s and no clipboard paste occurred.
4. **Ctrl-open + spoken cancel**: inject Right Ctrl (open); inject cancel-word
   transcript; assert `dictation.end {"reason": "cancel"}` + `session_stopped` arrive
   and clipboard is unchanged.
5. **Regression: Scroll Lock session stays open**: open a Scroll Lock session, inject
   Right Ctrl + end-word, assert `session_stopped` is NOT published.
6. Capture screenshots as evidence; log all assertions to a test-evidence file.

Patterns to copy: `scripts/picker_modal_smoke.py` (in-process render smoke) and
`scripts/picker_visual_e2e.py` (subprocess + SSE + PrintWindow + `GetForegroundWindow`
assertion).

### Manual validation (HITL gate)

- No session open: press Right Ctrl once. Confirm the recording-start chime sounds and
  the sprite transitions to active. Say two or three words, then say `"done"`. Confirm
  text is pasted and the sprite returns to idle (recording-stop chime).
- No session open: press Right Ctrl twice in succession (open then immediate Ctrl-end
  with no speech). Confirm the session closes cleanly and nothing is pasted.
- No session open: press Right Ctrl, say "cancel". Confirm nothing is pasted and the
  sprite shows the cancelled cue.
- Scroll Lock session open: press Right Ctrl to start dictation, say `"done"`. Confirm
  dictation ends but the session remains open (mic still live, sprite stays active).

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Open/close helper refactor changes Scroll Lock behaviour | Medium — the paths are subtle | Regression tests assert Scroll Lock produces identical state transitions as before; require green before merging |
| `_close_voice_session` on executor thread races hotkey-listener `_close_voice_session` | Low | Both calls are idempotent (recorder guarded by `_state_lock`); redundant events are harmless; documented explicitly |
| 5 s blocking join in `_close_voice_session` on the executor thread | Acceptable | The join target is the `vad-worker`, not the pipeline thread; no deadlock path exists; documented |
| `_session_opened_by_dictation` written by two threads (hotkey + executor) without a lock | Acceptable — consistent with existing `_session_active` / `_audio_gen` pattern | GIL provides the required atomicity for plain attribute writes; documented as an explicit design choice |
| `_open_voice_session` failure leaves the session in a partial state | Low — same exception path as existing `on_scroll_lock` | Helper returns `False` and surfaces error; `_session_opened_by_dictation` is never set; dictation is never started |

---

## Docs to update (same change as code)

The following must be updated in the same PR as the implementation — a doc that
contradicts the code is a defect:

- **New ADR** `docs/decisions/0090-dictation-hotkey-opens-session.md` — records the
  state-machine change, the `_session_opened_by_dictation` flag, the helper refactor,
  and the auto-close mechanism; cites ADR 0086 (dictation mode) and ADR 0089 (hotkey
  sentinel + cancel + debounce) as predecessors.
- **`docs/agents/technical-decisions.md`** — add the ADR 0090 row.
- **ADR 0086** — add a "Successor / Amendment" note: dictation is no longer strictly a
  sub-state of a Scroll Lock session; Right Ctrl can now open its own session (see ADR
  0090).
- **`CLAUDE.md` "Current state" dictation paragraph** — update to reflect that Right
  Ctrl opens a self-contained dictation session when no Scroll Lock session is active;
  on end/cancel the session closes automatically; the Scroll Lock sub-state behaviour
  is unchanged.
- **`config.toml.example`** — no changes expected (no new config key).
- **`docs/index.md`** — no new overview doc warranted; the ADR suffices.
