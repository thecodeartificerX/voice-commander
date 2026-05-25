# Dictation Backend Selector (internal vs external / Wispr Flow) — Design Spec

- **Date:** 2026-05-25
- **Status:** Draft (awaiting user review)
- **Topic:** Config-selectable dictation backend — `internal` (our record→WS→paste pipeline) vs `external` (passthrough: VC mutes + animates, an external tool like Wispr Flow does the real dictation)

## Summary

Add a `[dictation] backend` config selector with two values:

- **`internal`** (default) — today's behaviour exactly: Right Ctrl / `"dictate"` records VAD-segmented PCM, streams it to the WS server, pastes the cleaned transcript (ADR 0096).
- **`external`** — a **passthrough** sub-state. Right Ctrl / `"dictate"` plays the same start chime and shows the same DICTATING animation, but VC records **nothing**, transcribes nothing, sends nothing, and pastes nothing. Its own command routing is muted while active. A separate dictation tool (Wispr Flow, bound by the user to the **same** Right Ctrl key) performs the actual dictation. Pressing Right Ctrl again exits, restoring the prior state and animation.

The user runs Wispr Flow on Right Ctrl already. VC does **not** suppress the key (pynput listener has no `suppress`), so a single Right Ctrl press reaches both apps: VC enters/exits passthrough, Wispr Flow toggles its dictation. VC never touches Wispr Flow directly.

## Motivation / goals

- Let the user keep using VC's command launcher (Scroll Lock + local Whisper) while opting out of the **unfinished** dictation pipeline in favour of Wispr Flow.
- Preserve the existing dictation UX (chime + bright cat + DICTATING badge) so muscle memory and visual feedback are unchanged — only the backend differs.
- Reuse the existing dictation sub-state machinery wholesale; no new sprite work, no new wire protocol.

## Non-goals

- **No key-forwarding / automation of Wispr Flow.** Wispr Flow shares the Right Ctrl binding independently; VC does not synthesize keys for it. (Decided 2026-05-25.)
- **No change to command transcription.** Scope is dictation-only. Scroll Lock command routing keeps using local Whisper whenever passthrough is not active. (Decided 2026-05-25.)
- **No `dictation.processing` / paste / timing in external mode.** No server round-trip happens, so there is no PROCESSING badge and no `last_timings.json` write.
- **No backend abstraction class.** Two behaviours → a config flag plus branches. (YAGNI.)
- **No spoken exit in external mode.** While passthrough is active VC transcribes nothing, so end-word `"done"` / cancel word cannot be heard. Right Ctrl is the only exit.

## Concepts & terminology

- **Backend** — the dictation engine selected by `[dictation] backend`: `internal` or `external`.
- **Passthrough** — the external-mode dictation sub-state: chime + animation + mute, no capture/transcribe/paste.
- **`_passthrough_active`** — new daemon boolean, the single source of truth for "VC mic muted, external tool driving."
- **Owned session** — a voice session VC opened itself for dictation (`_session_opened_by_dictation == True`), auto-closed on exit (existing ADR 0090 concept, reused).

## § 1 — Config

New key in the `[dictation]` table:

```toml
[dictation]
backend = "internal"   # "internal" = our record→WS→paste pipeline (default)
                       # "external" = passthrough; VC mutes + animates only,
                       #              an external tool (e.g. Wispr Flow, bound to
                       #              the same Right Ctrl) does the real dictation.
```

- Parsed into `DictationConfig.backend: Literal["internal", "external"]`, default `"internal"`.
- Unknown/empty value → log a WARNING and fall back to `"internal"` (never fatal — matches the repo's "bad config degrades, never blocks" convention).
- `config.toml.example` documents the key with the inline comment above.

The key is named `backend` (not `mode`) deliberately: `mode` already denotes **named command modes** (ADR 0100) in this codebase; overloading it would be confusing.

## § 2 — Daemon state

Add `self._passthrough_active: bool = False` alongside `self._session_opened_by_dictation` in `Daemon.__init__`. The backend value is read from config and stored as `self._dictation_backend`.

Invariants:

- `_passthrough_active` is only ever `True` when `_dictation_backend == "external"`.
- `_passthrough_active` and an active `DictationSession` are mutually exclusive (external mode never starts a `DictationSession`).
- When `_passthrough_active` is `False`, behaviour is byte-for-byte the current behaviour regardless of backend (the flag gates every new branch).

## § 3 — Entry / exit (hotkey toggle)

`on_dictation_toggle` gains an `external`-backend branch at the top; the existing `internal` body is untouched.

**External branch:**

```
if backend == "external":
    if not _passthrough_active:
        # ENTER
        if not _session_active:
            if not _open_voice_session():   # plays on_recording_start + session_started (case 1)
                return
            _session_opened_by_dictation = True
        _passthrough_active = True
        publish "dictation.start" {}        # → bright cat + DICTATING badge
    else:
        # EXIT
        _passthrough_active = False
        publish "dictation.end" {"reason": "done"}   # restore animation (ADR 0099)
        if _session_opened_by_dictation:
            _close_voice_session()          # plays on_recording_stop + session_stopped (case 1)
        # case 2: Scroll Lock session stays open; no extra chime (matches internal)
    return
```

- **Case 1 — no session open (Right Ctrl from idle):** VC opens an owned session (so `_session_active`, sprite brightness, and Scroll Lock toggling stay consistent), enters passthrough, and on exit closes the owned session — identical lifecycle to internal owned-session dictation (ADR 0090), minus the WS/paste.
- **Case 2 — Scroll Lock session already open:** VC enters passthrough as a sub-state (session stays open), suppressing command routing while active; on exit the session keeps listening for commands.

**Chime parity with internal mode.** The audible feedback comes entirely from the session open/close path, exactly as internal dictation does — *no separate dictation-enter/exit chime is added*. Verified against internal mode: case 1 chimes via `_open_voice_session`→`on_recording_start` (enter) and `_close_voice_session`→`on_recording_stop` (exit); case 2 (entering/leaving dictation inside an already-open Scroll Lock session) plays **no** extra chime in internal mode, so external matches by playing none. The DICTATING badge is driven purely by the `dictation.start`/`dictation.end` events in both cases.

## § 4 — Spoken `"dictate"` entry

The `__dictation.start` synthetic intercept in `_process_utterance` (today calls `DictationSession.start()`) gains the same backend branch: in `external` mode it enters passthrough (set flag + enter feedback + `dictation.start`) instead of starting a `DictationSession`.

This path is reachable only when a Scroll Lock session is open (the command pipeline must transcribe to recognise `"dictate"`). Once passthrough is active, utterances are dropped (§ 5), so re-entry/exit by voice is impossible — Right Ctrl exits.

## § 5 — Mute (the core of "won't record and send")

At the very top of `_process_utterance`, before the debug WAV write and before `transcribe()`:

```
if self._passthrough_active:
    return
```

Effect while passthrough is active:

- No debug WAV written, no `transcribe()` call (no Whisper CPU), no `transcribing` event, no routing, no command fires on the user's dictated speech.
- Wispr Flow captures the same mic independently (Windows shared-mode WASAPI) and does the real dictation.

On exit, the flag clears and `_process_utterance` resumes normal command processing on the next utterance.

## § 6 — Sprite / feedback

No sprite code changes. External mode publishes the existing `dictation.start` and `dictation.end` events, which already drive the DICTATING badge + bright cat (enter) and pose restore (exit) per ADRs 0086/0097/0099. External mode deliberately **never** publishes `dictation.processing`, so no PROCESSING badge appears (there is no decode wait). No `dictation.timings`, no `dictation.result`.

## § 7 — Error / edge handling

- **`_open_voice_session()` fails** (case 1 enter): error already surfaced by the helper; return without setting `_passthrough_active` (no half-entered state).
- **Scroll Lock pressed while passthrough active:** `on_scroll_lock` → `_close_voice_session()`. It must also clear `_passthrough_active` (add to the reset block next to `_session_opened_by_dictation = False`) so a torn-down session never leaves a dangling mute. Publishes `session_stopped` → sprite dims.
- **Backend switched in config while the daemon runs:** config is read at start; a change needs a restart (documented). No hot-reload in this iteration.
- **Internal mode regression:** every new branch is gated on `backend == "external"` or `_passthrough_active`, both inert under the default, so internal behaviour is unchanged.

## § 8 — Testing

**Unit (`tests/unit/`):**
- Config: `backend` parses to `internal`/`external`; default `internal`; unknown value → WARNING + `internal`.
- `on_dictation_toggle` external: case 1 (no session) enter opens owned session + sets flag + publishes `dictation.start`; exit clears flag + closes owned session + `dictation.end`. Case 2 (session open) enter sets flag without closing; exit clears flag, session stays open.
- `__dictation.start` intercept in external mode → passthrough, not `DictationSession.start()`.
- `_process_utterance` returns early (no transcribe, no route) when `_passthrough_active`; `transcribe` mock asserted not called.
- Regression: internal mode path unchanged (existing dictation tests still green); `_passthrough_active` never set in internal mode.
- `_close_voice_session` clears `_passthrough_active`.

**Visual E2E (`scripts/`, MANDATORY per `docs/agents/visual-e2e-testing.md`):**
- Subprocess sprite + SSE; configure `backend = "external"`.
- Simulate Right Ctrl (or publish the enter path): assert DICTATING badge present + cat bright via PrintWindow capture; assert **no** PROCESSING badge.
- Simulate second Right Ctrl: assert badge cleared + pose restored (idle dim if owned session, bright listening if a Scroll Lock session remained).
- Capture screenshots + log as evidence; assert on the evidence (copy `scripts/picker_visual_e2e.py` / `scripts/sprite_dim_e2e.py` patterns).

## § 9 — Documentation (ship in the same change)

- **ADR 0103** — dictation backend selector (decision, the external-shares-Right-Ctrl rationale, mute mechanism).
- **`docs/agents/technical-decisions.md`** — summary row → ADR 0103.
- **CLAUDE.md** — extend the dictation paragraph in *Current state* with the backend selector.
- **`config.toml.example`** — `backend` key + inline comment.
- **`docs/dictation-streaming.md`** (or `docs/transcription-pipeline.md`) — external/passthrough behaviour + the Wispr-Flow-shares-Right-Ctrl setup note.

## § 10 — Files touched (estimate)

| File | Change |
|---|---|
| `src/voice_commander/config.py` | `DictationConfig.backend` field + parse/validate |
| `src/voice_commander/daemon.py` | `_passthrough_active` + `_dictation_backend` state; `on_dictation_toggle` branch; `__dictation.start` branch; `_process_utterance` early-return; `_close_voice_session` reset |
| `config.toml.example` | `backend` key + comment |
| `tests/unit/test_*` | new unit coverage (config, toggle, intercept, mute, regression) |
| `scripts/dictation_external_e2e.py` | new visual E2E harness |
| `docs/decisions/0103-*.md`, `docs/agents/technical-decisions.md`, `CLAUDE.md`, `docs/dictation-streaming.md` | docs |

## Open questions

- Config key is `backend` (chosen, to avoid clashing with named-command "modes"). Flag if you'd rather it be `[dictation] mode`. — only remaining open item.
