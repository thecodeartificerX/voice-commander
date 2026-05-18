# ADR 0090 — Right Ctrl Opens a Self-Contained Dictation Session

**Status:** Accepted
**Date:** 2026-05-18
**Amends:** [ADR 0086](0086-dictation-mode.md) Decision D1 — dictation is no longer strictly a sub-state of a Scroll Lock session for the Ctrl-opened case.
**Extends:** [ADR 0089](0089-dictation-hotkey-sentinel-cancel-debounce.md) — hotkey-end sentinel, debounce, spoken cancel.

## Context

ADR 0086 D1 stated that dictation is entered/exited "without touching StreamingRecorder
session state" — dictation was always a sub-state of an already-open Scroll Lock voice
session. Pressing Right Ctrl with no Scroll Lock session open played a miss chime and
returned. Users expect Right Ctrl to start transcription directly — no prior Scroll Lock
press required.

## Decision

### D1 — New daemon state field

`Daemon.__init__` gains `self._session_opened_by_dictation: bool = False`.
This flag is `True` only when the current voice session was opened by a Right Ctrl
press (not by Scroll Lock). Every close route resets it to `False`.

### D2 — Extract `_open_voice_session` / `_close_voice_session` helpers (pure refactor)

The open and close arms of `on_scroll_lock` are extracted into two protected helpers.
`on_scroll_lock` is rewritten to delegate to them. Observable behaviour of Scroll Lock
is byte-for-byte unchanged.

`_open_voice_session() -> bool`: opens recorder, sets `_session_active`, publishes
`session_started` → `unmuted`. Returns `True` on success, `False` on recorder failure.
Does NOT touch `_session_opened_by_dictation`.

`_close_voice_session() -> None`: bumps `_audio_gen`, calls `recorder.close_session()`,
drains `_utt_q`, sets `_session_active = False`, resets `_session_opened_by_dictation = False`,
cancels active dictation/elements sessions, publishes `muted` → `session_stopped`.

Intentional divergences from verbatim extraction: (a) adds `if self._recorder is None: return`
guard (helper callable independently); (b) generic exception log message (multiple callers);
(c) adds `_session_opened_by_dictation = False` (new field).

### D3 — `on_dictation_toggle` idle branch

When `_session_active == False`: call `_open_voice_session()`; if it returns `False`
return early (error already surfaced); on `True` set `_session_opened_by_dictation = True`
then `_dictation_session.start()`. The Scroll Lock (`_session_active == True`) branch
is unchanged.

### D4 — `_end_owned_session_if_needed` helper

```python
def _end_owned_session_if_needed(self) -> None:
    """If this session was opened by Right Ctrl, close it now.

    MUST only ever be invoked by submitting it to _dictation_executor —
    NEVER called directly on the pipeline thread (deadlock hazard).
    """
    if self._session_opened_by_dictation:
        self._close_voice_session()
```

### D5 — Close-before-finalize ordering (BLOCKER requirement)

In all three dictation-end paths, `_end_owned_session_if_needed` is submitted to
`_dictation_executor` **before** `_finalize_dictation`:

- **End-word path** (`_process_utterance`, `kind=="end"`): submit `_end_owned_session_if_needed`
  unconditionally after `take_and_finish()`, then submit `_finalize_dictation` only if
  `audio is not None`.
- **Hotkey-end path** (`_finalize_pending_dictation_end`): submit `_end_owned_session_if_needed`
  unconditionally after `take_and_finish()`, before the `if audio is not None:` branch.
- **Spoken-cancel path** (`_process_utterance`, `kind=="cancel"`): `cancel()` then submit
  `_end_owned_session_if_needed`.

Rationale: `_dictation_executor` is FIFO single-worker. Close-first means the recording
stops promptly (≤5 s VAD-worker join) before the network POST begins. The captured audio
is passed by value and is unaffected by the close.

### D6 — HTTP timeout reduction

`_TIMEOUT_S` in `src/voice_commander/dictation/remote.py` is reduced from 300.0 to 30.0.
A hung POST at 300 s would block `_dictation_executor` for all subsequent dictation
operations. 30 s gives ample headroom (reference hardware: ~1.2 s for a 36 s clip) without
allowing an indefinite executor stall.

### D7 — Shutdown cleanup (REV 3 placement)

`Daemon.shutdown()` gains a direct `self._dictation_session.cancel()` call placed
**immediately after the session-close block and pipeline join, and BEFORE
`_dictation_executor.shutdown(wait=False)`**.

Rationale for placement: `cancel()` only sets flags and publishes `dictation.end` on the
event bus — it does not use the executor — so it is safe to call before executor teardown.
Publishing `dictation.end` before executor shutdown keeps event ordering clean.
`executor.shutdown(wait=False)` abandons queued tasks; any `_end_owned_session_if_needed`
already queued will not execute. The direct cancel call is the only reliable path to clear
the dictating state and prevent the sprite from being stuck in the `dictating` visual state.

## Consequences

### Positive

- Right Ctrl becomes a complete transcribe toggle — no prior Scroll Lock press required.
- Recording-stop chime fires before text is pasted (close-first UX): chime signals
  "captured, processing"; paste lands shortly after network inference completes.
- HTTP timeout bounded at 30 s; a hung inference server sounds a miss chime and
  unblocks the executor within 30 s.
- Shutdown no longer leaves sprite in a stale `dictating` state.

### Negative

- `_session_active` and `_session_opened_by_dictation` are now written from two threads
  (hotkey-listener and `_dictation_executor` worker). Consistent with the existing
  `_session_active` / `_audio_gen` cross-thread write pattern; GIL provides required
  atomicity for plain attribute writes. No lock is added.
- Recording-stop chime fires before paste — the user hears "done" before text appears.
  This is the intended UX and is documented as "captured, processing".

### Neutral

- Scroll Lock open/close behaviour is byte-for-byte unchanged (regression-tested).
- No new config key, no new LLM-visible primitive, no new SSE event type.
- Abandoned Ctrl-opened session (user presses Right Ctrl then never speaks) stays open
  indefinitely — identical to an abandoned Scroll Lock session; no auto-timeout.

## Thread safety

`_close_voice_session` is called from the hotkey-listener thread (via `on_scroll_lock`)
and submitted to `_dictation_executor` (via `_end_owned_session_if_needed`). Both
`recorder.close_session()` and `DictationSession.cancel()` are idempotent under their
own internal locks. A double-close race is benign. The 5 s VAD-worker join in
`close_session()` is safe on the executor thread because the join target is the
vad-worker (not the pipeline thread), which drains independently.

## References

- [ADR 0086](0086-dictation-mode.md) — dictation mode (amended D1)
- [ADR 0089](0089-dictation-hotkey-sentinel-cancel-debounce.md) — hotkey-end sentinel + spoken cancel
- [ADR 0048](0048-eventbus-sse-outbound-telemetry.md) — EventBus / SSE event wire format
- `src/voice_commander/daemon.py` — `_session_opened_by_dictation`, `_open_voice_session`, `_close_voice_session`, `_end_owned_session_if_needed`, `on_dictation_toggle`, `_process_utterance`, `_finalize_pending_dictation_end`, `shutdown`
- `src/voice_commander/dictation/remote.py` — `_TIMEOUT_S`
- `tests/integration/test_dictation_opens_session.py` — integration test suite
- `scripts/dictation_opens_session_e2e.py` — visual E2E harness
