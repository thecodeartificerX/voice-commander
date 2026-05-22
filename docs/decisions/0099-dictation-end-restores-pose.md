# ADR 0099 — Dictation end restores the pre-dictation pose (cat un-dims)

**Status:** Accepted
**Date:** 2026-05-22

## Context

ADR 0096 added a `PROCESSING` sprite state for the dictation decode wait, and
ADR 0097 made the cat **dim** whenever `is_dim(state, processing)` is true —
where `PROCESSING` is one of the dim states.

The `dictation.processing` handler parks **both** `current_state` and
`target_state` at `PROCESSING`. The `dictation.end` handler cleared the
`processing` flag and set `cancelled_cue`, but **never moved the state off
`PROCESSING`** and returned `None`. Because `is_dim(PROCESSING, …)` is true
regardless of the flag, the cat stayed dimmed after the server round-trip
finished — even though, in a Scroll-Lock session, the mic was hot again. The
`None` return also meant the renderer never left the frozen `PROCESSING` pose.

Both dictation-end code paths hit this:

- **Scroll-Lock session (dictation as a sub-state):** no `session_stopped` is
  published; after `dictation.end` the session is still listening, so the cat
  should be **bright LISTENING** — but it stayed dim `PROCESSING`. *(bug)*
- **Right-Ctrl-owned session:** `_end_owned_session_if_needed` publishes
  `session_stopped` (→ IDLE) *before* `_finalize_dictation` publishes
  `dictation.processing` (→ PROCESSING), so the cat ended dim — correct dim, but
  via the wrong (PROCESSING) pose rather than IDLE.

## Decision

The StateMachine tracks session liveness and restores the pre-dictation pose on
`dictation.end`:

- New `StateMachine.session_active` flag, set `True` on `session_started` and
  `False` on `session_stopped` (these events still fall through to
  `EVENT_STATE_MAP` for their own transitions).
- `dictation.end` now sets `current_state` and `target_state` to
  **`LISTENING` if `session_active` else `IDLE`**, and **returns** that state so
  the `__main__.py` `on_event` handler calls `renderer.set_state(...)` and the
  cat leaves the frozen `PROCESSING` pose. `_apply_dim` then reads the restored
  `target_state` and un-dims (LISTENING) or keeps dim (IDLE).

Deriving the restore target from `session_active` — rather than caching the
literal pre-`PROCESSING` state — also handles the race where the user presses
Scroll Lock **during** the decode wait: `session_stopped` flips the flag, so
`dictation.end` correctly restores to dim `IDLE` instead of snapping back to
bright.

The cancel path (`dictation.end {reason: "cancel"}`, no prior
`dictation.processing`) goes through the same restore: an open session stays
bright, a closed one is `IDLE`. `is_dim` itself is unchanged — `PROCESSING`
remains a dim state for the actual decode window.

## Consequences

- After dictation finishes, the cat returns to its original visual: bright when
  still listening, dim when the session has closed — the end-criterion the user
  reported.
- `on_event("dictation.end", …)` now returns a `SpriteState` instead of `None`;
  two unit tests that asserted the old `None` contract were updated to the
  restored-state contract (their real intent — flag clearing — is unchanged).
- The visual E2E harness `scripts/sprite_dim_e2e.py` gained a **post-end** phase
  (emit `dictation.end` after `dictation.processing` with the session still
  open) and asserts that frame is bright, not dim. It passed: post-end metric
  **246.1** vs the dim processing/idle metric **94.9** (bright ÷ 0.6 guard).
- The harness now prepends its own `src/` to the subprocess `PYTHONPATH`, so the
  launched sprite imports the **local checkout** rather than wherever the
  editable-install `.pth` points — without this, a sprite launched from a git
  worktree silently runs the main working copy's code, which is exactly how this
  fix initially appeared not to work under E2E.

## References

- Dim-when-not-listening rule: ADR 0097
- `PROCESSING` state / server-side dictation: ADR 0096
- Owned-session close ordering (`_end_owned_session_if_needed` before
  `_finalize_dictation`): ADR 0090
- Tests: `tests/unit/test_sprite_dim_after_processing.py`,
  `tests/unit/test_sprite_state_machine.py`
- Harness: `scripts/sprite_dim_e2e.py`
