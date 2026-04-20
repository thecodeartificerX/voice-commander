# ADR 0014: Miss-Only Chimes (Silent on Start/Stop)

**Status:** Accepted
**Date:** 2026-04-20
**Amends:** [ADR 0008](0008-winsound-for-chimes.md), [ADR 0013](0013-drop-winrt-toasts-audio-only-feedback.md)

## Context

ADR 0013 committed to audio-only feedback with three chimes: `start.wav` on recording-start, `stop.wav` on recording-stop, `miss.wav` on no-match. During Phase 4 live validation — once the full 14-tool MVP was running end-to-end — the start/stop chimes turned out to be noise, not signal.

The user already knows when they pressed Scroll Lock (they pressed it) and when they stopped speaking (they pressed it again). The chimes confirm an action the user just performed, adding auditory load without new information. Over the course of a normal session (dozens of commands), two chimes per command becomes a constant beep-boop background that the user explicitly objected to.

The miss chime is different: it signals something the user did *not* already know — that the utterance failed to match. That's genuine information with no alternative channel (the log is fine for post-hoc debugging but useless mid-command).

## Decision

1. `WindowsFeedbackSink.on_recording_start()` and `on_recording_stop()` are no-ops. No sound plays on toggle.
2. `WindowsFeedbackSink.on_miss()` still plays `miss.wav`. Unchanged.
3. The `FeedbackSink` protocol keeps `on_recording_start` / `on_recording_stop` — they remain hooks for the daemon to call, and other sinks (tray state, tests) still rely on them firing. Only the Windows audio sink silences them.
4. `start.wav` and `stop.wav` stay on disk under `assets/sounds/` — cheap to keep, and re-enabling them is a two-line change if the decision is ever reversed.
5. No config flag for this. One-dimensional preference, silent-by-default wins.

## Consequences

### Positive
- Quiet operation. Commands feel instant and invisible — the intended UX.
- Miss chime's signal-to-noise ratio jumps: when you hear a beep, something's wrong. Before, miss beeps were lost among start/stop beeps.
- No code removed from the protocol or sink — other subscribers (tray icon in Phase 5) still get state transitions.

### Negative
- If the user ever doubts whether Scroll Lock registered, there is no audio confirmation. Mitigation: the tray icon (Phase 5) will change color on recording/thinking/idle, covering this case with a visual channel that doesn't interrupt.
- Silent-failure mode: if `pynput` drops a keypress, the user presses Scroll Lock and nothing happens — and nothing is heard either. Pre-change, the absence of a start chime would have been the tell. Accepted: this is a reliability concern about the hotkey listener, not the feedback layer, and the log still records the event.

### Neutral
- ADR 0008's choice of `winsound` is unaffected — the miss chime still uses it.
- ADR 0013's rationale (audio-only, non-interruptive) still holds; this ADR tightens "audio-only" to "audio-only on exceptions."

## Alternatives considered

### Keep all three chimes with user-configurable mute flags
Rejected. Adds a `FeedbackConfig` surface (three booleans) for a preference that has an obvious default. If someone wants the chimes back, they edit two methods.

### Single low-volume tick instead of full chime
Rejected. Still adds auditory load per command. The problem isn't loudness; it's that any sound-per-toggle is redundant with the user's own keypress.

### Delete start.wav / stop.wav from assets
Rejected. Zero storage cost to keep them; easy reinstatement path if the call is wrong.

## References
- Amended ADR: [0013-drop-winrt-toasts-audio-only-feedback.md](0013-drop-winrt-toasts-audio-only-feedback.md)
- Implementation: `src/voice_commander/feedback.py` — `on_recording_start` / `on_recording_stop` on `WindowsFeedbackSink` are `pass`.
