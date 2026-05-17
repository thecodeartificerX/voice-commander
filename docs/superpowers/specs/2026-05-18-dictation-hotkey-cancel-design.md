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

The race condition is:

1. The pipeline thread has already entered `self._utt_q.get(timeout=None)` — it is
   blocked inside the `Queue.get` syscall.
2. The user presses Right Ctrl. The hotkey thread calls `request_end()`, which sets
   `_pending_end`.
3. `_pending_end` is now `True`, but the `pending` variable evaluated at the
   **top of the previous iteration** was `False`. The pipeline thread is stuck inside
   `get(timeout=None)` and will not re-evaluate `pending` until `get()` returns.
4. If the user stops speaking after the press (the normal case), no new utterance
   arrives. `get(timeout=None)` **never returns**. The pipeline is permanently
   blocked. Dictation is effectively hung.

**Why "done" always works:** the spoken word itself is an utterance that wakes the
blocked `get()`. Once `get()` returns, the loop re-evaluates `pending`, routes
through `handle_utterance`, recognises `"done"`, and finalises.

**Why hotkey-end sometimes works ("hit" cases):** a stray VAD noise event or one
more spoken word produces an utterance, waking `get()`. The loop then sees
`pending_end == True`, switches to the timed drain, and eventually finalises.
Silence after the key press = permanent hang.

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
| `_DICTATION_DRAIN_TIMEOUT_S` | `daemon.py` | L61 |
| `_pipeline_loop` | `daemon.py` | L430–449 |
| `on_dictation_toggle` | `daemon.py` | L395–412 |
| `_finalize_pending_dictation_end` | `daemon.py` | L725–748 |
| `_finalize_dictation` | `daemon.py` | L750–813 |
| `DictationSession` | `dictation/session.py` | entire file |
| `UtteranceKind` | `dictation/session.py` | L47 |
| `HotkeyController._on_release` | `hotkey.py` | L52–60 |
| `DictationConfig` | `config.py` | L26–35 |
| `HotkeyConfig` | `config.py` | L17–25 |

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

**Chosen approach (A):** `on_dictation_toggle`, in the branch where dictation is
active, enqueues a module-level sentinel object immediately after calling
`request_end()`:

```python
# daemon.py — module level
_DICTATION_WAKE = object()

# on_dictation_toggle (abridged):
if self._dictation_session.active:
    self._dictation_session.request_end()
    self._utt_q.put_nowait(_DICTATION_WAKE)   # ← new
    logger.info("dictation: hotkey-end requested; pipeline will drain and finalize")
```

The pipeline loop, on receiving an item, checks the sentinel by identity before the
existing `isinstance(item, tuple)` branch:

```python
if item is _DICTATION_WAKE:
    continue   # re-enter loop top; pending_end is now True → short-timeout drain
```

The `continue` returns to the top of the loop, where `pending` is re-evaluated as
`True`. The pipeline immediately switches to `get(timeout=_DICTATION_DRAIN_TIMEOUT_S)`,
and the first timeout after an empty queue triggers `_finalize_pending_dictation_end()`.

**Why not approach B** (replace `get(timeout=None)` with a polling loop on
`pending_end`): constant thread wakeups every N ms even when no dictation is active;
increases idle CPU usage; adds complexity to the non-dictation hot path.

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

**`put_nowait` safety:** the utterance queue has `maxsize=8`. The sentinel is added
once per hotkey press. A queue full at the point of the press (≥8 pending utterances)
raises `queue.Full`; this should be caught and logged (not raised) with a fallback of
calling `_utt_q.put(_DICTATION_WAKE)` with a short timeout (50 ms). In practice a
full queue means the pipeline is already processing speech, so it will naturally exit
the blocking `get()` soon anyway.

---

### Change 2 — Per-key debounce in `HotkeyController`

`HotkeyController` stores a `dict[keyboard.Key, float]` mapping each bound key to the
`time.monotonic()` of its last dispatched release. In `_on_release`, before invoking
the callback:

```python
now = time.monotonic()
last = self._last_fire.get(key, 0.0)
if now - last < _DEBOUNCE_S:
    logger.debug("Hotkey debounce: dropping double-release for %s", key)
    return
self._last_fire[key] = now
```

`_DEBOUNCE_S = 0.30` (300 ms). This value is:

- **Short enough** not to swallow real intentional presses: dictation toggle presses
  are always several seconds apart in normal use; even rapid users pause at least 400 ms.
- **Long enough** to absorb driver bounce: hardware bounce typically resolves within
  10–50 ms; 300 ms gives a 6× safety margin.

The debounce applies uniformly to all bound keys, including `scroll_lock`. Each key
has its own independent timer in `_last_fire`, so fast alternation between two keys
(e.g. Scroll Lock then Right Ctrl) is unaffected.

No new configuration key is introduced; the 300 ms constant is internal. If tuning is
needed in the future, it can be promoted to config at that point.

---

### Change 3 — Spoken cancel word

**`DictationSession` changes:**

`UtteranceKind` gains a `"cancel"` member:

```python
UtteranceKind = Literal["buffered", "end", "cancel"]
```

`DictationSession.__init__` gains a `cancel_word` parameter (default `"cancel"`),
normalized the same way as `end_word`:

```python
def __init__(self, bus: _BusLike, end_word: str = "done",
             cancel_word: str = "cancel") -> None:
    ...
    self._cancel_word = _normalize_spoken(cancel_word)
```

`handle_utterance` checks for the cancel word after the end-word check, and does NOT
append the matched utterance to the buffer:

```python
if _normalize_spoken(text) == self._cancel_word:
    return "cancel"
self._buffer.append(audio)
return "buffered"
```

A new `cancel()` method is added to `DictationSession` — it does not exist today.
The current public methods are `start`, `handle_utterance`, `request_end`,
`take_and_finish`, plus the `pending_end` and `active` properties. The new method
signature is:

```python
def cancel(self) -> bool:
```

Behaviour: under `self._lock`, if `not self._active` return `False`; else set
`_active = False`, `_pending_end.clear()`, `_buffer = []`; then OUTSIDE the lock
publish the `dictation.cancelled` event and log; return `True`.

**`daemon.py` changes** (in `_process_utterance`, the dictation dispatch block):

```python
kind = self._dictation_session.handle_utterance(utterance, result.text)
if kind == "end":
    audio = self._dictation_session.take_and_finish()
    if audio is not None:
        self._dictation_executor.submit(self._finalize_dictation, audio)
elif kind == "cancel":
    self._dictation_session.cancel()
# "buffered" → fall through, nothing to do
run.set_status("ok")
return
```

**Feedback:** no chime on spoken cancel — the cancellation is deliberate and silent.
The new `dictation.cancelled` SSE event (payload `{"reason": "spoken"}`) is the only
signal. This is a NEW event type — it must be created, not reused from `dictation.end`.
The sprite process must gain a handler for `dictation.cancelled` that renders a
cancelled state distinct from the normal `dictation.end` finish animation. This update
ships in the same change.

**Config:** `DictationConfig` gains:

```python
cancel_word: str = "cancel"
```

Validation at config-load time: if `cancel_word == end_word`, log a WARNING and reset
`cancel_word` to `""` (disabling spoken cancel), consistent with how the existing
`dictation_key == hotkey_key` clash is handled (warn + ignore the conflicting binding).

`config.toml.example` gains the key in the `[dictation]` section:

```toml
[dictation]
endpoint = "http://192.168.4.200:8765/inference"
end_word  = "done"
cancel_word = "cancel"   # say this word to abort dictation and discard the audio
```

---

### Race: simultaneous hotkey-end + spoken cancel

If `pending_end` is set (hotkey pressed) and the next utterance to arrive is the
cancel word:

1. `handle_utterance` returns `"cancel"` — the cancel word is NOT buffered.
2. The pipeline calls `self._dictation_session.cancel()`.
3. Inside `cancel()`, the lock is held: `_active` is set `False`, `_pending_end` is
   cleared, `_buffer` is discarded.
4. Later, when the sentinel or timeout fires, `_finalize_pending_dictation_end` calls
   `take_and_finish()`, which immediately returns `None` because `_active` is already
   `False`. No audio is submitted — no double-submit, no transcription.

**Cancel wins.** The atomic lock across both `cancel()` and `take_and_finish()` makes
this guarantee unconditional: whichever path holds the lock first determines the
outcome, and both paths guard on `_active` before proceeding.

---

## Validation

### Unit tests

**`DictationSession` cancel word (`tests/unit/test_dictation_session.py`):**

- `handle_utterance` returns `"cancel"` when the utterance exactly matches
  `cancel_word` (normalized).
- Cancel-word utterance is NOT appended to the buffer.
- Non-exact matches (partial, superset phrase) are buffered normally.
- Spoken cancel when session inactive returns `"buffered"` (lost-race no-op — mirrors
  existing `handle_utterance` idle behaviour).
- `cancel() -> bool`: first call on an active session clears buffer, sets
  `_active = False`, publishes `dictation.cancelled` with `{"reason": "spoken"}`,
  and returns `True`; second call (session already inactive) returns `False` with no
  side effects (idempotent no-op). Tests assert both the return value AND the
  observable side-effects (buffer cleared, `_active == False`, event published).
- `take_and_finish()` after `cancel()` returns `None`.
- End-word + cancel-word equality → config-load warning + spoken cancel disabled.

**`HotkeyController` debounce (`tests/unit/test_hotkey.py`):**

- Two rapid releases of the same key within `_DEBOUNCE_S` dispatch the callback once.
- Two releases spaced beyond `_DEBOUNCE_S` dispatch the callback twice.
- Different keys are independently debounced (rapid alternation between two keys each
  dispatches once, regardless of inter-key timing).

**Sentinel wake (`tests/unit/test_pipeline.py` or
`tests/unit/test_daemon.py`):**

- `_DICTATION_WAKE` sentinel in the queue is recognized by identity and triggers a
  `continue` without calling `_process_utterance`.
- A real utterance tuple queued before the sentinel is processed; the sentinel then
  arrives and is skipped.

### Integration tests

**Hotkey-end with no trailing utterance** (`tests/integration/test_dictation_hotkey_end.py`):

The core regression test for the reported bug. Drive the pipeline with a stub
transcriber. Start dictation, buffer one utterance, then call `on_dictation_toggle()`
with no further utterance produced. Assert that dictation is finalized within 500 ms
(≪ infinite). This is the definitive integration gate for Change 1.

**Spoken cancel** (`tests/integration/test_dictation_cancel.py`):

- Say cancel word during active dictation → no `_finalize_dictation` call, no HTTP
  POST, no clipboard paste; `dictation.cancelled` event emitted with
  `{"reason": "spoken"}`.
- Spoken cancel after hotkey-end-pending: assert cancel wins (`cancel() -> True`),
  no audio submitted.

### Visual E2E (mandatory — `docs/agents/visual-e2e-testing.md`)

`scripts/dictation_hotkey_cancel_e2e.py` — a subprocess harness per the mandatory
protocol. Dictation touches hotkeys, daemon↔sprite IPC, and system clipboard — all
three triggers for the visual E2E requirement.

The harness must:

1. Start the daemon + sprite (subprocess), verify the sprite window is visible
   (`PrintWindow` / `GetForegroundWindow`).
2. **Hotkey-end test**: open a dictation session, send audio, fire the `dictation_key`
   via `SendInput` or `pynput` injection, assert that `dictation.end` SSE event
   arrives within 1 s with no trailing audio sent from the test.
3. **Spoken cancel test**: open a dictation session, inject the cancel-word transcript,
   assert `dictation.cancelled` SSE event arrives with payload `{"reason": "spoken"}`;
   assert no clipboard change (the paste never happens).
4. Capture screenshots as evidence; assert on the event type (`dictation.cancelled`)
   and `reason` field (`"spoken"`) in the SSE payload.
5. Log all assertions to a test-evidence file.

Patterns to copy: `scripts/picker_modal_smoke.py` (in-process render), `scripts/picker_visual_e2e.py` (subprocess + SSE + PrintWindow).

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
| 300 ms debounce swallows a fast intentional re-press | Low — real gap is multi-second | Constant chosen conservatively; promotable to config if users report it |
| Sentinel lost if queue is full at press time | Very low — maxsize=8 and pipeline drains fast | `put_nowait` catches `queue.Full`; logs warning; falls back to short-timeout `put` |
| `dictation.cancelled` event silently ignored by sprite without update | Likely without this change | Ship sprite `dictation.cancelled` handler in the same PR; integration test asserts on event type and `{"reason": "spoken"}` payload |
| Cancel word false-trigger in spoken prose | Low — user-configurable | User chooses a phrase unlikely in their dictation; word-boundary match prevents mid-word hits |
| Sentinel identity check fragile if queue item type changes | Negligible | Module-level `object()` with identity (`is`) check is Python-idiomatic and immune to equality overrides |

---

## Docs to update (same change as code)

The following must be updated in the same PR as the implementation — a doc that
contradicts the code is a defect:

- **New ADR** `docs/decisions/0089-dictation-hotkey-sentinel-cancel-debounce.md` —
  records the sentinel-wake approach, the debounce constant, and the spoken-cancel
  design decision; cites ADR 0086 as the dictation-mode predecessor.
- **`docs/agents/technical-decisions.md`** — add the ADR 0089 row.
- **`CLAUDE.md` "Current state" dictation paragraph** — describe sentinel-wake
  hotkey-end, 300 ms debounce on all hotkeys, and the spoken `cancel_word` exit path.
- **`config.toml.example`** — add `cancel_word = "cancel"` to `[dictation]` with
  the inline comment shown in Change 3 above.
- **ADR 0086** — add a "Successor" note citing ADR 0089 for the hotkey-end reliability
  fix. The core dictation design is unchanged; ADR 0086 remains authoritative for
  the initial design; ADR 0089 is the patch.
- **`docs/index.md`** — no new overview doc is warranted; the ADR suffices.

---

## Open questions (deferred to implementation plan)

- Exact `put_nowait` / `put(timeout)` fallback policy for a full utterance queue at
  sentinel-enqueue time — document in the ADR's implementation notes.
