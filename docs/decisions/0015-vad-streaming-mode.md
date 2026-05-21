# ADR 0015: VAD Streaming Mode Replaces Single-Shot Recording

**Status:** Accepted
**Date:** 2026-04-20

## Context

The Phase 1–5 MVP uses a single-shot recording model: press Scroll Lock to start, speak one command, press Scroll Lock again to stop. One command costs two keypresses. For rapid command sequences — "copy, paste, new tab, reload" — this is a rhythm-breaking friction: press, speak, press, press, speak, press, press, speak, press. The user's hands keep returning to Scroll Lock instead of staying where the work is.

There is also a latency gap: the pipeline idles between the second keypress (recording stop) and the next first keypress (recording start). Transcription and matching could overlap with the next recording if the model were aware of session boundaries.

## Decision

Replace the single-shot `Recorder` + `Phase*Daemon` stack with a session-based **VAD streaming mode**:

1. **One press opens a session.** `Scroll Lock` down starts a continuous audio capture loop.
2. **silero-vad auto-segments utterances.** A VAD worker thread monitors the raw audio stream and emits discrete utterance buffers whenever speech is detected and then falls silent.
3. **Second press closes the session.** `Scroll Lock` down again drains any in-flight utterance and shuts the capture loop.
4. **Serial FIFO queue for pipeline overlap.** Each completed utterance buffer is enqueued immediately. The pipeline worker (transcribe → match → dispatch) processes them FIFO while the VAD worker continues listening for the next utterance.

Threading layout under the new model:

| Thread | Role |
|--------|------|
| pynput listener | Toggle session open/closed |
| Audio callback (sounddevice) | Push raw PCM chunks to a ring buffer |
| VAD worker | Consume ring buffer, run silero-vad, emit utterance ndarrays |
| Pipeline worker | Dequeue utterances, transcribe, match, dispatch |

## Consequences

### Positive
- Zero keypresses between commands inside a session — one press opens, one closes, unlimited commands in between.
- Pipeline overlap: transcription of utterance N runs while the VAD worker is already collecting utterance N+1.
- More natural interaction model: open a session, rattle off several commands, close it.

### Negative
- VAD threshold requires tuning — too sensitive and breath sounds trigger utterances; too coarse and fast speech is clipped. Addressed in `config.toml` via `vad_threshold`.
- Threading model grows from 3 threads to 4; the VAD worker is a new coordination point with its own error surface.
- silero-vad model must be loaded at startup alongside the Whisper model, adding ~50 ms to daemon init and ~2 MB to resident memory.

### Neutral
- The `FeedbackSink` protocol is unchanged; `on_recording_start` / `on_recording_stop` now map to session open/close rather than single-utterance boundaries.
- The queue between the VAD worker and pipeline worker uses the same `queue.Queue` pattern established in ADR 0010.

## Alternatives considered

### Push-to-talk but with server-side VAD (no session concept)
Keep two-keypress model but add VAD to auto-trim silence from the WAV before transcription. Rejected: reduces transcription noise but does not remove inter-command keypresses, which is the primary UX complaint.

### Wake-word activation instead of hotkey
Replace Scroll Lock with a wake word ("commander"). Rejected for MVP: requires always-on audio and a wake-word detector (another model), no offline-capable option with <5 ms latency is trivially available. Tagged for Phase 6+.

## References
- silero-vad: https://github.com/snakers4/silero-vad
- ADR 0010 (threading model): `0010-threading-model.md`
- ADR 0016 (silero-vad selection): `0016-silero-vad-over-webrtcvad.md`
- ADR 0019 (superseding old recorder): `0019-supersede-single-shot-recorder.md`
