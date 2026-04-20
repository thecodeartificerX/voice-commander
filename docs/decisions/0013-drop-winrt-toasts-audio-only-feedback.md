# ADR 0013: Drop WinRT Toasts, Rely on Audio Chimes Only

**Status:** Accepted
**Date:** 2026-04-20
**Supersedes:** [ADR 0007](0007-windows-toasts-over-tkinter.md)

## Context

ADR 0007 committed to WinRT toast notifications via the `windows_toasts` package for user-visible feedback on match / miss / recording events. That decision held for Phase 1 and Phase 2. During Phase 3 validation two problems surfaced, one UX and one technical.

**UX problem.** The toast is interruptive in the worst way for this class of product: a voice-command launcher is used in short, repeated bursts. Each command fires another toast. Toasts stack in the Action Center, pull focus from the user's current task for ~300 ms while the tray slides in, and leave residue the user later has to clear manually. The audio chimes already communicate the three states the user cares about — recording started, recording stopped, no match — and they do it in the peripheral channel (ear) that doesn't compete with whatever the user is doing visually.

**Technical problem.** Importing `windows_toasts` eagerly at module scope corrupts the CUDA DLL loading path and causes `faster_whisper.WhisperModel.__init__()` to crash with `STATUS_ACCESS_VIOLATION` during model construction — silently, with no Python traceback. See [`docs/gotchas.md`](../gotchas.md) §10 for the full diagnosis. We mitigated this with a lazy import inside `WindowsFeedbackSink._toast()` in commit `e3a8fe7`, but the underlying fragility remains: any future change that re-promotes the import to module scope, or any other WinRT-loading helper we add later, reintroduces the crash. The surface area is large and the failure mode is silent.

Removing toasts entirely eliminates both problems in one move.

## Decision

1. Strip WinRT toast code from `WindowsFeedbackSink`: the `_toast()` helper, the `_toaster` field, and the toast calls inside `on_match` / `on_miss` are all gone. `on_match` now only logs; `on_miss` plays the miss chime and logs.
2. Drop `toast_enabled` and `toast_show_transcript` from `FeedbackConfig` and `config.toml`. No config surface for toasts.
3. Remove `windows-toasts` (and its transitive `winrt-*` packages) from `pyproject.toml`. Re-lock. The venv no longer carries the WinRT runtime.
4. Delete `tests/unit/test_feedback_toast.py` and `docs/references/windows-toasts.md`.
5. User-visible feedback is now exclusively audio: `start.wav` on recording-start, `stop.wav` on recording-stop, `miss.wav` on low-confidence transcription or fuzzy-match miss. All dispatched via `winsound.PlaySound(..., SND_ASYNC)` — fire-and-forget, non-blocking.
6. Log-level visibility remains: every match / miss / error still writes to `voice-commander.log` for debugging.

## Consequences

### Positive
- No visual interruption. The tool stays out of the user's way. Chimes give enough signal to know the voice command was heard and matched / missed.
- CTranslate2's CUDA init is no longer at risk from WinRT side effects. The windows_toasts import-order gotcha is moot: the package is not installed.
- Venv shrinks. `windows-toasts` pulled five `winrt-*` transitive packages; all gone.
- `WindowsFeedbackSink` constructor is smaller: four params instead of six. Fewer config keys to document and test.
- Phase-3 GATE becomes simpler — no "expect a toast with the following text" checkpoint.

### Negative
- Users who liked seeing the recognised phrase + score on-screen lose that. Mitigation: they can still tail `voice-commander.log` in a terminal if they want structured visibility.
- No way to signal errors visually (e.g. daemon couldn't access the clipboard). The log catches this; a user-facing surface would need a new mechanism (status tray, error beep, etc.) — deferred to Phase 5 hardening if we ever want it.

### Neutral
- ADR 0007's reasoning about Tkinter (thread-blocking GUI event loops) is still correct. If we ever re-introduce visual feedback we would revisit that argument, but the current answer is "don't introduce any visual feedback at all."
- `docs/gotchas.md` §10 is kept but flagged as "no longer a live issue" because the offending package is no longer a dependency. The cautionary note about WinRT-loading packages interfering with CUDA init stays — relevant for any future dev tempted to add `winsdk`, `winrt-*`, or similar.

## Alternatives considered

### Keep toasts but keep them lazy-imported (status quo after `e3a8fe7`)
Rejected. Fixes the crash but leaves the UX problem. Also leaves a fragile rule for future devs: "never promote this import to module scope." One careless refactor reintroduces the crash.

### Move to a status-tray indicator (pystray or similar)
Rejected for now. Same class of Windows-integration complexity that motivated ADR 0007 in the first place, and pystray has its own event-loop / threading concerns. If we later want persistent visual state (e.g. "daemon is listening"), Phase 5 can revisit.

### Console/terminal display instead of toasts
Rejected. The daemon is intended to run minimized or in a tray — users don't want to keep a terminal visible to see match output. Logs handle the "I want to see what just happened" case.

### Custom chime per outcome (match-success chime, match-miss chime, error chime)
Partially adopted — we already have `start.wav`, `stop.wav`, and `miss.wav`. Adding a match-success chime is cheap and could happen later without an ADR.

## References
- Superseded ADR: [0007-windows-toasts-over-tkinter.md](0007-windows-toasts-over-tkinter.md)
- Gotcha: [`docs/gotchas.md`](../gotchas.md) §10 — windows_toasts eager-import CUDA crash
- Implementation: commit that ships this ADR also rips the toast code from `feedback.py`, `config.py`, `daemon.py`, and drops the `windows-toasts` dependency.
