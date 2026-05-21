# Spec — Sprite dim-when-not-listening simplification

**Date:** 2026-05-21
**Status:** Approved (brainstorming)
**Topic:** Collapse the sprite's "off" visual to a single dimmed treatment driven by a binary listening / not-listening rule.

## Problem

The `voice_sprite` companion tracks 10+ logical states (`IDLE`, `LISTENING`, `HEARING_SPEECH`, `THINKING`, `LLM_THINKING`, `SUCCESS`, `MISS`, `TOOL_ERROR`, `WARMUP`, `CRASHED`, `PROCESSING`) plus mute/dictation overlays. The visual story across these is inconsistent and over-elaborate: the only runtime tint today is a grey `(128,128,128)` applied when `muted` **or** `dictating` is true, and dictation also force-locks the cat to the `IDLE` (sleeping) pose.

The mid-session mute *toggle* was removed (ADR 0025), but the daemon still emits `muted` / `unmuted` as session-boundary cues — `muted` immediately before `session_stopped`, `unmuted` immediately after `session_started` (`daemon.py`; daemon tests assert this ordering). The sprite consumes `muted` to drive its grey tint, but that is fully redundant with the `IDLE` state `session_stopped` already produces. The net effect is that the cat greys out and "sleeps" precisely when the mic is hottest (dictation capture), which is backwards.

We want one simple, legible rule: **if the cat is listening, it is bright; if it is not listening, it is dimmed.** Nothing more.

## Goal

A single binary visual treatment layered on top of the existing per-state animations:

- **Listening → bright** (full-colour, `(255,255,255)` tint), playing the state's own animation.
- **Not listening → dimmed** (brightness multiplied down to ~40%, full opacity), still playing the state's own animation row.

In-session animation variety is **kept** — `LISTENING`, `HEARING_SPEECH`, `THINKING`, `LLM_THINKING`, `SUCCESS`, `MISS`, `TOOL_ERROR` continue to show their distinct poses. Only the *not-listening* condition collapses to one uniform dim.

## Non-goals

- No new sprite-sheet rows, greyscale assets, or second PNG. Dim is a runtime tint only.
- No GL shaders / fragment darkening.
- No change to the badge system (DICTATING / PROCESSING / CANCELLED) — badges keep drawing at full opacity on top of the cat regardless of dim.
- No change to which animation row each state uses (that mapping stays as-is in `charsheet.toml`).
- No change to the daemon, SSE event contract, or any state-machine transition table beyond removing the dead `muted` handling.

## Decisions (locked during brainstorming)

1. **Scope.** Keep in-session poses bright and rich; only the not-listening condition collapses to one dimmed cat.
2. **Dictation boundary.** Brightness tracks mic-hot. Dictation **capture** (mic recording) is **bright**; dictation **decode** (Whisper/LLM wait, `processing=True`, mic off) is **dimmed**. Amber DICTATING / blue PROCESSING badges are unchanged.
3. **Off look.** Darken/dim — multiply brightness to ~40%, keep full opacity (solid, readable on any wallpaper). Not opacity-fade. Dim level configurable.

## State → visual mapping

| State | Listening? | Visual |
|---|---|---|
| `LISTENING`, `HEARING_SPEECH`, `THINKING`, `LLM_THINKING`, `SUCCESS`, `MISS`, `TOOL_ERROR` | yes | bright, own animation row |
| dictation **capture** (`dictating=True`, `processing=False`) | yes | **bright** + amber DICTATING badge, normal listening/hearing animation |
| `IDLE`, `WARMUP`, `CRASHED` | no | dimmed, own animation row (sleeping / idle loop keeps playing, just dark) |
| dictation **decode** (`processing=True`) | no | dimmed + blue PROCESSING badge |

Note the deliberate asymmetry: command-path `THINKING` (fast transcribe/match) stays **bright** as part of the lively in-session flow, while dictation `PROCESSING` (longer heads-down Whisper+LLM wait) is **dimmed**. These are distinct states with distinct, explicitly-chosen treatments — not a contradiction.

## The dim predicate (single source of truth)

```python
def is_dim(state: SpriteState, processing: bool) -> bool:
    """True when the cat is NOT listening and should render dimmed."""
    return (
        state in (
            SpriteState.IDLE,
            SpriteState.WARMUP,
            SpriteState.CRASHED,
            SpriteState.PROCESSING,
        )
        or processing
    )
```

`dictating` is intentionally absent: during capture the underlying state is a session state (`LISTENING` / `HEARING_SPEECH`) with `processing=False`, so the predicate yields `False` (bright). When decode begins, `processing` flips `True` and the predicate yields `True` (dim). `PROCESSING` is also listed in the state tuple defensively — at runtime it always coincides with `processing=True`, but listing it keeps `is_dim` correct for any `(state, processing)` pair (and makes the unit truth-table clean).

## Architecture / component changes

All changes live inside the `voice_sprite` process. The cat is a click-through pyglet overlay; dimming reuses the existing `pyglet.sprite.Sprite.color` multiply.

- **`src/voice_sprite/window.py`**
  - Rename `_mute_color` → a dim colour derived from config; rename `set_muted(...)` → `set_dim(bool)`.
  - At the single tint point (currently `self._sprite.color = self._mute_color if self._muted else (255,255,255)`), select the dim colour when dim, else `(255,255,255)`.
  - Dim colour = `(b, b, b)` where `b = round(255 * dim_brightness)` (default `0.4` → `(102,102,102)`).
- **`src/voice_sprite/sprite_renderer.py`**
  - Delete `_muted` field and `set_muted(...)` IDLE-pose-lock. Not-listening states already own their animation rows (`IDLE` sleeps, etc.), so no pose override is needed. `frame_region` selects the current state's row unconditionally.
- **`src/voice_sprite/__main__.py`**
  - Replace the `grey = sm.muted or sm.dictating; window.set_muted(grey); renderer.set_muted(grey)` block with `dim = is_dim(sm.state, sm.processing); window.set_dim(dim)`.
  - Keep `window.set_dictating(...)`, `_apply_processing_state(...)`, `_apply_cancelled_cue(...)` as-is (badge logic).
- **`src/voice_sprite/state_machine.py`**
  - Remove the sprite's now-redundant `muted` field and `"muted"` / `"unmuted"` handlers. Dimming is driven by `state` + `processing`, so the flag is unnecessary. **The daemon keeps emitting `muted` / `unmuted`** around session boundaries (out of scope to change); the sprite ignores them as unknown no-op events (they are absent from `EVENT_STATE_MAP`).
  - Add a module-level pure function `is_dim(state, processing) -> bool` so it is exhaustively unit-testable without constructing a time-driven `StateMachine`, and so `__main__` has one import.

## Data flow

Unchanged transport. Per SSE event, `__main__` updates the `StateMachine`, then each frame: compute `dim = is_dim(state, processing)` → `window.set_dim(dim)` → `on_draw` applies the tint colour → sprite drawn. Badges drawn on top at full opacity.

## Configuration

Add to the existing `[sprite]` section:

```toml
[sprite]
follow_cursor = true
follow_poll_hz = 30
dim_brightness = 0.4   # 0.0–1.0 brightness multiplier for not-listening (dimmed) states
```

`dim_brightness` is validated to `[0.0, 1.0]` — out-of-range raises `SpriteConfigError`, matching the validate-and-raise convention every other `[sprite]` key uses. `1.0` disables dimming (cat always bright); `0.0` is fully black. Absent → default `0.4`.

## Error handling / edge cases

- **Out-of-range `dim_brightness`** → raise `SpriteConfigError` (validate-and-raise, matching `config.py`); absent → default `0.4`.
- **Dictation-owned session auto-close** → on `dictation.end`, `processing` and `dictating` clear and state returns to `IDLE`; predicate yields dim. Correct ("done listening").
- **`CRASHED`** → dimmed sleeping cat reinforces "daemon gone" (no separate crash visual today; out of scope to add one).
- **Badges over a dim cat** → badges keep full opacity; they remain legible against the dimmed sprite.

## Testing

Per the project's mandatory visual-E2E protocol (`docs/agents/visual-e2e-testing.md`), a user-visible feature needs both unit coverage and an evidence-capturing harness.

- **Unit** (`tests/unit/`): truth table for `is_dim(state, processing)` over every `SpriteState` × `{processing=False, True}`. Assert: bright set = `{LISTENING, HEARING_SPEECH, THINKING, LLM_THINKING, SUCCESS, MISS, TOOL_ERROR}` with `processing=False`; dim set = `{IDLE, WARMUP, CRASHED}` ∪ anything with `processing=True`. Plus tests that `dim_brightness` maps to the expected `(b,b,b)` colour and that out-of-range raises `SpriteConfigError`.
- **Existing-test updates for the `muted` removal:** `tests/unit/test_state_machine.py` (drop the `muted`/`unmuted` overlay tests + "non-muted" reachability wording), `tests/unit/test_window.py` (`set_muted`/`_mute_color`/`_muted` → `set_dim`/`_dim_color`/`_dim`), `tests/unit/test_sprite_main.py` (`sm.muted` / `window.set_muted.assert_not_called()` → dim equivalents). The daemon-side `muted`/`unmuted` tests (`test_daemon_session_helpers.py`, `test_event_bus.py`) are **untouched** — the daemon still emits those events.
- **Visual E2E** (`scripts/sprite_dim_e2e.py`, pattern copied from `scripts/picker_visual_e2e.py`): launch the sprite subprocess, drive SSE transitions `idle → session_started → (dictation capture) → processing → dictation.end → idle`, `PrintWindow`-capture the sprite each phase, and assert the mean brightness of the sprite region: bright phases ≈ full brightness, dim phases ≈ `dim_brightness × full`. Capture screenshots as evidence.

## Documentation impact (same change as code)

- New ADR in `docs/decisions/` recording the dim-when-not-listening decision (supersedes the mute-grey behaviour; references ADR 0025 mute removal).
- New row in `docs/agents/technical-decisions.md`.
- Update the **Current state** paragraph in `CLAUDE.md` (sprite visual behaviour).
- Update `config.toml.example` (the `[sprite] dim_brightness` key, with inline comment).
- Fix any docstrings touched in `window.py` / `sprite_renderer.py` / `state_machine.py`.

## Files touched (summary)

- `src/voice_sprite/window.py` — dim colour + `set_dim`.
- `src/voice_sprite/sprite_renderer.py` — delete `set_muted` pose-lock.
- `src/voice_sprite/__main__.py` — dim predicate wiring.
- `src/voice_sprite/state_machine.py` — `is_dim` helper; remove dead `muted`.
- `config.toml.example` — `dim_brightness`.
- `tests/` — unit tests.
- `scripts/sprite_dim_e2e.py` — visual E2E harness.
- Docs: new ADR, `technical-decisions.md`, `CLAUDE.md`.
