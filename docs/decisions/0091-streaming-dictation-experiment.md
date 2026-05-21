# ADR 0091 — Streaming Dictation Experiment (WebSocket + LocalAgreement)

**Status: SUPERSEDED by [ADR 0096](0096-server-side-dictation.md)** — the streaming-window prototype and LocalAgreement experiment are deleted; the daemon no longer does client-side transcript assembly.

**Status:** Experimental
**Date:** 2026-05-18
**Extends:** [ADR 0086](0086-dictation-mode.md) — an alternative transcription path, not yet wired into the daemon

## Context

Shipped dictation (ADR 0086, amended 0090) is batch: VAD buffers the whole
utterance, the daemon POSTs one WAV to the remote whisper.cpp `/inference`
endpoint, and pastes the result once. Latency scales with utterance length and
there is no cross-chunk decoding context.

This ADR records an isolated experiment in streaming transcription. It is not
wired into the daemon; it runs standalone. If it proves out, a later, separate
ADR will cover replacing the batch path.

## Decision

### D1 — Isolated `dictation_stream` package

A new self-contained package `src/voice_commander/dictation_stream/` holds the
experiment. It is run standalone via `python -m voice_commander.dictation_stream`.
The shipped `voice_commander.dictation` package and `daemon.py` are not edited.

### D2 — Hybrid chunking reuses `VADGate`

The chunker drives the existing `VADGate` with dictation-tuned parameters
(`threshold=0.5`, `min_silence_ms=1200`, `speech_pad_ms=300`,
`max_utterance_ms=15000`). `VADGate` already emits one utterance per speech-end
and force-caps at `max_utterance_ms` — that dual trigger is the hybrid silence
+ hard-time-cap chunking. Chunks under 1 s are discarded.

### D3 — WebSocket streaming with server-side prompt carry

Each WAV chunk streams to the confirmed-live `ws://192.168.4.200:8765/ws/transcribe`
endpoint. The protocol: a one-time `{"type":"config","language":"<lang>"}`
handshake, binary WAV frames, `{"type":"partial"}` / `{"type":"error"}` replies,
`{"type":"end"}` on stop. The client also ends the session if no audio arrives
within the idle timeout. The server carries `initial_prompt` context across
chunks.

### D4 — LocalAgreement word stabilisation

Chunk boundaries land mid-sentence, so each chunk's suffix is unstable.
`LocalAgreement` holds back a chunk's tail and commits a word only when the
next chunk confirms it by overlap. `finalize()` flushes the held-back tail at
session end.

### D5 — Accumulate-then-paste with a `transform` seam

Confirmed words accumulate; at session end the full transcript passes through
`transform(text) -> text` (identity now) and is pasted via a clipboard
round-trip. `transform` is the seam for a future small-LLM rewrite step.

### D6 — Right Ctrl toggle, 50 ms debounce

The standalone runner binds Right Ctrl (matching the shipped `dictation_key`).
A first press starts a session, the next stops it. A 50 ms debounce drops
driver double-release events, as in the shipped daemon.

### D7 — Selectable input device

`MicCapture` opens the Windows default input device unless `[dictation_stream]
input_device` names another — an integer device index or a name substring.
Threaded `config -> StreamSession -> MicCapture`; `None` (the default) preserves
the system-default behaviour. Needed because a user's preferred mic (e.g. an
external USB condenser) is often not the OS default capture device.

## Consequences

### Positive

- Streaming transcription with progressive, context-carrying decoding.
- Zero risk to shipped dictation — the experiment is fully isolated.
- The chunker reuses proven, torch-free ONNX VAD code.

### Negative

- A second dictation code path exists until the experiment is resolved.
- Adds a `websockets` dependency.

### Neutral

- The experiment is standalone; integration into the daemon is future work.
- `transform` is identity until the LLM rewrite step lands.

## References

- [ADR 0086](0086-dictation-mode.md) — shipped batch dictation
- `docs/dictation-streaming.md` — subsystem overview
- `src/voice_commander/dictation_stream/` — implementation
- `docs/references/websockets.md` — WebSocket library reference
