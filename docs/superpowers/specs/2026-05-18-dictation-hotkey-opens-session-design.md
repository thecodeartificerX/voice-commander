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

    IMPORTANT: This method MUST only ever be invoked on the _dictation_executor
    thread (by submitting it as a callable) — never called directly on the pipeline
    thread. A direct call on the pipeline thread would run recorder.close_session(),
    which joins the vad-worker thread (up to 5 s timeout), stalling the pipeline
    thread and preventing it from draining _utt_q. The vad-worker depends on the
    pipeline draining _utt_q to finish its flush and exit — calling close_session()
    directly from the pipeline thread creates a circular wait / deadlock.
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

**Thread-safety note on `_close_voice_session` invocation:** `_end_owned_session_if_needed`
(§4 below) is the sole mechanism by which `_close_voice_session` is submitted to
`_dictation_executor`. It must always be submitted — never called directly — from
pipeline-thread code. Violation of this rule is a deadlock hazard (see docstring above).

#### `on_scroll_lock` rewritten to call helpers

```python
def on_scroll_lock(self) -> None:
    """Toggle the voice session on/off.  (docstring unchanged — no behavioural change.)"""
    if self._recorder is None:
        logger.warning("on_scroll_lock called but recorder is not yet initialised; ignoring")
        return
    if self._session_active:
        self._close_voice_session()          # ← delegates; clears _session_opened_by_dictation
    else:
        self._open_voice_session()           # ← delegates; does NOT set the flag
```

`on_scroll_lock` never sets `_session_opened_by_dictation` — a Scroll Lock session
always leaves the flag `False`.

**Note on `on_scroll_lock` thread context:** `on_scroll_lock` runs on the hotkey-listener
thread. When the session is closing via Scroll Lock, `self._close_voice_session()` is
called **directly** (not via the executor). This is safe because the hotkey-listener
thread is not the pipeline thread — the pipeline thread can continue draining `_utt_q`
so the `vad-worker` join completes promptly. The executor-submission rule in
`_end_owned_session_if_needed` applies exclusively to calls originating from pipeline-thread
code (i.e. the dictation-end paths).

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

**Abandoned Ctrl-opened session (no auto-timeout):** If the user presses Right Ctrl to
open a dictation session and then never speaks and never presses Right Ctrl again, the
session stays open indefinitely — identical to an abandoned Scroll Lock session. No
auto-timeout is introduced. This is deliberate: the existing design has no idle-timeout
for voice sessions, and this feature does not change that policy.

---

### 4. Auto-close on dictation end — `_end_owned_session_if_needed`

A small helper checks the flag and, if set, submits the close to the dictation
executor so it runs after any in-flight finalize work:

```python
def _end_owned_session_if_needed(self) -> None:
    """If this session was opened by Right Ctrl, close it now.

    Contract / docstring:
    - MUST only ever be invoked by submitting it to _dictation_executor as a
      callable — never called directly on the pipeline thread. A direct call on
      the pipeline thread would stall it on recorder.close_session()'s vad-worker
      join (up to 5 s), preventing _utt_q from being drained and causing a
      circular wait with the vad-worker.
    - Runs on the _dictation_executor worker thread (FIFO, single-worker).
    - Safe to call even when take_and_finish() returned None (empty buffer /
      already closed).
    - DictationSession.cancel() and recorder.close_session() are both idempotent;
      double-cancel / double-close races are benign.
    """
    if self._session_opened_by_dictation:
        self._close_voice_session()
```

This helper is submitted to `_dictation_executor` from ALL THREE dictation-end paths.
The close trigger is independent of whether any audio was submitted — a Ctrl-open +
immediate Ctrl-close with zero speech still closes the session.

**Close-first ordering (BLOCKER):** In both the end-word path and the hotkey-end path,
the session close MUST be submitted to `_dictation_executor` BEFORE `_finalize_dictation`.
Rationale: `_dictation_executor` is a FIFO single-worker executor. If `_finalize_dictation`
(which performs a network POST) is submitted first and the POST hangs, the recorder stays
open indefinitely. Close-first means the recording stops promptly (bounded by the ≤5 s
VAD-worker join) and the network POST runs afterward on the same executor. The captured
audio is passed by value to `_finalize_dictation`, so it is unaffected by the session close.

**Resulting UX:** With close-first ordering, the recording-stop chime fires before the
text is pasted at the cursor. The chime signals "captured, processing" — the text lands
shortly after once the remote inference completes. This is the intended UX.

**HTTP timeout requirement:** `post_audio` in `src/voice_commander/dictation/remote.py`
already accepts a `timeout` parameter (default `_TIMEOUT_S = 300.0` seconds via
`httpx.post(timeout=timeout)`). However, 300 s is too generous for the executor-ownership
context introduced by this feature: a 5-minute hung POST blocks `_dictation_executor`
for all subsequent dictation operations. The spec mandates:

- Lower `_TIMEOUT_S` from 300 s to a **bounded value of 30 s** (connect + read, total).
  Long dictations on the reference hardware have been measured at ~+1.2 s for a 36 s
  clip; 30 s total gives ample headroom without allowing an indefinite executor stall.
- On timeout (httpx raises `TimeoutException`, caught by the `except Exception` wrapper),
  `post_audio` raises `DictationRemoteError` — the existing error type that
  `_finalize_dictation` already catches, publishing a `dictation.error` event and
  sounding a miss chime.
- The timeout constant `_TIMEOUT_S` must be updated in the same PR as the implementation.
  Its inline comment must explain the rationale (executor-stall prevention).

Current state of `remote.py` for the record: `post_audio` already passes
`timeout=timeout` to `httpx.post`; the default `_TIMEOUT_S = 300.0` is the only
defect — the wiring is correct, only the value is too generous.

#### End-word path (pipeline thread, `_process_utterance` around line 620)

Precise ordering — close is submitted BEFORE finalize:

```python
if kind == "end":
    audio = self._dictation_session.take_and_finish()
    self._dictation_executor.submit(self._end_owned_session_if_needed)  # ← close first
    if audio is not None:
        self._dictation_executor.submit(self._finalize_dictation, audio)
```

**Ordering:** (1) `take_and_finish()` — captures audio; (2) submit `_end_owned_session_if_needed`
unconditionally; (3) submit `_finalize_dictation` only if audio is not None.
`_end_owned_session_if_needed` is submitted unconditionally regardless of whether audio
was captured — covering the empty-buffer case.

#### Hotkey-end path (`_finalize_pending_dictation_end`, pipeline thread, line 757)

Precise ordering — close is submitted BEFORE finalize, UNCONDITIONALLY:

```python
def _finalize_pending_dictation_end(self) -> None:
    audio = self._dictation_session.take_and_finish()            # (1) capture audio
    self._dictation_executor.submit(self._end_owned_session_if_needed)  # (2) close first, unconditional
    if audio is not None:
        self._dictation_executor.submit(self._finalize_dictation, audio)  # (3) finalize if audio present
```

The close is submitted **unconditionally** — after the `take_and_finish()` call but
before (and independently of) the `if audio is not None:` branch. This covers the
empty-buffer Ctrl-open → Ctrl-close case (user presses Ctrl to open, then immediately
presses Ctrl again without speaking). Whether audio is None or not, the session closes.

#### Spoken-cancel path (pipeline thread, `_process_utterance` around line 625)

```python
elif kind == "cancel":
    self._dictation_session.cancel()
    self._dictation_executor.submit(self._end_owned_session_if_needed)  # ← new
```

**Ordering guarantee:** `_end_owned_session_if_needed` and `_finalize_dictation` are
submitted in order to a FIFO single-worker executor (`max_workers=1`). Close-first
ordering ensures the recorder is stopped before the network POST begins. The paste
(clipboard) and the recorder stop are independent operations; FIFO ordering means the
close never races ahead of the paste.

---

### 5. Shutdown cleanup

`Daemon.shutdown()` already closes the session if `_session_active`, but does NOT
call `self._dictation_session.cancel()`. If dictation is active at shutdown, the
sprite is left stuck in the `dictating` state — no `dictation.end` event is ever
published.

The spec mandates: in `shutdown()`, add a direct `self._dictation_session.cancel()` call
placed **immediately after the session-close block and pipeline join, and BEFORE
`_dictation_executor.shutdown(wait=False)`**:

```python
# In shutdown(), after session-close block + pipeline join, BEFORE executor shutdown:
if self._dictation_session is not None and self._dictation_session.active:
    self._dictation_session.cancel()

# Shut down executors AFTER the cancel call:
self._wav_executor.shutdown(wait=False)
self._dictation_executor.shutdown(wait=False)
self._elements_executor.shutdown(wait=False)
```

**Placement rationale (REV 3):** `cancel()` only sets flags and publishes `dictation.end`
on the event bus — it does not use the executor — so it is safe to call before executor
teardown. Publishing `dictation.end` **before** `executor.shutdown(wait=False)` keeps
event ordering clean and ensures the sprite sees the `dictation.end` event before the
executor is torn down. `executor.shutdown(wait=False)` abandons queued tasks — any
`_end_owned_session_if_needed` already in the queue will not execute. The cancel must be
called directly here; relying on queued tasks is unreliable.

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

Scroll Lock `on_scroll_lock` calls `self._close_voice_session()` (hotkey-listener thread),
which clears `_session_opened_by_dictation = False`, cancels the dictation sub-state,
and closes the recorder. The later dictation-end `_end_owned_session_if_needed` (on
the executor thread) then sees the flag `False` → returns immediately, a no-op.
`recorder.close_session()` is a no-op when already IDLE (guarded by its `_state_lock`
inside `StreamingRecorder`). Any redundant `session_stopped` events or queue drains
are harmless. **Result: benign.**

### Double-close (executor close racing a hotkey close)

Both paths call `_close_voice_session`, which calls `recorder.close_session()`.
`recorder.close_session()` is idempotent when not in the OPEN state — it is a no-op.
`DictationSession.cancel()` is also idempotent. Redundant queue drains
(already-empty queue) are also harmless. **Result: benign.**

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

### Pipeline thread after session close

After `_close_voice_session` executes (on the executor thread), the pipeline worker
thread blocks in `_utt_q.get(timeout=None)` until the next session open or daemon
shutdown. This is the existing Scroll-Lock-close behaviour and is unchanged — no `None`
sentinel is enqueued by `_close_voice_session`. The pipeline thread simply waits for
the next utterance or shutdown signal.

### Timing window: Ctrl press during a pending close (benign)

`_close_voice_session` runs on the executor and the ≤5 s VAD-worker join means
`_session_active` may still read `True` for a few seconds after a dictation-end
(before the executor task runs). A Right Ctrl press in that window takes the
existing-session branch in `on_dictation_toggle` (because `_session_active` reads
`True`) and sends a redundant hotkey-end sentinel. The resulting second
`_end_owned_session_if_needed` sees either the flag already cleared (set to `False` by
the first close) or `recorder.close_session()` is an idempotent no-op — both outcomes
are harmless. **Result: benign.**

### Idempotency contract

`DictationSession.cancel()` and `recorder.close_session()` are both idempotent. The
spec relies on this property wherever double-close / double-cancel races are discussed
above. Any future refactor of these classes must preserve idempotency.

---

## Validation

### Unit tests

**New field and helpers (`tests/unit/test_daemon.py` or `tests/unit/test_daemon_dictation.py`):**

- `on_dictation_toggle` with `_session_active=False`:
  - calls `self._open_voice_session()`;
  - on success: sets `_session_opened_by_dictation = True`, calls
    `self._dictation_session.start()`, plays NO miss chime;
  - on `_open_voice_session()` failure: returns early, `_session_opened_by_dictation`
    remains `False`, dictation does not start.
- `on_scroll_lock` open path: does NOT set `_session_opened_by_dictation`.
- `self._close_voice_session()`: always resets `_session_opened_by_dictation = False`
  regardless of prior value.
- `self._open_voice_session()` / `self._close_voice_session()`: behaviour identical to the old
  inline `on_scroll_lock` logic (regression guard for the refactor).
- `_end_owned_session_if_needed`:
  - flag `True` → calls `self._close_voice_session`;
  - flag `False` → no-op, `self._close_voice_session` not called.
- `shutdown()` with active dictation session: confirms `self._dictation_session.cancel()`
  is called, even when `_dictation_executor.shutdown(wait=False)` abandons queued tasks.
- `_finalize_pending_dictation_end` with `_session_opened_by_dictation=True` and empty
  buffer: confirms `_end_owned_session_if_needed` is submitted to executor even when
  `take_and_finish()` returns `None`.
- `remote.post_audio`: confirm `httpx.post` is called with `timeout <= 30` (enforces the
  `_TIMEOUT_S` reduction); confirm `TimeoutException` is re-raised as `DictationRemoteError`.

### Integration tests

**`tests/integration/test_dictation_opens_session.py`:**

- **Ctrl-open → dictate → "done" → finalize + session closes**: `recorder.close_session`
  called, `_session_active = False`, `session_stopped` published. Confirm close-first
  ordering: `session_stopped` event arrives before the paste completes.
- **Ctrl-open → Ctrl-end with empty buffer → session closes**: `recorder.close_session`
  called even when `take_and_finish()` returned `None`.
- **Ctrl-open → "cancel" → aborts + session closes**: no audio submitted, no paste,
  `dictation.end {"reason": "cancel"}` published, `session_stopped` published.
- **Scroll Lock session + Ctrl dictation + "done" → session STAYS open** (regression
  guard for the non-goal): `session_stopped` is NOT published after dictation ends.
- **Scroll Lock open/close path unchanged** (regression guard for the refactor):
  `_session_opened_by_dictation` stays `False`; `session_started` / `session_stopped`
  events are published in the same order as before.
- **Shutdown with active dictation**: `shutdown()` called while dictation is active;
  confirm `dictation.end` is published (cancel called directly, not via executor).
- **Timeout propagation**: mock `httpx.post` to raise `TimeoutException`; confirm
  `_finalize_dictation` catches it and publishes `dictation.error` + miss chime; confirm
  executor worker is unblocked afterward.

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
   clipboard is populated. Confirm recording-stop chime fires before paste (close-first
   UX).
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
  the recording-stop chime fires (close-first), then text is pasted. Confirm the sprite
  returns to idle.
- No session open: press Right Ctrl twice in succession (open then immediate Ctrl-end
  with no speech). Confirm the session closes cleanly and nothing is pasted.
- No session open: press Right Ctrl, say "cancel". Confirm nothing is pasted and the
  sprite shows the cancelled cue, then returns to idle.
- Scroll Lock session open: press Right Ctrl to start dictation, say `"done"`. Confirm
  dictation ends but the session remains open (mic still live, sprite stays active).
- Simulate a hung endpoint (point at a non-responsive host): open Ctrl dictation, speak,
  say "done". Confirm the session closes promptly (recording-stop chime fires), and after
  30 s a miss chime sounds (timeout → `DictationRemoteError` → `dictation.error` event).

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Open/close helper refactor changes Scroll Lock behaviour | Medium — the paths are subtle | Regression tests assert Scroll Lock produces identical state transitions as before; require green before merging |
| `_close_voice_session` on executor thread races hotkey-listener `_close_voice_session` | Low | Both calls are idempotent (recorder guarded by `_state_lock`; `DictationSession.cancel()` is idempotent); redundant events are harmless; documented explicitly |
| 5 s blocking join in `_close_voice_session` on the executor thread | Acceptable | The join target is the `vad-worker`, not the pipeline thread; no deadlock path exists; documented |
| `_session_opened_by_dictation` written by two threads (hotkey + executor) without a lock | Acceptable — consistent with existing `_session_active` / `_audio_gen` pattern | GIL provides the required atomicity for plain attribute writes; documented as an explicit design choice |
| `_open_voice_session` failure leaves the session in a partial state | Low — same exception path as existing `on_scroll_lock` | Helper returns `False` and surfaces error; `_session_opened_by_dictation` is never set; dictation is never started |
| Hung remote POST blocks `_dictation_executor` indefinitely | Medium — mitigated by REV 1 | `_TIMEOUT_S` reduced from 300 s to 30 s; `TimeoutException` → `DictationRemoteError` → miss chime; executor unblocked promptly |
| Dictation state leaked at shutdown (sprite stuck in `dictating`) | Low — pre-existing defect | `shutdown()` now calls `self._dictation_session.cancel()` directly after executor shutdown; documented in §5 |
| Timing window: Ctrl press during pending close (executor not yet run) | Low — `_session_active` still reads True | Second hotkey-end is redundant but benign: `_end_owned_session_if_needed` sees flag cleared; `recorder.close_session()` is idempotent |

---

## Docs to update (same change as code)

The following must be updated in the same PR as the implementation — a doc that
contradicts the code is a defect:

- **New ADR** `docs/decisions/0090-dictation-hotkey-opens-session.md` — records the
  state-machine change, the `_session_opened_by_dictation` flag, the helper refactor,
  the auto-close mechanism, the close-first ordering requirement, the 30 s HTTP timeout,
  and the shutdown cleanup; cites ADR 0086 (dictation mode) and ADR 0089 (hotkey
  sentinel + cancel + debounce) as predecessors. ADR 0090 must be framed explicitly as
  **amending ADR 0086 Decision D1** — ADR 0086 D1 stated that dictation is entered/exited
  "without touching StreamingRecorder session state"; this feature deliberately changes
  that for the Ctrl-opened case.
- **`docs/agents/technical-decisions.md`** — add the ADR 0090 row.
- **ADR 0086** — add an explicit **"Amendment: ADR 0090"** pointer to D1: dictation is
  no longer strictly a sub-state of a Scroll Lock session for the Ctrl-opened case; the
  amendment note must cite ADR 0090 by number and summarise the change in one sentence.
  A vague "successor note" is insufficient — the pointer must be explicit and identify
  D1 as the amended decision.
- **`CLAUDE.md` "Current state" dictation paragraph** — update to reflect that Right
  Ctrl opens a self-contained dictation session when no Scroll Lock session is active;
  on end/cancel the session closes automatically (close-first: recording-stop chime
  fires before paste); the Scroll Lock sub-state behaviour is unchanged.
- **`src/voice_commander/dictation/remote.py`** — update `_TIMEOUT_S` from `300.0` to
  `30.0` and update its inline comment to explain the executor-stall rationale.
- **`config.toml.example`** — no changes expected (no new config key).
- **`docs/index.md`** — no new overview doc warranted; the ADR suffices.

---

## Spec self-review

| # | Check | Result |
|---|-------|--------|
| 1 | All pseudocode uses `self.` prefix on method calls | PASS — all calls use `self._open_voice_session()`, `self._close_voice_session()`, `self._end_owned_session_if_needed()`, `self._finalize_dictation()` etc. |
| 2 | Close-before-finalize ordering mandated in both end-word and hotkey-end paths | PASS — §4 pseudocode shows `submit(_end_owned_session_if_needed)` before `submit(_finalize_dictation)` in both paths |
| 3 | HTTP timeout requirement specified | PASS — §4 mandates `_TIMEOUT_S` reduced to 30 s; `remote.py` current state documented |
| 4 | `_end_owned_session_if_needed` submitted unconditionally in hotkey-end path | PASS — §4 hotkey-end pseudocode shows unconditional submit before the `if audio is not None:` branch |
| 5 | Shutdown cleanup specified | PASS — §5 mandates `self._dictation_session.cancel()` in `shutdown()`, BEFORE `_dictation_executor.shutdown(wait=False)` (REV 3: cancel only publishes to event bus, no executor dependency; publishing before teardown keeps event ordering clean) |
| 6 | `_end_owned_session_if_needed` executor-only contract documented | PASS — both the helper's docstring (§4) and `_close_voice_session`'s docstring (§2) state the pipeline-thread-call prohibition |
| 7 | Abandoned session (no auto-timeout) documented | PASS — §3 states this is deliberate |
| 8 | Pipeline thread post-close behaviour documented | PASS — Race section documents `_utt_q.get(timeout=None)` blocking behaviour |
| 9 | Timing window (Ctrl press during pending close) documented | PASS — Race section "Timing window" entry |
| 10 | Idempotency contract stated explicitly | PASS — Race section "Idempotency contract" |
| 11 | ADR 0086 D1 amendment — specific pointer, not vague successor | PASS — Docs section specifies amendment target (D1), requires ADR 0090 to be framed as amending D1, and requires explicit pointer in ADR 0086 |
| 12 | Design, Race, Validation, Risks, Docs sections internally consistent | PASS — close-first ordering, 30 s timeout, shutdown cleanup, and timing-window race all appear consistently in Design (§4, §5), Race, Validation (unit + integration + manual), Risks, and Docs |
| 13 | UX description of close-first chime ordering | PASS — §4 and Manual validation gate describe recording-stop chime before paste |
| 14 | `remote.py` actual current state accurately described | PASS — §4 states `_TIMEOUT_S = 300.0` is the defect; wiring (`timeout=timeout` passed to `httpx.post`) is correct |
