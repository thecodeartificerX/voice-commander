# ADR 0102 — Dictation Backend Selector (internal vs external / Wispr Flow)

**Status:** Accepted
**Date:** 2026-05-26
**Extends:** [ADR 0086](0086-dictation-mode.md) (dictation lifecycle), [ADR 0090](0090-right-ctrl-opens-session.md) (owned session), [ADR 0096](0096-server-side-dictation.md) (internal transport), [ADR 0099](0099-dictation-end-restores-pose.md) (pose restore)

## Context

The internal dictation pipeline (record → WS → server Whisper+LLM → paste, ADR 0096) is still being hardened. The user already runs Wispr Flow bound to Right Ctrl for real dictation. We want to keep VC's command launcher (Scroll Lock + local Whisper) while opting out of the unfinished internal dictation pipeline — without losing the dictation UX (chime + bright cat + DICTATING badge) that muscle memory depends on.

Wispr Flow shares the Right Ctrl binding independently. VC's pynput listener uses no `suppress`, so a single Right Ctrl press reaches both apps: VC toggles its own state and Wispr Flow toggles its dictation. VC never synthesizes keys for, or otherwise drives, Wispr Flow.

## Decision

Add a `[dictation] backend` config selector with two values:

- **`internal`** (default) — today's behaviour, unchanged.
- **`external`** — a *passthrough* sub-state. Right Ctrl / spoken "dictate" plays the same start chime and shows the same DICTATING animation, but VC records nothing, transcribes nothing, sends nothing, pastes nothing, and mutes its own command routing while active. A second Right Ctrl press exits, restoring the prior state. An external tool (Wispr Flow) does the real dictation.

Implementation is a config flag plus branches — **no backend abstraction class** (YAGNI, two behaviours). A single daemon boolean `_passthrough_active` is the source of truth for "VC mic muted, external tool driving":

- `on_dictation_toggle` gains an external branch: enter opens an owned voice session if none is open (case 1) or enters as a sub-state of an open Scroll Lock session (case 2); exit publishes `dictation.end` then closes the owned session if VC opened it.
- The `__dictation.start` spoken intercept enters passthrough instead of starting a `DictationSession`.
- `_process_utterance` returns early when `_passthrough_active` — before the debug-WAV write and `transcribe()` — so there is no Whisper CPU, no transcript event, no routing, no fire.
- `_close_voice_session` clears `_passthrough_active` so a torn-down session (incl. Scroll Lock during passthrough) never leaves a dangling mute.

The backend is named `backend` (not `mode`) to avoid clashing with named command modes (ADR 0100). It is parsed into `DictationConfig.backend: str` (plain `str`, not `Literal`, because the generic config parser raises on type mismatch and we want degrade-not-block); an unknown/empty value logs a WARNING and falls back to `internal`. The value is cached on the daemon at startup; a change requires a restart (no hot-reload).

External mode reuses the existing dictation sprite machinery: it publishes the same `dictation.start` (`{}`) and `dictation.end` (`{"reason": "done"}`) events, and deliberately never publishes `dictation.processing` (no server round-trip), so no PROCESSING badge appears and no `last_timings.json` is written. No sprite, wire-protocol, or new-dependency changes.

## Consequences

### Positive

- The user keeps VC's command launcher while delegating dictation to Wispr Flow, with identical chime + visual feedback.
- Internal behaviour is byte-for-byte unchanged: every new branch is gated on `backend == "external"` or `_passthrough_active`, both inert under the default.
- No new sprite code, no new wire protocol, no new dependency.

### Negative

- No spoken exit in external mode (VC transcribes nothing) — Right Ctrl is the only exit.
- Backend change needs a daemon restart.

### Neutral

- VC and Wispr Flow share the mic via Windows shared-mode WASAPI; VC simply ignores its capture while muted.
- No key-forwarding/automation of Wispr Flow (explicit non-goal).

## Alternatives rejected

- **Backend abstraction class / strategy object** — overkill for two behaviours; a flag + branches is clearer and fully testable. (YAGNI.)
- **Suppressing Right Ctrl so only VC sees it, then VC drives Wispr Flow** — requires key synthesis/automation of a third-party app; fragile and an explicit non-goal.
- **`[dictation] mode` key** — `mode` already denotes named command modes (ADR 0100); overloading it would confuse.

## References

- Design spec: `docs/superpowers/specs/2026-05-25-dictation-backend-selector-design.md`
- Visual E2E: `scripts/dictation_external_e2e.py`
- Unit tests: `tests/unit/test_dictation_backend.py`, config tests in `tests/unit/test_config.py`
