# ADR 0097 — Sprite dims when not listening, bright when listening

**Status:** Accepted
**Date:** 2026-05-21

## Context

The `voice_sprite` cat tracked 10+ states with an inconsistent visual story: the
only runtime tint was grey `(128,128,128)` applied when `muted` **or**
`dictating`, plus a forced IDLE pose during dictation. Mute's mid-session toggle
was removed (ADR 0025), but the daemon still emits `muted`/`unmuted` as
session-boundary cues; the sprite consumed `muted` for the grey tint. Net effect:
the cat greyed out and "slept" exactly when the mic was hottest (dictation
capture) — backwards.

## Decision

One binary visual rule layered on the existing per-state animations:

- **Listening → bright** (`(255,255,255)`), playing the state's own animation.
- **Not listening → dimmed**, brightness multiplied to `dim_brightness` (default
  0.4), full opacity, still playing the state's own animation row.

The dim decision is the pure predicate `is_dim(state, processing)` in
`voice_sprite/state_machine.py`:

`dim = state in {IDLE, WARMUP, CRASHED, PROCESSING} or processing`

Dictation **capture** carries a session state with `processing=False` → bright;
dictation **decode** (`processing=True`) → dim. Badges (DICTATING / PROCESSING /
CANCELLED) draw at full opacity on top, regardless of dim.

The predicate is fed the StateMachine's **`target_state`**, not `current_state`:
`current_state` lags on an `ANIMATED_TRANSITIONS` pair (e.g. IDLE→LISTENING)
because nothing advances it past the play-once animation, so it would hold the
dim source state; the renderer is likewise driven by the target state. The decision is applied through a small module-level helper
`_apply_dim(sm, window)` (mirroring `_apply_processing_state` /
`_apply_cancelled_cue`, so it is unit-testable without a pyglet window), invoked
in three places: the `on_event` SSE handler, the `update()` frame loop (for
tick-driven CRASHED / hold-expiry), and `on_disconnect()`. This guarantees
CRASHED — a dark state reachable via heartbeat-timeout or an SSE drop, not just
an explicit event — actually dims.

Implementation reuses `pyglet.sprite.Sprite.color` (the existing tint point in
`window.py`). The sprite's `muted` field + `muted`/`unmuted` handlers + the
renderer's IDLE pose-lock are removed as redundant; the daemon's emission of
`muted`/`unmuted` is unchanged (out of scope).

`[sprite] dim_brightness` (0.0–1.0, default 0.4) is configurable;
out-of-range raises `SpriteConfigError`. `1.0` disables dimming.

## Consequences

- Legible, consistent "off" signal; the mic-hot moment is now bright, as expected.
- Less code: one tint path, no pose-lock, no `muted` consumption in the sprite.
- Supersedes the mute-grey behaviour from the ADR 0025 era for the sprite.
- A visual E2E harness (`scripts/sprite_dim_e2e.py`) drives the real sprite
  through bright/dim phases, captures each via PrintWindow, and asserts the
  not-listening phases are measurably darker — the harness passed at a
  brightness ratio of 0.386 against a 0.6 guard, and it was the harness that
  surfaced the `current_state`-lag bug described above.
- The dead `complete_transition()` method (never called by the renderer) has been
  removed; `target_state` is the single source of the intended state.

## References

- Spec: `docs/superpowers/specs/2026-05-21-sprite-dim-not-listening-design.md`
- Plan: `docs/superpowers/plans/2026-05-21-sprite-dim-not-listening.md`
- Mute toggle removal: ADR 0025
- Animated dictation states / PROCESSING: ADR 0096
