# ADR 0019: Supersede Single-Shot Recorder and Phase Daemons

**Status:** Accepted
**Date:** 2026-04-20
**Supersedes:** ADR 0010 (threading model) — partially; the queue pattern is retained but the thread count and roles change.

## Context

The Phase 1–5 codebase contains:
- `Recorder` — manages a single `sounddevice.rec()` buffer between two Scroll Lock presses; writes `recorded.wav` on stop.
- `Phase1Daemon`, `Phase2Daemon`, `Phase3Daemon` — incremental daemon implementations kept for archaeological reference as each phase was developed.
- `build_phase1()`, `build_phase2()`, `build_phase3()` factory functions in `daemon.py`.

VAD streaming mode (ADR 0015) is a clean architectural replacement for `Recorder`. The new `StreamingRecorder` owns the sounddevice stream, feeds raw PCM to the VAD worker, and emits utterance ndarrays — a fundamentally different interface and lifecycle than `Recorder`. Attempting to maintain both behind a feature flag or an abstract base class would require:
- An `AbstractRecorder` interface that satisfies both "write WAV on stop" and "emit ndarray stream" — semantically incompatible contracts.
- Dual-mode daemon logic with conditional branches throughout startup, shutdown, and the pipeline worker.
- Test coverage for two code paths that will never both be production-active simultaneously.

The Phase daemons (`Phase1Daemon` etc.) have no users outside their own test files. They were scaffolding for incremental delivery, not components expected to survive Phase 5. Keeping them inflates the import surface, confuses new contributors, and means any refactor of shared subsystems must be validated against three dead daemon variants.

## Decision

Execute a **clean break**:

1. **Delete `Recorder`** (`src/voice_commander/recorder.py`). Replace with `StreamingRecorder` (`src/voice_commander/streaming_recorder.py`).
2. **Delete `Phase1Daemon`, `Phase2Daemon`, `Phase3Daemon`** and their `build_phaseN()` factories from `daemon.py`. The sole remaining daemon class is `StreamingDaemon`.
3. **Delete associated test files** that test the deleted classes (`tests/unit/test_recorder.py`, `tests/integration/test_phase*.py`). Replace with `tests/unit/test_streaming_recorder.py` and `tests/integration/test_streaming_pipeline.py`.
4. **No fallback mode.** There is no config flag or runtime switch to revert to single-shot recording. VAD streaming is the only supported recording model going forward.
5. **`outputs/last_utterance.wav`** replaces `outputs/recorded.wav` as the debug artifact path (per ADR 0018).

`StreamingDaemon` wires the four threads defined in ADR 0015 and is the sole entrypoint from `__main__.py`.

## Consequences

### Positive
- `daemon.py` loses ~150 lines of dead phase scaffolding; the file becomes a clean description of one architecture.
- No dual-mode conditional branches; every code path is exercised in production.
- `StreamingRecorder` has a well-defined interface (`start_session()`, `stop_session()`, utterance callback) that is unambiguously different from the old `Recorder` (`start()`, `stop() -> Path`); naming alone makes the migration legible.
- Test suite shrinks to cover one recording model, reducing maintenance surface.

### Negative
- The old single-shot recording model cannot be restored by configuration — code must be recovered from git history. Accepted: VAD is strictly better for the stated use case (rapid voice command sequences); no regression scenario requires reverting.
- Contributors who read old ADRs (0010, Phase 1–3 docs) will find references to classes that no longer exist. Mitigation: this ADR is cross-linked from those documents; deleted class names are noted here for grep-ability: `Recorder`, `Phase1Daemon`, `Phase2Daemon`, `Phase3Daemon`, `build_phase1`, `build_phase2`, `build_phase3`.

### Neutral
- The `queue.Queue` pattern from ADR 0010 is retained between the VAD worker and the pipeline worker; only the producer side changes (from a `Path` to a `np.ndarray`).
- The `FeedbackSink` protocol, `ToolRegistry`, `Matcher`, and `Dispatcher` are unchanged. This deletion only affects the recording and daemon layers.

## Alternatives considered

### Dual-mode daemon with `use_vad` config flag
Rejected: maintains two code paths indefinitely. Every future change to the pipeline worker must be validated against both modes. The config flag becomes permanent technical debt.

### Abstract `BaseRecorder` with `SingleShotRecorder` and `StreamingRecorder` subclasses
Rejected: the two recorders have incompatible lifecycles (`stop() -> Path` vs. utterance callback stream). A common interface would be so abstract as to be useless, and the type system would not prevent misuse.

### Keep phase daemons in a `legacy/` subdirectory
Rejected: code in the repo is code that must be maintained and tested. If it has no production role, it belongs in git history, not in the working tree.

## References
- ADR 0010 (threading model, partially superseded): `0010-threading-model.md`
- ADR 0015 (VAD streaming mode): `0015-vad-streaming-mode.md`
- ADR 0018 (ndarray handoff): `0018-ndarray-handoff-to-whisper.md`
- Git history preserves deleted classes: `git log --all -- src/voice_commander/recorder.py`
