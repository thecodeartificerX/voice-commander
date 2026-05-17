# ADR 0089 — Dictation Hotkey-End Sentinel Wake, 50 ms Debounce, and Spoken Cancel

**Status:** Accepted
**Date:** 2026-05-18
**Extends:** [ADR 0086](0086-dictation-mode.md) — dictation mode / remote transcription pipeline

## Context

ADR 0086 introduced two exit paths from dictation mode:

1. **End-word path** — the pipeline thread recognises the spoken end word (default
   `"done"`) via `DictationSession.handle_utterance()`, then calls `take_and_finish()`
   and submits audio to `_finalize_dictation`.
2. **Hotkey-end path** — a second `dictation_key` press calls `on_dictation_toggle()`,
   which calls `DictationSession.request_end()` to set `_pending_end`. The pipeline
   thread is supposed to detect this flag, switch to a short-timeout queue drain, and
   eventually call `_finalize_pending_dictation_end()`.

Three reliability problems with the original design were identified and confirmed by
code inspection:

### Problem 1 — Hotkey-end pipeline race (root cause of hotkey-end unreliability)

The pipeline worker (`vc-pipeline` thread) runs `_pipeline_loop()`. At the top of
each loop iteration it evaluates whether `pending_end` is set and picks
`get(timeout=_DICTATION_DRAIN_TIMEOUT_S)` vs `get(timeout=None)`. The race is:

1. The pipeline thread has already entered `self._utt_q.get(timeout=None)` and is
   blocked inside `Queue.get`, waiting indefinitely for the next utterance.
2. The user presses `dictation_key`. The hotkey thread calls `request_end()`, setting
   `_pending_end = True`.
3. `_pending_end` is now `True`, but the pipeline thread only re-evaluates it at the
   **top of its next iteration**, which cannot happen while `get()` is still blocked.
4. If the user stops speaking after the press (the normal case), no new utterance
   arrives, `get(timeout=None)` never returns, and the pipeline is effectively hung
   until the next utterance or daemon shutdown.

This is not a permanent hang — it resolves the moment any utterance or the shutdown
`None` sentinel arrives — but from the user's perspective dictation never finalizes.
The end-word path is unaffected because the spoken word itself unblocks `get()`.

### Problem 2 — No debounce on `HotkeyController._on_release`

Some keyboard drivers fire two rapid `on_release` events for a single physical key
press (hardware bounce or driver double-release). With `dictation_key`, two releases
within a few milliseconds toggle dictation on and then immediately off; the net
visible effect is "nothing happened", which looks identical to the hotkey-end race.

### Problem 3 — No spoken cancel path

There was no way to abandon a dictation session without finalizing it. The user had
to say "done", wait for the transcription to paste, and then undo. No discard path
existed.

## Decision

Three targeted changes are made. They are independent but ship together because they
collectively close the reported reliability gap.

### D1 — Sentinel wake for hotkey-end

A module-level sentinel object is defined in `daemon.py`:

```python
_DICTATION_WAKE = object()
```

In `on_dictation_toggle`, the branch that handles an active dictation session calls
`request_end()` and then immediately enqueues the sentinel:

```python
self._dictation_session.request_end()
try:
    self._utt_q.put_nowait(_DICTATION_WAKE)
except queue.Full:
    logger.warning(
        "dictation: _utt_q full — sentinel not needed; pipeline already active"
    )
```

**`put_nowait` full-queue policy:** on `queue.Full`, log a warning and do nothing.
A full queue (`maxsize=8`) means the pipeline is already non-idle, processing items;
it will naturally exit `get()` and re-evaluate `pending_end` within a few iterations.
The sentinel's sole purpose is to wake an **idle** pipeline. The pynput listener
thread must never block; `put(timeout=...)` is therefore rejected.

In `_pipeline_loop`, after the existing `None` shutdown-sentinel check, the wake
sentinel is consumed via identity check before item handling:

```python
if item is None:
    break
if item is _DICTATION_WAKE:
    continue   # re-enter loop top; pending_end is now True → short drain
```

`continue` returns to the loop top where `pending_end` is re-evaluated as `True`.
The pipeline immediately switches to `get(timeout=_DICTATION_DRAIN_TIMEOUT_S)`, and
the first timeout after an empty queue triggers `_finalize_pending_dictation_end()`.

**Ordering guarantee:** real utterances already queued ahead of the sentinel are
dequeued first (FIFO). The sentinel only supplies the missing wake signal; all
buffered audio is still captured by the 250 ms drain window before `take_and_finish()`
is called.

**Type annotation:** `_utt_q` is widened to accept `object` alongside the existing
`tuple[ndarray, int] | ndarray | None`:

```python
self._utt_q: queue.Queue[
    tuple[npt.NDArray[np.float32], int] | npt.NDArray[np.float32] | None | object
] = queue.Queue(maxsize=8)
```

### D2 — Per-key debounce in `HotkeyController`

`HotkeyController` gains a `dict[keyboard.Key, float]` (`_last_fire`) that maps each
bound key to the `time.monotonic()` timestamp of its last dispatched release. In
`_on_release`, before invoking the callback:

```python
now = time.monotonic()
last = self._last_fire.get(key, 0.0)
if now - last < _DEBOUNCE_S:
    logger.debug("Hotkey debounce: dropping double-release for %s ...", key)
    return
self._last_fire[key] = now
```

`_DEBOUNCE_S = 0.050` (50 ms). Rationale:

- Hardware key bounce and driver double-release events resolve well **under 50 ms**.
  The window is explicitly **not** intended to suppress intentional rapid re-presses
  by the user — those are always hundreds of milliseconds apart.
- Safe for `scroll_lock` and `dictation_key` alike: even the fastest mis-press
  correction requires the user's hand to be moving, which is far longer than 50 ms.
- Each key has its own independent timestamp in `_last_fire`, so fast alternation
  between two keys (e.g. Scroll Lock then Right Ctrl) is unaffected.
- No new configuration key is introduced; `_DEBOUNCE_S` is a code constant. If
  tuning is needed in the future it can be promoted to config at that point.
- The debounce guard is inside `_lock` so it is thread-safe.

### D3 — Spoken cancel word

#### Pre-existing `DictationSession.cancel()` is reused unchanged

`DictationSession.cancel()` already existed (it was called by the scroll-lock session-
close path). It atomically:

- Sets `_active = False`.
- Clears `_pending_end`.
- Discards `_buffer`.
- Publishes `dictation.end` with `{"reason": "cancel"}` if the session was active.

The spoken-cancel path calls this method unchanged. **No new method, no new event.**

#### `UtteranceKind` gains `"cancel"`

```python
UtteranceKind = Literal["buffered", "end", "cancel"]
```

#### `DictationSession.__init__` gains `cancel_word`

```python
def __init__(self, bus, end_word="done", cancel_word="cancel") -> None:
```

Cross-field validation in `__init__`:
- If the normalized `cancel_word` is empty/whitespace: log WARNING, set
  `self._cancel_word = None` (spoken cancel disabled).
- If the normalized `cancel_word` equals the normalized `end_word`: log WARNING, set
  `self._cancel_word = None` (spoken cancel disabled to prevent ambiguity).
- Otherwise: `self._cancel_word = normalized_cancel`.

#### `handle_utterance` cancel guard

After the end-word guard, before buffering:

```python
if self._cancel_word is not None and normalized == self._cancel_word:
    return "cancel"
```

The match is **exact normalized** (lowercase, strip punctuation). A longer phrase
containing the cancel word is buffered normally, not cancelled.

#### Daemon `_process_utterance` cancel branch

```python
kind = self._dictation_session.handle_utterance(utterance, result.text)
if kind == "end":
    audio = self._dictation_session.take_and_finish()
    if audio is not None:
        self._dictation_executor.submit(self._finalize_dictation, audio)
elif kind == "cancel":
    self._dictation_session.cancel()   # existing method, reused unchanged
# "buffered" → fall through
run.set_status("ok")
return
```

No chime on spoken cancel — the cancellation is deliberate user intent.

#### Config — `DictationConfig` gains `cancel_word`

```python
cancel_word: str = "cancel"   # say this word to abort dictation and discard audio
```

`config.toml.example` gains the key in the `[dictation]` section:

```toml
[dictation]
endpoint    = "http://192.168.4.200:8765/inference"
end_word    = "done"
cancel_word = "cancel"   # say this word to abort dictation and discard the audio
```

#### Sprite visual cue for cancellation

The `dictation.end` handler in `StateMachine` (`src/voice_sprite/state_machine.py`)
reads `data.get("reason")`. When `reason == "cancel"` it sets `self.cancelled_cue = True`
in addition to `self.dictating = False`. This flag covers both the spoken cancel path
and the scroll-lock session-close path (both call `DictationSession.cancel()` and both
emit `dictation.end {"reason": "cancel"}`). `cancelled_cue` is cleared to `False` on
`dictation.start`.

The rendering path in `voice_sprite/__main__.py` calls the module-level helper
`_apply_cancelled_cue(sm, window, pyglet.clock.schedule_once)` after every SSE event
that passes through `sm.on_event()`. When `sm.cancelled_cue` is `True`, the helper:

1. Calls `window.set_cancelled_cue(True)` to show the badge immediately.
2. Schedules a `pyglet.clock.schedule_once` callback at `_CANCELLED_CUE_DURATION_S`
   (2.5 s) that resets `sm.cancelled_cue = False` and calls
   `window.set_cancelled_cue(False)` to hide the badge.

`SpriteWindow.set_cancelled_cue()` in `src/voice_sprite/window.py` sets the
`_cancelled_cue` flag. `on_draw()` renders a "✕ CANCELLED" `pyglet.text.Label` in
red-orange `(255, 90, 90, 255)` at the bottom-centre of the sprite window when
`_cancelled_cue` is `True` — the same position and style as the existing
"● DICTATING" badge (yellow), but with distinct text and colour so the user can
tell at a glance that dictation was discarded.  The label is created lazily on first
draw and re-centred every frame (to handle CursorDock window resizes).

#### Race — simultaneous hotkey-end + spoken cancel

If `pending_end` is set and the cancel word arrives as the next utterance:

1. The sentinel is dequeued first (FIFO — enqueued before the utterance finished).
   Pipeline calls `continue`, re-evaluating `pending_end = True`.
2. The cancel utterance arrives; `handle_utterance` returns `"cancel"`.
3. Pipeline calls `cancel()`: `_active = False`, `_pending_end` cleared, buffer
   discarded. `dictation.end {"reason":"cancel"}` published.
4. Later, when the drain timeout fires, `_finalize_pending_dictation_end` calls
   `take_and_finish()`, which returns `None` because `_active` is already `False`.
   No audio is submitted — no double transcription.

**Cancel wins.** The atomic lock across both `cancel()` and `take_and_finish()` makes
this guarantee unconditional.

#### Picker mutual exclusion

Dictation and the picker (ADR 0083) are mutually-exclusive voice-session sub-states.
`handle_utterance` runs only while dictation is active; picker selection is a separate
routing path that is unreachable during dictation. The dictation `cancel_word` and
`PickerConfig.cancel_words` therefore never conflict.

## Consequences

### Positive

- **Hotkey-end is now reliable.** A second `dictation_key` press finalizes dictation
  within ≤ 250 ms (`_DICTATION_DRAIN_TIMEOUT_S`) even when the user speaks nothing
  further. The previous race — pipeline blocked in `get(timeout=None)` with no new
  utterance incoming — is eliminated.
- **No false double-toggle from driver bounce.** Two rapid `on_release` events within
  50 ms for the same key dispatch the callback exactly once.
- **Users can discard a dictation session** without transcribing or pasting, by saying
  a configurable word (default `"cancel"`). Nothing is POSTed, nothing is pasted,
  clipboard is unchanged.
- **Spoken cancel is a zero-cost code path.** It reuses the pre-existing `cancel()`
  method; no new infra.
- **Retroactive improvement to scroll-lock cancel.** The sprite's `cancelled_cue`
  visual covers all `reason="cancel"` `dictation.end` events, not only spoken cancel.
- Tool catalogue unchanged at 11 primitives.
- No new config section; `cancel_word` is a single key under `[dictation]`.

### Negative

- `_utt_q` type annotation must be kept consistent with the `object` widening — a
  sentinel that is silently treated as a real utterance would cause a crash. The
  identity check (`item is _DICTATION_WAKE`) is the guard.
- `cancel_word` is per-install; no per-session or per-profile cancel words.
- **Cancellation after finalization starts is not supported** — the POST is already
  in flight once `_finalize_dictation` has been submitted to the executor.

### Neutral

- The 50 ms debounce constant is not user-configurable (code constant only). This
  is deliberate — it is a bounce-rejection threshold, not a repeat-rate control.
- Spoken cancel with `cancel_word == end_word` logs a WARNING at daemon startup and
  disables spoken cancel silently; this is documented but not surfaced to the user
  beyond the log.

## Alternatives considered

1. **Polling loop instead of sentinel (approach B)** — replace `get(timeout=None)`
   with a tight polling loop that checks `pending_end`. Rejected: constant thread
   wakeups even when no dictation is active; increases idle CPU usage; adds complexity
   to the non-dictation hot path.
2. **Finalize directly in `on_dictation_toggle` (approach C)** — skip the drain; call
   `take_and_finish()` from the hotkey thread. Rejected: races with concurrent buffer
   appends from the pipeline thread; skips the 250 ms drain window that captures
   in-flight utterances.
3. **`put(timeout=...)` fallback when queue is full** — rejected: the pynput listener
   thread must never block; a full queue means the pipeline is already active.
4. **Dedicated cancel hotkey** — a new key binding for cancel. Rejected as non-goal;
   spoken word is lower friction and does not require a spare key.
5. **Per-session cancel words (list)** — rejected for v1; a single `cancel_word` per
   install covers the use case without configuration complexity.

## References

- [ADR 0086](0086-dictation-mode.md) — dictation mode this extends (predecessor)
- [ADR 0088](0088-dictation-custom-vocabulary.md) — custom vocabulary / postprocess pipeline
- [ADR 0083](0083-bare-primitive-picker.md) — picker sub-state (mutual exclusion note)
- [ADR 0048](0048-eventbus-sse-outbound-telemetry.md) — EventBus / SSE event wire format
- `src/voice_commander/daemon.py` — `_DICTATION_WAKE`, `on_dictation_toggle`, `_pipeline_loop`, `_process_utterance`
- `src/voice_commander/dictation/session.py` — `DictationSession`, `UtteranceKind`, `cancel()`
- `src/voice_commander/hotkey.py` — `HotkeyController`, `_DEBOUNCE_S`, `_last_fire`
- `src/voice_commander/config.py` — `DictationConfig.cancel_word`
- `src/voice_sprite/state_machine.py` — `StateMachine.cancelled_cue`
