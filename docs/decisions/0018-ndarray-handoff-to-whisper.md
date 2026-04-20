# ADR 0018: Direct ndarray Handoff to faster-whisper (No Temp WAV)

**Status:** Accepted
**Date:** 2026-04-20

## Context

In the single-shot model, `Recorder.stop()` writes a WAV file to `outputs/recorded.wav` and pushes the `Path` onto the pipeline queue. The transcriber reads that path:

```python
segments, info = model.transcribe("outputs/recorded.wav", ...)
```

In VAD streaming mode the VAD worker assembles a 16 kHz float32 ndarray for each detected utterance. The question is how to hand it off to faster-whisper.

The faster-whisper README shows only file-path usage. However, inspecting the source confirms that `WhisperModel.transcribe()` accepts either a `str | Path` or a `np.ndarray` of float32 audio samples directly. When an ndarray is passed, faster-whisper skips its internal `ffmpeg`-based decode step and feeds the array straight to the Whisper encoder. This is documented in the faster-whisper API reference and exercised in its own test suite.

Writing each utterance to a temp file before transcription would add:
- One `wave.open()` + write call per utterance (~1–3 ms for a 1-second utterance).
- One `wave.open()` + read call inside faster-whisper's decode path (~0.5–1 ms).
- Disk I/O on the hot path — unnecessary given the ndarray is already in memory.

## Decision

Pass the utterance **ndarray directly** to `WhisperModel.transcribe()`:

```python
segments, info = model.transcribe(
    utterance_f32,          # np.ndarray, shape (N,), dtype float32, 16 kHz
    language="en",
    beam_size=5,
)
```

Additionally, write the utterance to `outputs/last_utterance.wav` **asynchronously** (fire-and-forget `threading.Thread`) after enqueuing it for transcription. This provides a post-mortem WAV for debugging misses without adding to the critical-path latency.

The async WAV write is best-effort: if the write thread raises, it is logged and discarded. It does not affect transcription.

## Consequences

### Positive
- Eliminates temp file I/O from the hot path; utterance-to-transcript latency drops by ~2–4 ms per command.
- Simpler code: no temp path generation, no cleanup logic for transient files.
- Post-mortem WAV still available at a fixed path (`outputs/last_utterance.wav`) for manual inspection after a miss.

### Negative
- `outputs/last_utterance.wav` is written asynchronously and may lag the actual transcription by 10–50 ms. A human trying to inspect it immediately after a command may get the previous utterance if the write thread hasn't flushed. Acceptable: it is a debugging aid, not a user-facing feature.
- The ndarray handoff path in faster-whisper is not highlighted in the primary README; a future faster-whisper major version could deprecate or break it. Mitigation: pin faster-whisper in `pyproject.toml` and review on upgrade.

### Neutral
- The `outputs/` directory and single-overwrite retention policy (from the Phase 1 design) still apply — just to `last_utterance.wav` instead of `recorded.wav`.
- `NullFeedbackSink` and unit tests can pass a synthetic ndarray directly to the transcriber without any file I/O, simplifying test setup compared to the WAV-path model.

## Alternatives considered

### Write WAV synchronously, pass path to transcriber (existing approach)
Rejected for streaming: adds 2–4 ms of file I/O to every utterance on the critical path. Acceptable in single-shot mode (one command per keypress); unacceptable in streaming mode where multiple utterances queue up rapidly.

### Write WAV synchronously, pass ndarray AND path (dual handoff)
Rejected: complexity for zero benefit. The WAV is only needed for debugging; async fire-and-forget is sufficient.

### Use a `BytesIO` buffer instead of ndarray
Rejected: would require encoding the ndarray to WAV format in memory before passing to faster-whisper, which re-introduces the encode/decode overhead the ndarray path avoids.

## References
- faster-whisper `WhisperModel.transcribe` source: https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py
- faster-whisper ndarray input tests: https://github.com/SYSTRAN/faster-whisper/blob/master/tests/
- ADR 0017 (soxr resampler): `0017-soxr-streaming-resampler.md`
- ADR 0019 (superseding old recorder): `0019-supersede-single-shot-recorder.md`
