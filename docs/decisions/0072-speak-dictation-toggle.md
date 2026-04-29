# ADR 0072: Speak Dictation Toggle

**Status:** Accepted
**Date:** 2026-04-29
**Supersedes:** [ADR 0025](0025-mute-hotkey-for-external-dictation.md) — mute hotkey for external dictation coexistence

## Context

ADR 0025 solved external dictation coexistence by closing the audio stream on Right Ctrl (mute). The stream-close approach introduced complexity: `_drain_utt_q()`, a pipeline mute-guard, and recorder open/close churn on every dictation toggle. It also meant any utterance captured during the stream-reopen lag was silently lost.

The new design solves the same false-dispatch concern at the routing layer instead of the transport layer: the audio stream stays open through the entire active-session lifetime, and `_process_utterance` checks `_speak_mode` before calling `LLMRouter.route()`. Crosstalk audio is transcribed (negligible GPU cost) but cannot reach the dispatcher.

A new `speak` tool replaces the old mute primitive. The user says "speak" → LLM routes to `speak` → daemon synthesizes Right Ctrl → dictation app activates → `on_speak_toggle` fires (listener sees the synthesized keypress) → `_speak_mode = True`. All subsequent transcripts are checked against the fuzzy wake-word "speak" and silently dropped unless they match. A second "speak" exits speak-mode identically. A manual Right Ctrl press has the same effect as a synthesized one.

## Decision

1. **Remove** `_muted`, `_drain_utt_q()` call on mute, and the in-pipeline mute-guard from ADR 0025. They served the closed-stream invariant that no longer exists.

2. **Rename** `on_mute_toggle` → `on_speak_toggle`. Simplified to a single-line flag flip + event publish. No recorder calls.

3. **Add** `_speak_mode: bool = False` to `StreamingDaemon`. Owns the dictation-coexistence gate. Flipped by `on_speak_toggle`.

4. **`_process_utterance` speak-mode gate** (inserted after `no_speech_prob` gate, before confidence gate):
   - Normalise transcript: strip, lowercase, strip trailing punctuation.
   - If `word_count == 1` AND `rapidfuzz.fuzz.ratio(text_norm, "speak") >= fuzzy_threshold` → call `speak` tool → listener flips `_speak_mode`.
   - Otherwise → silent drop (no LLM call, no plan_outcome event, no miss chime).
   - Either way → `return` (short-circuit rest of pipeline).

5. **New `speak` tool** in `src/voice_commander/tools/_system.py`:
   ```python
   _controller.press(keyboard.Key.ctrl_r)
   _controller.release(keyboard.Key.ctrl_r)
   ```
   Uses `pynput.keyboard.Controller` (same library as the listener) for lockstep key enum consistency.

6. **`system = true` TOML flag** on the `speak` tool. UI-visibility only: hides the tool from `/page/primitives`, `/api/tools`, and the Builder palette. LLM router and dispatcher treat system tools identically to user tools.

7. **Scroll Lock from speak-mode**: synthesize Right Ctrl first (turns dictation app off cleanly), then close session and set `_speak_mode = False`.

8. **Config schema**:
   ```toml
   [hotkey]
   mute_key = "ctrl_r"   # default flips from "" to "ctrl_r" — on by default

   [speak]
   fuzzy_threshold = 95  # range [0, 100]; validated at startup
   ```
   The `mute_key` config key retains its name to avoid breaking existing user configs. Internally the callback is `on_speak_toggle`.

9. **EventBus events**: reuses existing `muted`/`unmuted` events. No new event types.

10. **HUD summary rule**: `speak` key added to `summary_rules.RULES` with value `"Dictation mode — say 'speak' to exit"`.

## Key rationale

- **Why stream stays open**: closing/reopening `sd.InputStream` on every dictation toggle induces PortAudio state-machine overhead and a reopen latency gap. Gating at the route layer is zero-cost when not in speak-mode.
- **Why `pynput.Controller` for synthesis**: same library as the listener — if the `Key.ctrl_r` constant ever changes, synth and listener track together automatically. `pyautogui` uses a magic string `"ctrlright"`, less robust.
- **Why `system = true` flag (not `internal = true`)**: `internal` hides from the LLM's tool list. `speak` must be LLM-visible so the router can route "speak" utterances to it. `system` is a separate, orthogonal UI-visibility flag. The two flags are independent.
- **Why `word_count == 1` guard**: prevents "speak louder" or other dictated text that happens to contain the word "speak" from accidentally exiting speak-mode.
- **Why `fuzzy_threshold = 95`**: requires a near-exact match (1–2 character tolerance), reducing false positives from dictated homophones like "speke" while staying permissive enough for natural speech variation.

## State transition table

| Trigger | `_session_active` pre | `_speak_mode` pre | `_session_active` post | `_speak_mode` post | Side effects |
|---|---|---|---|---|---|
| Scroll Lock | F | – | T | F | `recorder.open_session()`, `feedback.on_recording_start()`, publish `session_started` |
| Scroll Lock | T | F | F | F | `recorder.close_session()`, `feedback.on_recording_stop()`, publish `session_stopped` |
| Scroll Lock | T | T | F | F | synth Right Ctrl (→ listener flips `_speak_mode` F), `recorder.close_session()`, `feedback.on_recording_stop()`, publish `session_stopped` |
| Right Ctrl (manual or synth) | F | – | F | F | silent no-op |
| Right Ctrl (manual or synth) | T | F | T | T | publish `muted` |
| Right Ctrl (manual or synth) | T | T | T | F | publish `unmuted` |
| Utterance (fuzzy "speak") | T | T | T | F | synth Right Ctrl → `on_speak_toggle` flips to F, publish `unmuted` |
| Utterance (non-"speak") | T | T | T | T | silent drop — no LLM call, no plan_outcome |
