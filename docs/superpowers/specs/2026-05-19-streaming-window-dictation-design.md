# Streaming-Window Dictation — Design Spec

**Date:** 2026-05-19
**Status:** Approved in brainstorming — pending implementation plan
**Topic:** Rework dictation transcription from hard VAD-segmented chunks to a growing-window streaming model with correct LocalAgreement-2.

## Problem

Dictation currently segments audio with VAD: every ~250 ms of silence
(`[vad] min_silence_duration_ms`) closes an utterance, each utterance is encoded
to an independent WAV chunk, streamed to the transcription server, and decoded
alone. This fragments the pasted output:

- Stray `...` ellipses wherever the speaker paused.
- Sentences broken mid-thought; random short fragments.
- Word loss / duplication at chunk boundaries.

**Root cause.** Whisper is a 30-second *batch* model trained on complete
utterances. Hard-cutting on short silences violates its training assumptions —
an isolated ~1 s clip hallucinates roughly half the time, which is the source of
the stray `...`. Compounding it: the daemon's `LocalAgreement` is fed
*independent-chunk* transcripts, not consecutive hypotheses of the *same growing
audio*, so it concatenates rather than stabilises — the algorithm is not doing
its real job.

The transcription server (`/ws/transcribe`, custom Python/FastAPI wrapping
faster-whisper) decodes every WAV chunk fully independently; cross-chunk
continuity is text-only via `initial_prompt`, with no audio-level decoder state.

## Goal

Dictation transcribes a *growing audio window* re-decoded on a fixed cadence.
LocalAgreement-2 commits stable words by comparing consecutive window
hypotheses. The window is trimmed at committed segment boundaries so it stays
bounded. VAD is demoted to endpointing only. The final transcript is the
daemon's de-duplicated committed text, LLM-cleaned by the proxy.

Result: coherent, punctuated dictation output; no pause-fragmentation; bounded
latency independent of dictation length.

## Non-goals

- No change to command-mode VAD, routing, or any non-dictation path.
- No on-device LLM — cleanup stays server-side (ADR 0093).
- No migration off Whisper to a streaming-native model (RNN-T / transducer).

## Background — why this is the right shape

Established streaming-Whisper practice (UFAL `whisper_streaming`,
Macháček et al. 2023; and production systems):

- **VAD is for endpointing, not chunk boundaries.** It detects that the speaker
  stopped; it does not slice the transcription buffer.
- **Overlapping / growing windows** keep the decoder's context across pauses so
  the model never sees an isolated silence clip.
- **LocalAgreement-2** commits a word only when two consecutive decode passes of
  the same growing audio agree on it; the unstable tail is discarded. This is
  what removes the stray `...`.
- **Two-stage**: a fast raw streaming transcript plus a separate LLM cleanup
  pass — already present here as the ADR 0093 proxy.

The daemon already ships `LocalAgreement` (ADR 0091/0092); this spec feeds it
correctly for the first time.

## Architecture

### Current path (ADR 0092 / 0093 / 0094)

```
PortAudio --> _raw_q --> VAD worker (resample 48k->16k, 512-sample frames)
   --> VADGate --> completed utterance --> DictationSession.handle_utterance
   --> encode_wav --> WS chunk --> server (per-chunk independent decode)
   --> partial --> LocalAgreement --> finish() --> done.text --> paste
```

### New path

```
PortAudio --> _raw_q --> VAD worker --> [ VADGate (endpointing) ]
                                        [ frame tap (dictation)  ]
frame tap --> DictationWindow (growing 16 kHz buffer)
   --every window_step_ms--> window WAV --> WS --> server decodes whole window
   --> partial{text, segments} --> LocalAgreement-2 commits prefix
   --> DictationWindow.commit(seg_end) trims buffer
end --> finish(): flush --> full committed raw text
   --> end frame {raw_transcript} --> proxy LLM-clean --> done.text --> paste
```

## Components

### 1. Frame tap — `StreamingRecorder`

- New method `set_frame_tap(callback | None)`. At most one consumer; `None`
  clears it; default unset (command mode unaffected).
- The VAD worker thread invokes the tap with each resampled 16 kHz / 512-sample
  float32 frame, alongside the existing `VADGate.process` call.
- `DictationSession.start()` registers the tap; `finish()` and `cancel()` clear
  it.
- A raising tap callback must be caught and logged — it must never kill the VAD
  worker thread.

### 2. `DictationWindow` — new, `src/voice_commander/dictation/window.py`

Owns the growing 16 kHz mono float32 audio buffer for one dictation.

- `append(frame)` — called by the frame tap (VAD worker thread).
- Emits the current window as a WAV every `window_step_ms` of *accumulated*
  audio (cadence driven by sample count, not wall-clock).
- `commit(committed_end_s: float)` — drops buffer audio up to that timestamp;
  advances an internal `committed_offset` (samples).
- Cap: when uncommitted buffered audio exceeds `window_cap_ms`, force-commit the
  oldest whole segment so the window stays bounded.
- Thread-safe: frames arrive on the VAD worker thread, windows are read on the
  asyncio loop thread — guard the buffer with a lock or hand off via a queue.

### 3. WebSocket client — `ws_client.py`

Sends window WAVs (whole growing window) rather than per-utterance chunks. The
streaming loop reads `partial` replies that now carry a `segments` array. The
`{"type":"end"}` send is extended to carry `raw_transcript` (see §6 and the
server contract). The `done`-frame read (ADR 0094) is unchanged.

### 4. LocalAgreement-2 — `local_agreement.py`

Fed consecutive *whole-window* hypotheses (each a transcript of the same growing
audio). Commits the longest agreeing prefix across the last two hypotheses;
discards the unstable tail. Returns the committed words and the segment end-time
of the last committed word, which `DictationWindow.commit` uses as the trim
point. Alignment must account for `committed_offset` after the buffer is trimmed
mid-stream (hypothesis timestamps are relative to the *current* window).

### 5. Endpointing — VAD demoted

VADGate still produces utterances, transcribed locally only to detect the
end-word (`done`) and cancel-word — the existing detection path is unchanged.
Those utterances are **no longer** sent as transcription chunks. All end paths
(end-word, Right-Ctrl hotkey, silence, Scroll-Lock cancel) are unchanged.

### 6. Finalization — `finish()`

Stop the frame tap; flush `DictationWindow` (send the final window, await the
last `partial`, run the final LocalAgreement commit); assemble the full
committed raw transcript; send `{"type":"end","raw_transcript":<text>}`; read
the `done` frame; return `done.text` (ADR 0094 path preserved). If no `done`
frame arrives, fall back to the locally committed text.

## Server contract additions (VPS — faster-whisper proxy)

Implemented by the VPS engineer; ~15% of the work. Both are backward-compatible.

- **Segment timestamps.** Every `partial` reply gains
  `segments: [{"start": float, "end": float, "text": str}, ...]` — seconds
  relative to the sent WAV (faster-whisper segment objects already carry
  `.start/.end/.text`). Existing `text` and `accumulated` fields retained.
- **Text-clean on end.** The `end` frame accepts an optional `raw_transcript`.
  When present and non-empty: the server LLM-cleans that text directly (no
  whisper decode) and returns
  `{"type":"done","text":<cleaned>,"raw":<raw_transcript>}`. When present and
  empty: returns `{"type":"done","text":"","raw":""}`. When absent: current
  behaviour, unchanged.
- `accumulated` becomes unreliable for the daemon (overlapping windows
  double-count it) — the daemon ignores it; the daemon's LocalAgreement output
  is authoritative. No server-side fix to `accumulated` is required.

## Configuration — `[dictation]`

New keys (added to `config.toml.example` with inline comments):

- `window_step_ms` — default `1000`. Cadence at which the growing window is
  re-decoded.
- `window_cap_ms` — default `25000`. Max uncommitted window length; beyond it
  the oldest segment is force-committed.

Existing keys (`ws_url`, `language`, `end_word`, `cancel_word`,
`idle_timeout_seconds`) unchanged.

## Error handling

- WebSocket connect failure → `error="endpoint"`, empty transcript, miss chime
  (existing behaviour).
- No `done` frame at end → fall back to the locally committed transcript
  (ADR 0094 fallback).
- Server omits `segments` (e.g. an un-upgraded server) → trimming falls back to
  a time-based policy: keep the last `window_cap_ms` of audio, commit older
  text — degraded but functional. (Plan decides: degrade vs hard-require.)
- Frame-tap callback raising → caught and logged; VAD worker unaffected.

## Threading

- The frame tap fires on the VAD worker thread; `DictationWindow.append` must be
  cheap and thread-safe.
- Window emission and WebSocket sends run on the asyncio loop thread.
- `LocalAgreement` is touched by the loop thread (`_on_partial`) and by
  `finish()` (the `_dictation_executor` thread) — the existing `_agreement_lock`
  pattern covers this.

## Testing

- `DictationWindow` — append/emit cadence, `commit` trim, `window_cap_ms`
  force-commit; pure unit tests with synthetic frame arrays.
- LocalAgreement-2 — consecutive-hypothesis prefix commit, including
  timestamp-carrying hypotheses and post-trim offset alignment.
- Frame tap — registered on `start`, cleared on `finish`/`cancel`; command mode
  receives no tap.
- Visual E2E (`scripts/`, per `docs/agents/visual-e2e-testing.md`) — dictate a
  multi-sentence paragraph with deliberate ~2 s pauses; assert the pasted text
  has no stray `...` and sentence boundaries are intact.

## Docs / ADR

- New ADR 0095 — streaming-window dictation.
- Update `docs/dictation-streaming.md` and `docs/transcription-pipeline.md` §4.
- Correct or replace the stale `docs/references/whisper-cpp-server-inference.md`
  — the server is custom Python/FastAPI + faster-whisper, not whisper.cpp's
  `/inference` endpoint.
- Update the CLAUDE.md "Current state" dictation paragraph.

## Open items for the implementation plan

- Whether to degrade gracefully or hard-require the `segments` field when the
  server has not been upgraded.
- Exact LocalAgreement-2 offset alignment when the window is trimmed mid-stream
  (hypothesis timestamps are relative to the current, post-trim window).
- Whether the live HUD `transcript` event follows the committed prefix or the
  latest raw window hypothesis.

## Cross-repo coordination

~85% of the work is in voice-commander (frame tap, `DictationWindow`,
LocalAgreement-2 fix, config, finalize). The VPS engineer implements the two
server contract additions above; the brief has been sent and can land
independently and ahead of the daemon-side work, since both additions are
backward-compatible.
