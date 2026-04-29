# Speak Dictation Toggle — Design Spec

**Date:** 2026-04-29
**Status:** Approved (brainstorm complete; awaiting implementation plan)
**Supersedes:** ADR 0025 (mute hotkey for external dictation coexistence) — see "Architecture changes" below

## Goal

Hands-free toggle for Windows external voice dictation. The user says "speak" and the daemon synthesizes a Right Ctrl keystroke (the dictation app's own hotkey), entering a "speak-mode" where the audio stream stays open but transcripts are routed to a fuzzy-match wake-word check instead of the LLM. The user says "speak" again and the daemon synthesizes Right Ctrl once more, returning to normal LLM-routed operation. A manual Right Ctrl press has identical state effects (the synthesized and manual presses are indistinguishable to the rest of the system).

## Non-goals

- Wake-word models (porcupine, picovoice, etc.). The existing whisper pipeline handles all transcripts in speak-mode.
- Dictation app process detection / auto-pairing. Toggling stays manual (voice or hotkey, both initiated by the user).
- Multi-trigger word configurability. Only the literal word "speak" toggles. Aliases can be added in a follow-up if needed.
- A generic cancel/abort system command. Future hardcoded system primitives reuse the `system = true` mechanism added here, but those tools are not part of this work.
- Secondary or lighter audio pipelines. The single primary pipeline does all the listening.

## User-facing behavior

1. Session inactive → press Scroll Lock → session active, normal mode.
2. In normal mode, the user says "speak" → LLM picks the `speak` tool → daemon synthesizes Right Ctrl → Windows dictation app receives the keystroke and starts listening → daemon flips into speak-mode.
3. In speak-mode, every transcript is checked against the literal word "speak" with `rapidfuzz.ratio >= 95` AND `word_count == 1`. Anything else is silently dropped (no plan_outcome, no dispatch, no LLM call).
4. The user says "speak" again → fuzzy match → daemon synthesizes Right Ctrl → dictation app stops → daemon flips back to normal mode.
5. A manual Right Ctrl press at any active-session time has the same state effect as a synthesized one (same listener callback handles both).
6. Pressing Scroll Lock while in speak-mode synthesizes Right Ctrl on the way out (cleanly turning off the dictation app), then ends the session.
7. The sprite shows the existing `muted` visual whenever `_speak_mode = True`. The HUD summary rule for `muted` reads "Dictation mode — say 'speak' to exit."

## Architecture changes (vs. current code)

### Replaces ADR 0025's stream-close-on-mute

ADR 0025 closed the audio stream on mute to prevent crosstalk-induced false dispatch from the dictation app's audio. The new design solves the same false-dispatch concern by gating the LLM route instead of the audio stream:

- The audio stream stays open through the entire active-session lifetime.
- `_process_utterance` checks `_speak_mode` before reaching the LLM. In speak-mode, it does the fuzzy-match check and never calls `LLMRouter.route()` or `Dispatcher.run_plan()`.
- Crosstalk audio is still transcribed by whisper (negligible GPU cost on `small.en` CUDA), but it cannot reach the dispatcher because the route is gated.

### Daemon state model

| Field | Type | Owner | Notes |
|---|---|---|---|
| `_session_active` | `bool` | `on_scroll_lock` | Unchanged from current code. |
| `_speak_mode` | `bool` | `on_speak_toggle` | Replaces `_muted`. Flipped by Right Ctrl listener (manual or synthesized press). |

The `_muted` field, the `_drain_utt_q()` call on mute, and the in-pipeline mute-guard from ADR 0025 §3 are all removed. They served the closed-stream invariant that no longer exists.

### Hotkey controller binding

- `on_mute_toggle` → renamed to `on_speak_toggle`.
- Body simplified to a single line that flips the flag and publishes the event:
  ```python
  def on_speak_toggle(self) -> None:
      if not self._session_active:
          return
      self._speak_mode = not self._speak_mode
      self._publish("muted" if self._speak_mode else "unmuted")
  ```
- No recorder calls. The stream is unaffected.

### `_process_utterance` flow

Insert one new branch immediately after the `no_speech_prob` gate and before the confidence gate:

```python
if self._speak_mode:
    text_norm = result.text.strip().lower().rstrip(".,!?")
    if len(text_norm.split()) == 1 and rapidfuzz.fuzz.ratio(text_norm, "speak") >= self._speak_fuzzy_threshold:
        self._registry.get("speak").func()  # synth Right Ctrl → listener flips _speak_mode
    return  # speak-mode short-circuits the rest of the pipeline
```

The fuzzy threshold is read from `cfg.speak.fuzzy_threshold` (default 95). The `word_count == 1` condition prevents the literal word "speak" inside multi-word dictation (e.g. "speak louder") from accidentally exiting the mode.

The confidence gate runs *after* this branch when `_speak_mode = False`, unchanged from today.

### `speak` tool

New module `src/voice_commander/tools/_system.py`:

```python
from pynput import keyboard

from voice_commander.registry import tool

_controller = keyboard.Controller()

@tool
def speak() -> None:
    """Toggle Windows voice dictation on/off. Synthesizes a Right Ctrl press."""
    _controller.press(keyboard.Key.ctrl_r)
    _controller.release(keyboard.Key.ctrl_r)
```

Sidecar `src/voice_commander/tools/_system.toml`:

```toml
[meta]
system = true
enabled = true
description = "Toggle Windows voice dictation on/off."

[args]
# No args.

[phrases]
speak = ["speak", "start dictation", "voice typing", "begin dictating"]
```

The leading underscore in the module name keeps it sorted apart from user-facing tool modules in directory listings, and makes its "infrastructure" role clear. The tool registry's auto-discovery currently does not exclude underscore-prefixed modules; it discovers anything matching `*.py` with a sibling `.toml`. The leading underscore is purely a naming convention for human readers.

### `system = true` flag

A new boolean field in `[meta]` of every sidecar TOML. Default `false`. Two effects:

1. `web/templates/page_primitives.html` and the related route filter out tools where `meta.system == true`.
2. The Builder UI palette (`/page/builder`) and the `/api/tools` REST endpoint that powers the React Flow palette omit system tools from the catalog.

The LLM router treats system tools identically to user tools — they appear in the tool list passed to LM Studio. The Dispatcher, Tracer, and observability pipeline likewise treat them identically. The flag is purely a UI-visibility filter.

### Right Ctrl synthesis library choice

`pynput.keyboard.Controller` is used (not `pyautogui`) because:

- `HotkeyController` already imports `pynput.keyboard` and uses `Key.ctrl_r`. Reusing the same library keeps the synth and listener contracts in lockstep — if the listener key constant ever changes, the synth follows automatically.
- pyautogui's Right Ctrl support requires the string `"ctrlright"`, which is a magic string maintained inside pyautogui. Less robust than a typed enum.
- pynput's synthesized presses go through the same Win32 `SendInput` path internally, and they are observed by pynput's own Listener (verified in pynput docs and by the listener's hook-based design — it observes OS-level keyboard events regardless of source).

### Config schema

```toml
[hotkey]
key = "scroll_lock"
mute_key = "ctrl_r"     # default flips from "" to "ctrl_r" (recommended pairing with Windows dictation)

[speak]
fuzzy_threshold = 95    # rapidfuzz.fuzz.ratio threshold; range 0-100
```

The `[hotkey] mute_key` config key keeps its name to avoid breaking existing user configs. Internally the listener callback is `on_speak_toggle`, but the config key is unchanged. The default value flips from empty to `"ctrl_r"` so the speak-mode hotkey is on by default; users who do not want a hardware hotkey can set it back to `""` and only the voice trigger will work.

A new `Config.speak: SpeakConfig` dataclass is added with one field, `fuzzy_threshold: int = 95`. Validation rejects values outside `[0, 100]`.

### EventBus events

Reuses the existing `muted` and `unmuted` events. No new event types. The sprite's existing muted state visual is reused. The HUD summary rule for the `muted` event is updated (or a new rule is added with a `speak` discriminator in the event payload — implementation detail to be decided in the plan, but the user-visible string is "Dictation mode — say 'speak' to exit").

### Scroll Lock close from speak-mode

In `on_scroll_lock`, the existing close branch is extended:

```python
if self._session_active:
    if self._speak_mode:
        # Synth Right Ctrl so the dictation app turns off cleanly.
        self._registry.get("speak").func()
        # The listener will flip _speak_mode = False before we reach the close.
    self._recorder.close_session()
    self._session_active = False
    self._speak_mode = False
    self._feedback.on_recording_stop()
    self._publish("session_stopped")
```

The synthesized Right Ctrl is observed by the listener, which fires `on_speak_toggle` → `_speak_mode = False`. The subsequent `close_session()` runs on a now-clean state.

## Validation gates

Per `docs/testing-strategy.md`:

### Unit tests

- `tests/unit/test_tools_system.py` — `speak()` calls `Controller.press(Key.ctrl_r)` then `Controller.release(Key.ctrl_r)`. Mock the controller.
- `tests/unit/test_streaming_daemon.py` — extend with the 7 state-transition rows in the table below. Mock recorder, registry, listener, controller. Drive via direct method calls and queue-injected utterances.
- `tests/unit/test_streaming_daemon.py::test_pipeline_speak_branch` — in `_speak_mode = True`, a fuzzy-match-positive transcript fires the `speak` tool; a fuzzy-match-negative one is dropped without LLM call. Confidence gate is bypassed.
- `tests/unit/test_config.py` — extend with `[speak]` section validation: out-of-range threshold rejected; missing section uses default 95.
- `tests/unit/test_validator.py` — extend with the `system = true` flag drift check: a tool sidecar TOML with `system = true` does not require user-editable fields like phrases (already optional).
- `tests/unit/test_web_routes.py` — `/page/primitives` HTML response does not contain the `speak` tool name. `/api/tools` JSON response omits it. `/page/builder` palette payload omits it.

### Integration tests

- `tests/integration/test_speak_end_to_end.py` — feed canned WAV "speak", assert `_speak_mode` flips True and no LLM call was made. Feed a second canned WAV "speak", assert `_speak_mode` flips False and no LLM call was made. The `pynput.Controller` is mocked at the module level so tests do not actually toggle the developer's dictation app.
- `tests/integration/test_session_lifecycle.py` — extend with: open session, enter speak-mode, press Scroll Lock; assert one synth Right Ctrl was fired and the session ended cleanly with `_speak_mode = False`.
- `tests/integration/test_hotkey_toggles_session.py` — replace ADR 0025-era assertions about `recorder.close_session()` being called on mute with the new "stream stays open" assertions. Manual Right Ctrl press while session is active flips `_speak_mode` only.

### Manual validation (HITL gate)

- Cold start daemon. Press Scroll Lock. Say "speak". Verify Windows dictation app activates. Speak some sample text into a notepad and confirm dictation types it. Say "speak" again. Verify dictation app deactivates. Say a normal command (e.g. "open spotify"). Verify it dispatches.
- From speak-mode, press Scroll Lock. Verify dictation app deactivates and session ends.
- Manually press Right Ctrl in normal mode. Verify dictation activates, sprite shows muted state, HUD shows "Dictation mode — say 'speak' to exit".
- Verify the `speak` tool does not appear in `/page/primitives` or in the Builder palette.

## State transition table (single source of truth)

| Trigger | `_session_active` pre | `_speak_mode` pre | `_session_active` post | `_speak_mode` post | Side effects |
|---|---|---|---|---|---|
| Scroll Lock | F | – | T | F | `recorder.open_session()`, `feedback.on_recording_start()`, publish `session_started` |
| Scroll Lock | T | F | F | F | `recorder.close_session()`, `feedback.on_recording_stop()`, publish `session_stopped` |
| Scroll Lock | T | T | F | F | synth Right Ctrl (→ listener flips speak_mode F), `recorder.close_session()`, `feedback.on_recording_stop()`, publish `session_stopped` |
| Right Ctrl (manual or synth) | F | – | F | F | silent no-op |
| Right Ctrl (manual or synth) | T | F | T | T | publish `muted` |
| Right Ctrl (manual or synth) | T | T | T | F | publish `unmuted` |
| LLM dispatches `speak` | T | F | (T, T) via Right Ctrl | – | tool synthesizes Right Ctrl; listener row above handles flag flip |
| Fuzzy-match "speak" in pipeline | T | T | (T, F) via Right Ctrl | – | same path as above |
| Pipeline utterance, fuzzy-match miss | T | T | T | T | silent drop, no plan_outcome |
| Pipeline utterance, normal | T | F | T | F | unchanged from current code (LLM route → dispatch) |

## Files touched

### New

- `src/voice_commander/tools/_system.py`
- `src/voice_commander/tools/_system.toml`
- `tests/unit/test_tools_system.py`
- `tests/integration/test_speak_end_to_end.py`
- `docs/decisions/0072-speak-dictation-toggle.md` (new ADR; supersedes 0025)

### Modified

- `src/voice_commander/daemon.py` — rename `on_mute_toggle` → `on_speak_toggle`, simplify body, remove `_muted` and `_drain_utt_q`-on-mute, add speak-mode branch in `_process_utterance`, extend `on_scroll_lock` close branch with speak-mode synth.
- `src/voice_commander/config.py` — add `SpeakConfig` dataclass, default `mute_key = "ctrl_r"`.
- `src/voice_commander/tool_metadata.py` — add `system: bool = False` field to `ToolMeta`.
- `src/voice_commander/web/app.py` — filter `system = true` tools from `/page/primitives` and `/api/tools`.
- `src/voice_commander/web/builder.py` — filter `system = true` tools from the palette payload.
- `src/voice_sprite/summary_rules.py` — update `muted` event summary string.
- `tests/unit/test_streaming_daemon.py`, `tests/unit/test_config.py`, `tests/unit/test_validator.py`, `tests/unit/test_web_routes.py`, `tests/integration/test_session_lifecycle.py`, `tests/integration/test_hotkey_toggles_session.py` — per the test plan above.
- `docs/agents/technical-decisions.md` — add row for ADR 0072.
- `docs/decisions/0025-mute-hotkey-for-external-dictation.md` — change status to "Superseded by ADR 0072".

### Removed

- ADR 0025's `_drain_utt_q()` call from `on_mute_toggle` and the in-pipeline mute-guard. Both become dead code under the new flag-only model.

## Open implementation questions (deferred to plan)

- Exact wording for the `muted` HUD summary rule update vs. introducing a discriminator field on the event payload — both work; pick whichever requires fewer template/CSS changes.
- Whether the `system = true` flag is opt-in via `[meta]` block or via a top-level field. Aligning with existing TOML structure is preferred — pick whatever the current `ToolMeta` schema supports cleanest.
- Whether the controller singleton lives at module scope in `_system.py` or is constructed per-call. Module-scope is cheaper but adds a process-wide side effect at import time; per-call is one allocation per "speak" press, negligible.
