# Robust Dictation Hotkey-End + Spoken "Cancel" — Design Spec

**Date:** 2026-05-18
**Status:** Design complete — pending implementation plan
**New ADR:** 0089 — Dictation hotkey-end sentinel wake + spoken cancel + hotkey debounce
**Prior art in repo:** ADR 0086 (dictation mode), ADR 0025 (mute key, removed), ADR 0048 (event bus), ADR 0088 (custom vocabulary + postprocess pipeline).

---

## Context

Voice Commander dictation mode (ADR 0086) is a voice-session sub-state entered by
saying `"dictate"` or pressing Right Ctrl (`dictation_key`, default `ctrl_r`). It is
exited through one of two confirmed paths:

1. **End-word path** — the user says the end word `"done"` (configurable). The
   pipeline thread recognises it via `DictationSession.handle_utterance()`, calls
   `take_and_finish()`, and submits audio to `_finalize_dictation`.
2. **Hotkey-end path** — the user presses `dictation_key` a second time, calling
   `on_dictation_toggle()` → `DictationSession.request_end()`, which sets
   `_pending_end`. The pipeline thread is supposed to detect this, drain any in-flight
   utterances, and call `_finalize_pending_dictation_end()`.

The hotkey-end path is **unreliable in practice**. The user must fall back to saying
"done". This spec identifies the root cause and describes three targeted changes that
together make the hotkey path as reliable as the end-word path, and adds a spoken
"cancel" exit that discards the dictation buffer without transcription.

---

## Problem

### Root-cause analysis (confirmed by code inspection)

The pipeline worker (`vc-pipeline` thread) runs `_pipeline_loop()`. At the top of
each loop iteration it evaluates whether `pending_end` is set and, if so, uses a
short `_DICTATION_DRAIN_TIMEOUT_S` (0.25 s) timeout instead of `get(timeout=None)`.

**Precise root cause:** the pipeline worker thread is blocked INSIDE
`_utt_q.get(timeout=None)`, a call carried over from a previous loop iteration when
`pending_end` was `False`. `pending_end` is only evaluated at the TOP of the loop,
before the `get()`. So a hotkey-end press that lands while the thread sits in that
blocking `get()` is invisible until something else unblocks the call — the next real
utterance, or the daemon-shutdown `None` sentinel.

This is NOT a "permanently hung" pipeline — it is hung until the next utterance or
shutdown. The thread will eventually resume, but only when a queue item arrives.

The race condition in detail:

1. The pipeline thread has already entered `self._utt_q.get(timeout=None)` — it is
   blocked inside the `Queue.get` call, waiting indefinitely.
2. The user presses Right Ctrl. The hotkey thread calls `request_end()`, which sets
   `_pending_end`.
3. `_pending_end` is now `True`, but the pipeline thread re-evaluates the `pending`
   variable only at the **top of its next iteration**, which cannot happen until
   `get()` returns.
4. If the user stops speaking after the press (the normal case), no new utterance
   arrives. `get(timeout=None)` does not return. The pipeline remains blocked until
   the next utterance or daemon shutdown.

**Why "done" always works:** the spoken word itself is an utterance that unblocks
`get()`. Once `get()` returns, the loop re-evaluates `pending`, routes through
`handle_utterance`, recognises `"done"`, and finalises.

**Why hotkey-end sometimes works ("hit" cases):** a stray VAD noise event or one
more spoken word produces an utterance, waking `get()`. The loop then sees
`pending_end == True`, switches to the timed drain, and eventually finalises.
Silence after the key press = hung pipeline until next utterance or shutdown.

### Additional issues identified

1. **No debounce on `HotkeyController._on_release`**: some keyboard drivers fire two
   rapid `on_release` events for a single physical key press ("key bounce"). With
   `dictation_key`, two releases in quick succession toggle dictation on and then
   immediately off — the net user-visible effect is "nothing happened", which looks
   like the same reliability bug.
2. **No spoken cancel path**: there is no way to abandon a dictation mid-session
   without transcribing and pasting it. The user must say "done" and then manually
   undo the paste.

### Key source locations

| Symbol | File | Lines (approx) |
|--------|------|----------------|
| `_DICTATION_DRAIN_TIMEOUT_S` | `src/voice_commander/daemon.py` | L61 |
| `_pipeline_loop` | `src/voice_commander/daemon.py` | L430–449 |
| `on_dictation_toggle` | `src/voice_commander/daemon.py` | L395–412 |
| `_finalize_pending_dictation_end` | `src/voice_commander/daemon.py` | L725–748 |
| `_finalize_dictation` | `src/voice_commander/daemon.py` | L750–813 |
| `DictationSession` | `src/voice_commander/dictation/session.py` | entire file |
| `UtteranceKind` | `src/voice_commander/dictation/session.py` | L47 |
| `DictationSession.cancel` | `src/voice_commander/dictation/session.py` | L160–169 |
| `HotkeyController._on_release` | `src/voice_commander/hotkey.py` | L52–60 |
| `DictationConfig` | `src/voice_commander/config.py` | L26–35 |
| `HotkeyConfig` | `src/voice_commander/config.py` | L17–25 |

---

## Goals

1. **Reliable hotkey-end:** a second `dictation_key` press reliably and promptly
   finalizes dictation regardless of whether any further utterance arrives.
2. **Spoken cancel:** saying a configurable cancel word (default `"cancel"`) during
   dictation discards the audio buffer — no transcription, no POST, no paste.
3. **Hotkey debounce:** guard against driver double-release / key bounce causing an
   accidental double-toggle.

---

## Non-goals

- Cancelling **after** finalization has started (the POST is already in flight).
  `"cancel"` is only effective while the session is still buffering.
- A dedicated hotkey for cancel — spoken word only.
- A new LLM-visible primitive.
- Changes to the local command transcription path (`transcriber.py`, faster-whisper).
- Per-session or per-profile cancel words — a single `cancel_word` per install.

---

## Design

### Change 1 — Sentinel wake for hotkey-end

**Problem restated:** `pending_end` is set by the hotkey thread while the pipeline
thread is already inside `get(timeout=None)`, so the flag is never checked until an
utterance (or the shutdown `None` sentinel) naturally unblocks the queue.

**Chosen approach (A):** `on_dictation_toggle` in `src/voice_commander/daemon.py`,
in the branch where dictation is active, enqueues a module-level sentinel object
immediately after calling `request_end()`:

```python
# daemon.py — module level
_DICTATION_WAKE = object()

# on_dictation_toggle (abridged):
if self._dictation_session.active:
    self._dictation_session.request_end()
    try:
        self._utt_q.put_nowait(_DICTATION_WAKE)   # ← new
    except queue.Full:
        # A full queue means the pipeline is already non-idle and will
        # re-evaluate pending_end within a few iterations without help.
        logger.warning("dictation: _utt_q full — sentinel not needed; pipeline already active")
    logger.info("dictation: hotkey-end requested; pipeline will drain and finalize")
```

**`put_nowait` full-queue policy (decided — not deferred):** on `queue.Full`, log a
warning and do nothing. Do NOT fall back to `put(timeout=...)`. A full queue means
the pipeline is already non-idle and processing items; it will naturally exit the
blocking `get()` very soon and re-evaluate `pending_end` within a few iterations.
The sentinel's only purpose is to wake an IDLE pipeline; it is redundant when the
pipeline is already active. The pynput listener thread must never block.

The pipeline loop, after its existing `None` shutdown-sentinel check, adds an
identity check for the wake sentinel BEFORE the tuple/ndarray item handling:

```python
if item is None:
    break  # existing shutdown path, unchanged

if item is _DICTATION_WAKE:
    continue   # re-enter loop top; pending_end is now True → short-timeout drain

# existing item handling (isinstance(item, tuple) etc.) follows unchanged
```

The `continue` returns to the top of the loop, where `pending` is re-evaluated as
`True`. The pipeline immediately switches to `get(timeout=_DICTATION_DRAIN_TIMEOUT_S)`,
and the first timeout after an empty queue triggers `_finalize_pending_dictation_end()`.

**`_utt_q` type annotation widening (required change):** the queue is currently typed
as `queue.Queue[tuple[ndarray, int] | ndarray | None]`. Adding `_DICTATION_WAKE`
(type `object`) requires widening this to
`queue.Queue[tuple[ndarray, int] | ndarray | None | object]` — or more precisely,
introducing a sentinel type alias. This annotation change is mandatory; do not leave
the type annotation stale.

**Why not approach B** (replace `get(timeout=None)` with a polling loop on
`pending_end`): constant thread wakeups even when no dictation is active; increases
idle CPU usage; adds complexity to the non-dictation hot path.

**Why not approach C** (finalize directly in `request_end()` / `on_dictation_toggle`):
skips the in-flight-utterance drain; races with concurrent buffer appends from the
pipeline thread; the drain exists precisely to handle the window between the press
and the last utterance finishing transcription.

**Ordering guarantee:** real utterances already queued ahead of the sentinel are
processed first (FIFO). The sentinel only supplies the missing wake signal; all
buffered audio is still captured by the 250 ms drain before `take_and_finish()` is
called.

**Sentinel identity:** `_DICTATION_WAKE = object()` is a unique module-level
singleton. Identity comparison (`item is _DICTATION_WAKE`) is safe — no risk of a
real utterance array being mistaken for the sentinel.

---

### Change 2 — Per-key debounce in `HotkeyController`

`HotkeyController` in `src/voice_commander/hotkey.py` stores a
`dict[keyboard.Key, float]` mapping each bound key to the `time.monotonic()` of its
last dispatched release. In `_on_release`, before invoking the callback:

```python
now = time.monotonic()
last = self._last_fire.get(key, 0.0)
if now - last < _DEBOUNCE_S:
    logger.debug("Hotkey debounce: dropping double-release for %s", key)
    return
self._last_fire[key] = now
```

`_DEBOUNCE_S = 0.050` (50 ms). Rationale:

- **This is bounce/double-fire rejection only** — hardware key bounce and driver
  double-release events resolve well under 50 ms. The window is explicitly NOT meant
  to suppress intentional rapid re-presses by the user.
- **Safe for `scroll_lock`:** even the fastest "open session, oops, close" mis-press
  correction is far longer than 50 ms in practice — the user's hand is still moving.
  50 ms never eats a legitimate scroll-lock toggle.
- **Safe for `dictation_key`:** same reasoning — a deliberate double-press to
  open-then-close dictation is always many hundreds of milliseconds, not 50 ms.

The debounce applies uniformly to all bound keys. Each key has its own independent
timer in `_last_fire`, so fast alternation between two keys (e.g. Scroll Lock then
Right Ctrl) is unaffected.

No new configuration key is introduced; the 50 ms constant is internal. If tuning is
needed in the future, it can be promoted to config at that point.

---

### Change 3 — Spoken cancel word (collapses to existing `cancel()`)

#### What already exists

`DictationSession` at `src/voice_commander/dictation/session.py` (lines ~160–169)
already has a `cancel(self) -> None` method. It:

- Deactivates the session (`_active = False`).
- Clears `_pending_end`.
- Clears `_buffer`.
- Publishes `dictation.end` with `{"reason": "cancel"}` when the session was active.

This method is currently called by the scroll-lock-close path. **It is not new, not
modified, and not re-signatured by this change.** The spoken-cancel path reuses it
exactly as-is.

#### `UtteranceKind` — add `"cancel"`

```python
# dictation/session.py line 47
UtteranceKind = Literal["buffered", "end", "cancel"]
```

#### `DictationSession.__init__` — add `cancel_word` parameter

```python
def __init__(self, bus: _BusLike, end_word: str = "done",
             cancel_word: str = "cancel") -> None:
    ...
    self._cancel_word: str | None = _normalize_spoken(cancel_word)
```

Cross-field validation lives HERE in `__init__`, not in the config loader (the config
loader has no cross-field stage; the precedent is the daemon-level
`dictation_key == hotkey_key` warn-and-degrade):

- If the normalized `cancel_word` equals the normalized `end_word`: log a WARNING and
  set `self._cancel_word = None`, disabling spoken cancel.
- If `cancel_word` is empty or whitespace after normalization: log a WARNING and set
  `self._cancel_word = None`, disabling spoken cancel.

#### `handle_utterance` — complete method body (implementer must not drop any guard)

The complete method body after this change is shown here so no guard can be silently
omitted:

```python
def handle_utterance(
    self, audio: ndarray, text: str
) -> UtteranceKind:
    with self._lock:
        # Guard 1 — session inactive: no-op, matches existing behaviour for lost races
        if not self._active:
            return "buffered"

        normalized = _normalize_spoken(text)

        # Guard 2 — end word: existing path, unchanged
        if normalized == self._end_word:
            self._pending_end.set()
            return "end"

        # Guard 3 — cancel word: new path; do NOT append to buffer
        if self._cancel_word is not None and normalized == self._cancel_word:
            return "cancel"

        # Default — buffer the audio
        self._buffer.append(audio)
        return "buffered"
```

#### `daemon.py` — dictation dispatch block

Where the pipeline currently handles `kind == "end"`, add the `"cancel"` branch:

```python
kind = self._dictation_session.handle_utterance(utterance, result.text)
if kind == "end":
    audio = self._dictation_session.take_and_finish()
    if audio is not None:
        self._dictation_executor.submit(self._finalize_dictation, audio)
elif kind == "cancel":
    self._dictation_session.cancel()   # existing method, reused unchanged
# "buffered" → fall through, nothing to do
run.set_status("ok")
return
```

#### Feedback — silent + visual only

No chime on spoken cancel — the cancellation is deliberate. The `dictation.end` event
with `{"reason": "cancel"}` is the only signal (this is the same event already emitted
by the scroll-lock-cancel path — there is NO new `dictation.cancelled` event).

The `dictation.end` handler in `src/voice_sprite/state_machine.py` (lines 98–104)
currently handles `dictation.end` → `dictating = False` without reading `data["reason"]`.
It must be updated to read `data["reason"]`; when `reason == "cancel"` it surfaces a
distinct "cancelled" visual cue IN ADDITION to setting `dictating = False`. The
mechanism follows existing sprite chat-log / cue patterns (implementer's discretion).
This retroactively improves the scroll-lock-cancel visual as well as covering spoken
cancel. Ship this sprite update in the same PR.

#### Config — `DictationConfig` gains `cancel_word`

`DictationConfig` in `src/voice_commander/config.py` gains:

```python
cancel_word: str = "cancel"
```

Note: the config `_section()` parser rejects unknown keys. The config key and the
Python field must be added together; they cannot be deployed independently.

Wherever `DictationSession(...)` is constructed in `src/voice_commander/daemon.py`,
pass `cfg.dictation.cancel_word` through.

`config.toml.example` gains the key in the `[dictation]` section:

```toml
[dictation]
endpoint    = "http://192.168.4.200:8765/inference"
end_word    = "done"
cancel_word = "cancel"   # say this word to abort dictation and discard the audio
```

---

### Race: simultaneous hotkey-end + spoken cancel

If a Ctrl hotkey-end is pending (`pending_end` set, sentinel already enqueued) and
the next utterance arriving in the queue is the cancel word:

1. The sentinel is consumed first (FIFO — it was enqueued before the user finished
   speaking "cancel"); the pipeline calls `continue`, re-evaluating `pending_end`.
2. The cancel utterance then arrives via `handle_utterance`, which returns `"cancel"`.
3. The pipeline calls `self._dictation_session.cancel()`.
4. Inside `cancel()`, the lock is held: `_active = False`, `_pending_end` cleared,
   `_buffer` discarded.
5. Later, when the drain timeout fires, `_finalize_pending_dictation_end` calls
   `take_and_finish()`, which returns `None` because `_active` is already `False`.
   No audio is submitted — no double-submit, no transcription.

**Cancel wins.** The atomic lock across both `cancel()` and `take_and_finish()` makes
this guarantee unconditional.

### Picker interaction

Dictation and the picker (ADR 0083) are mutually-exclusive voice-session sub-states.
`handle_utterance` runs only while dictation is active; picker selection is a
separate routing path. The dictation `cancel_word` and `PickerConfig.cancel_words`
therefore never conflict.

---

## Validation

### Unit tests

**`DictationSession` cancel word (`tests/unit/test_dictation_session.py`):**

- `handle_utterance` returns `"cancel"` on exact cancel-word match (normalized) and
  does NOT append audio to the buffer.
- Non-exact matches (partial, superset phrase) are buffered normally.
- Spoken cancel when session inactive returns `"buffered"` (existing inactive guard
  preserved — lost-race no-op).
- `cancel_word == end_word` collision: `__init__` logs a WARNING and sets
  `self._cancel_word = None`; subsequent `handle_utterance` with the collision word
  buffers normally (spoken cancel disabled).

**`HotkeyController` debounce (`tests/unit/test_hotkey.py`):**

- Two rapid releases of the same key within 50 ms dispatch the callback exactly once.
- Two releases spaced more than 50 ms apart dispatch the callback twice.
- Different keys are independently debounced: rapid alternation between two keys each
  dispatches its own callback once, regardless of inter-key timing.

**Sentinel wake (`tests/unit/test_pipeline.py` or `tests/unit/test_daemon.py`):**

- `_DICTATION_WAKE` sentinel in the queue is recognized by identity and triggers
  `continue` without calling `_process_utterance`.
- A real utterance tuple queued before the sentinel is processed; the sentinel then
  arrives and is skipped.

### Integration tests

**Hotkey-end with no trailing utterance** (`tests/integration/test_dictation_hotkey_end.py`):

The core regression test for the reported bug. Drive the pipeline with a stub
transcriber. Start dictation, buffer one utterance, then call `on_dictation_toggle()`
with no further utterance produced. Assert that dictation is finalized within 500 ms
(not infinite). This is the definitive integration gate for Change 1.

**Spoken cancel** (`tests/integration/test_dictation_cancel.py`):

- Say cancel word during active dictation → no `_finalize_dictation` call, no HTTP
  POST, no clipboard paste; `dictation.end` event emitted with `{"reason": "cancel"}`.
- Spoken cancel after hotkey-end-pending: assert cancel wins, no audio submitted,
  `dictation.end` emitted with `{"reason": "cancel"}`.

### Visual E2E (mandatory — `docs/agents/visual-e2e-testing.md`)

`scripts/dictation_hotkey_cancel_e2e.py` — a subprocess harness per the mandatory
protocol. Dictation touches hotkeys, daemon↔sprite IPC, and system clipboard — all
three triggers for the visual E2E requirement.

The harness must:

1. Start the daemon + sprite (subprocess), verify the sprite window is visible
   (`PrintWindow` / `GetForegroundWindow`).
2. **Hotkey-end test:** open a dictation session, send audio, fire the `dictation_key`
   via `SendInput` or `pynput` injection, assert that `dictation.end` SSE event
   arrives within 1 s with no trailing audio sent from the test.
3. **Spoken cancel test:** open a dictation session, inject the cancel-word transcript,
   assert `dictation.end` SSE event arrives with payload `{"reason": "cancel"}`; assert
   no clipboard change (the paste never happens).
4. Assert the sprite renders a distinct "cancelled" cue on `dictation.end {reason:"cancel"}`.
5. Capture screenshots as evidence; log all assertions to a test-evidence file.

Patterns to copy: `scripts/picker_modal_smoke.py` (in-process render),
`scripts/picker_visual_e2e.py` (subprocess + SSE + PrintWindow).

### Manual validation (HITL gate)

- Start a dictation session, say two or three words, press Right Ctrl without saying
  anything further. Dictation must finalize within ~500 ms — confirm the text is
  pasted at the cursor and the sprite returns to idle.
- Press Right Ctrl twice in rapid succession (simulate bounce). Confirm only one
  toggle fires (dictation starts, not starts-then-immediately-stops).
- Start a dictation session, say several words, then say "cancel". Confirm nothing is
  pasted, the sprite returns to idle, and the clipboard is unchanged.
- Confirm `end_word == cancel_word` in config produces a startup warning and spoken
  cancel is disabled (saying "cancel" buffers the word normally).

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| 50 ms debounce swallows a fast intentional re-press | Negligible — 50 ms is bounce-rejection only; deliberate re-presses are always >100 ms | Constant is well below any intentional press interval; does not affect scroll-lock |
| Sentinel lost if queue is full at press time | Very low — maxsize=8, pipeline drains fast | `put_nowait` catches `queue.Full`, logs warning, does nothing; full queue = pipeline already active, no sentinel needed |
| Spoken cancel false-trigger in prose | Low — user-configurable | User chooses a phrase unlikely in their dictation; match is exact-normalized, not substring |
| Sentinel type annotation stale | Low | Spec explicitly calls out the annotation widening as a required change |
| Sprite `dictation.end {reason:"cancel"}` silently unhandled | Likely without this change | Sprite update ships in same PR; integration test asserts on SSE event + reason field |

---

## Docs to update (same change as code)

The following must be updated in the same PR as the implementation — a doc that
contradicts the code is a defect:

- **New ADR** `docs/decisions/0089-dictation-hotkey-sentinel-cancel-debounce.md` —
  records the sentinel-wake approach (including `put_nowait` full-queue policy), the
  50 ms debounce constant and its rationale, and the spoken-cancel design (reuse of
  existing `cancel()`); cites ADR 0086 as the dictation-mode predecessor.
- **`docs/agents/technical-decisions.md`** — add the ADR 0089 row.
- **`CLAUDE.md` "Current state" dictation paragraph** — describe sentinel-wake
  hotkey-end, 50 ms debounce on all hotkeys, and the spoken `cancel_word` exit path.
- **`config.toml.example`** — add `cancel_word = "cancel"` to `[dictation]` with
  the inline comment shown in Change 3 above.
- **ADR 0086** — add a "Successor" note citing ADR 0089 for the hotkey-end reliability
  fix. The core dictation design is unchanged; ADR 0086 remains authoritative for the
  initial design; ADR 0089 is the patch.
- **`docs/index.md`** — no new overview doc is warranted; the ADR suffices.
