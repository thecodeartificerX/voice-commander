# ADR 0025: Mute Hotkey for External Dictation Coexistence

**Status:** Superseded by [ADR 0086](0086-dictation-mode.md) — the mute toggle and all stream-close/reopen machinery are removed; `ctrl_r` is repurposed as `dictation_key`. Originally superseded by [ADR 0072](0072-speak-dictation-toggle.md), but that approach was itself dropped when the LLM router was removed (ADR 0082) — speak-mode depended on LLM-based fuzzy wake-word matching. The pre-0072 design (this ADR) was the live implementation again (amended below by §10) until ADR 0086 replaced it entirely.
**Date:** 2026-04-21
**Amended:** 2026-05-15 (§10 — generation-counter race fix)

## Context

Right Ctrl opens an external Windows voice-dictation app. While dictation is running, voice-commander's own mic capture picks up the same audio, causing crosstalk and false command dispatch. Users need a way to suspend voice-commander's audio stream while dictation is active, without ending the Scroll Lock session.

Currently there is no mid-session pause mechanism — the only option is to press Scroll Lock to end the session entirely and re-open it after dictation. This is disruptive: it closes the audio stream, discards pre-roll buffers, and forces the user to re-establish context with a second Scroll Lock press after dictation ends. It also does not cleanly interoperate with the external dictation app's own hotkey (Right Ctrl), which means the user is juggling two separate toggles with no shared semantics.

## Decision

1. A secondary hotkey (configurable via `config.toml` `[hotkey] mute_key`, default empty/disabled) toggles a `muted` flag on `StreamingDaemon`.
2. Two independent boolean flags: `session_active` (owned by Scroll Lock) and `muted` (owned by mute key). Not a single tri-state enum — the flags have distinct owners and distinct semantics.
3. When muted: `StreamingRecorder.close_session()` closes the `sd.InputStream`; `_drain_utt_q()` discards any pending utterances already enqueued by VAD; the pipeline worker re-checks `self._muted` before `dispatcher.dispatch()` as a stale-utterance guard for any utterance already mid-transcription when mute was pressed.
4. When unmuted: `StreamingRecorder.open_session()` reopens the stream. The Scroll Lock session remains active throughout — mute does not affect `session_active`.
5. Scroll Lock close from muted state: stream already closed, so no double-close; drain queue; clear both flags.
6. Mute key when session inactive: silent no-op — no feedback, no log-worthy event. The mute key is primarily the external dictation app's own hotkey; any chime or log spam on every dictation press would be confusing noise.
7. `HotkeyController` is refactored from a single-key + single-callback design to a multi-binding dispatch table (`dict[str, Callable]`), still backed by one `pynput.keyboard.Listener`. This eliminates the need for a second listener thread.
8. The `pynput` Listener keeps `suppress=False` (observational) so the external dictation app still receives the Right Ctrl keystroke.
9. `ctrl_r` and `ctrl_l` are added to `KEY_ALIASES` in `hotkey.py` so they can be named in `config.toml` without requiring users to use `pynput` internal key names.

10. **(Amendment 2026-05-15)** A monotonically-increasing `_audio_gen` int on `StreamingDaemon` is the third stale-utterance layer:
    - **Bumped** on the listener thread immediately *before* `recorder.close_session()` in both mute and scroll-lock-close paths, and immediately *before* `recorder.open_session()` in the unmute and scroll-lock-open paths.
    - **Snapshotted** at enqueue time inside `_on_utterance` and packed into the `_utt_q` item as a `(audio, gen)` tuple.
    - **Checked** twice inside `_process_utterance`: once before `transcribe()` (cheap escape — saves the ~200 ms-2 s GPU cost when mute fired before the pipeline popped the utterance), and once after `transcribe()` returns. Either mismatch ⇒ silent drop, no transcript event, no plan_outcome, no chime.
    - The `_pipeline_loop` accepts both `(audio, gen)` tuples (production path) and bare ndarrays (test injection — gen=None skips the generation check) for backward compatibility.

### Key rationale

- **Why `ctrl_r` as the suggested default:** Right hand reaches Right Ctrl naturally while left hand stays free; Right Ctrl is also the Windows dictation hotkey, so one keypress simultaneously toggles both apps — the least-friction possible interaction.
- **Why silent no-op when session inactive:** the mute key doubles as the external dictation app's primary hotkey. Any feedback from voice-commander on that press (chime, log line) would surface every time the user activates dictation outside a voice-commander session, which is confusing and contrary to the non-interruptive feedback policy in ADR 0013.
- **Why drain queue on mute:** between pressing mute and the PortAudio stream actually halting, VAD may have emitted utterances from pre-mute audio. Without draining, those utterances would dispatch tools from audio the user intended for dictation.
- **Why three layers of stale-utterance protection (amended 2026-05-15):** `_drain_utt_q()` catches utterances still sitting in the queue; the `_audio_gen` generation check (§10) catches the utterance the pipeline worker has already popped and is currently transcribing — the boolean `_muted` cannot, because `_muted = True` is written *after* `recorder.close_session()` returns (which itself blocks on the VAD worker join, ~tens to hundreds of ms), while `transcribe()` can finish on a warm GPU in less time. Bumping `_audio_gen` is synchronous on the listener thread and happens *before* close_session, so the post-transcribe gen check fires deterministically. The legacy `_muted` boolean check remains as a fourth defence-in-depth layer for utterances injected directly via `_process_utterance` in tests (gen=None skips the gen check).

## Consequences

### Positive

- Zero crosstalk with external dictation: the mic stream is closed during dictation, so no audio leaks into voice-commander's pipeline.
- No session teardown or rebuild: the Scroll Lock session survives the mute/unmute cycle; the user does not need to re-press Scroll Lock after dictation ends.
- Single `pynput` Listener for all bindings — no extra thread, no extra OS hook registration.
- Feature is fully opt-in: leaving `mute_key` empty in `config.toml` (the default) disables the feature entirely with no behavioral change.

### Negative

- User must manually toggle mute; there is no auto-detection of the dictation app becoming active. A user who forgets to mute will still get crosstalk.
- If `pynput` drops a keypress, mute state can desync (user believes they are muted but voice-commander is still listening, or vice versa). Mitigation: Scroll Lock close always resets both `session_active` and `muted` to `False`, providing a guaranteed recovery path.

### Neutral

- `HotkeyController` API changes from `(key, on_toggle)` constructor signature to a `bindings: dict[str, Callable]` constructor — all callers (primarily `daemon.py`) must update. The threading and suppression model is unchanged.
- Thread-safety model is unchanged: the `pynput` callback acquires `_lock`, mutates daemon state, then releases — same pattern as the existing session toggle.

## Alternatives considered

### Auto-detect dictation app via process sniffing

Rejected. Detecting when a specific external process is active (e.g., polling the foreground window or watching for a process by name) is OS-specific, fragile across OS versions, and adds significant complexity. It also creates a polling loop or event hook that runs continuously. The manual toggle achieves the same outcome with far less machinery.

### Hold-to-mute (press-and-hold silences, release resumes)

Rejected. Hold-to-mute requires tracking both keydown and keyup events, adding debounce/timer logic to distinguish a hold from a tap, and coordinating with the external dictation app which is itself listening to the same key. Toggle is simpler, matches the existing Scroll Lock toggle pattern, and is more reliable with `pynput`'s key-event model.

### Separate `pynput` Listener per binding

Rejected. Each `pynput` Listener spawns a listener thread and registers a separate OS keyboard hook. A single Listener with an internal dispatch table achieves the same result with one thread and one hook registration. More bindings in the future would not add threads.

### Tri-state enum instead of two boolean flags

Rejected. A tri-state (e.g., `INACTIVE`, `ACTIVE`, `MUTED`) conflates two independent concepts under a single owner. Scroll Lock owns whether a session exists; the mute key owns whether audio is suppressed within a session. Two bools with explicit ownership are clearer, easier to reason about at the call sites, and avoid edge-case confusion about what "transition from MUTED to INACTIVE" means when the Scroll Lock is pressed.

## References

- [ADR 0013: drop-winrt-toasts-audio-only-feedback.md](0013-drop-winrt-toasts-audio-only-feedback.md) — audio-only feedback policy; motivates silent no-op on inactive-session mute press
- [ADR 0014: miss-only-chimes.md](0014-miss-only-chimes.md) — silent start/stop; reinforces non-interruptive feedback approach
- [ADR 0015: vad-streaming-mode.md](0015-vad-streaming-mode.md) — toggle session model that this feature extends
- Implementation files: `src/voice_commander/hotkey.py`, `src/voice_commander/daemon.py`, `src/voice_commander/config.py`
