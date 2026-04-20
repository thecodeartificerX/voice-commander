# VAD Streaming Mode — Design Spec

**Date:** 2026-04-20
**Status:** Approved (brainstorming complete, ready for implementation plan)
**Supersedes parts of:** 2026-04-19-voice-commander-design.md (single-shot recording model)

## Problem

Current model: press Scroll Lock → record whole utterance → press Scroll Lock → transcribe → match → dispatch. One command per two key presses. Breaks flow for rapid command sequences.

## Goal

Replace with **session-based VAD streaming**: one Scroll Lock press opens a live voice-command session. silero-vad auto-segments utterances on natural silence. Each utterance fires the existing transcribe → gate → match → dispatch pipeline **immediately** on VAD-end. Second Scroll Lock press closes the session.

Result: a live command center — say "copy", clipboard updates; say "new tab", tab opens; say "reload", page reloads. Zero keypresses between commands. One press to start, one to stop.

## Non-goals

- Argument-bearing commands / LLM intent routing (Phase 6+).
- Always-on listening (explicit session model, not wake-word).
- Multi-command splitting within one utterance (user pauses → VAD splits; back-to-back words → one utterance, matcher treats as single string).
- Visual session indicator.
- Session open/close audio feedback (silent except miss-chime).

## Locked decisions

| Q | Decision | Why |
|---|---|---|
| Mode relationship | Replace single-shot toggle entirely | Clean break, no dual modes |
| VAD engine | silero-vad (ONNX, CPU) | Community standard, MIT, ~1ms/frame, robust on noise |
| Parameters | Command-tuned (see §Config) | Research-backed defaults, all in `config.toml` |
| Pipeline overlap | Serial FIFO queue | Simple, Whisper `small.en` on CUDA ≈ 300ms drains fast |
| Feedback | Miss-chime only | Extends ADR 0014; session is silent otherwise |
| False-wake filter | Layered: word-count + `no_speech_prob` + fuzzy threshold | Defense in depth |
| Session indicator | None | User knows they pressed the key |
| Utterance retention | Single overwriting `outputs/last_utterance.wav` | Matches existing retention philosophy; in-memory ndarray to Whisper for speed, WAV written async for post-mortem |
| Code strategy | Rip + replace (delete Recorder + Phase1/2/3Daemon) | Boil the ocean — no dangling modes |

## Architecture

### Thread topology (3-thread pipeline)

```
[PortAudio callback thread]      [VAD worker thread]            [Pipeline worker thread]
 sd.InputStream callback          Resampler + VADGate            Transcriber + gates + Matcher + Dispatcher
 48kHz float32 mono
        │                              ▲                                ▲
        │ chunk.copy()                 │ ndarray frames                 │ utterance ndarray
        ▼                              │                                │
    raw_q ────────────────────────────►│                                │
    (queue.Queue, maxsize=64)          │                                │
                                       │                                │
                                       └─── utt_q ──────────────────────┘
                                            (queue.Queue, maxsize=8)
```

Callback only copies + enqueues raw chunks. All VAD and pipeline work runs on worker threads. VAD model is not thread-safe → isolated to one worker.

### Data flow

1. Scroll Lock press (IDLE → OPENING) — `StreamingRecorder.open_session()` opens `sd.InputStream` at device-native rate (queried from `sd.query_devices`), creates fresh `Resampler`, `VADGate`, spawns VAD + pipeline workers.
2. Callback pushes raw 48kHz float32 chunks to `raw_q`.
3. VAD worker pulls chunk → `Resampler.process()` → 16kHz samples → buffer into 512-sample frames → `VADGate.process(frame)`.
4. `VADGate` maintains pre-roll ring (300ms). On speech-start event, begins accumulating active utterance from pre-roll + current frame. On speech-end event, pushes completed ndarray to `utt_q`, resets active buffer, keeps ring filling.
5. Pipeline worker pulls utterance ndarray from `utt_q` → async-writes `outputs/last_utterance.wav` in background thread → `Transcriber.transcribe(ndarray)` (direct ndarray API) → gate → `Matcher.match()` → `Dispatcher.dispatch()` or `FeedbackSink.on_miss()`.
6. Scroll Lock press again (LISTEN/CAPTURING → CLOSING) — stop stream, poison `raw_q` and `utt_q` with `None` sentinels, join threads. In-flight utterance completes through pipeline before threads join (no dropped tail command).

### Session state machine

```
      ┌───────────┐  ScrollLock press   ┌─────────────┐
      │   IDLE    │ ──────────────────► │  OPENING    │
      │ (no sd    │                     │ stream+     │
      │  stream)  │                     │ threads init│
      └───────────┘                     └──────┬──────┘
            ▲                                  │ stream.started
            │                                  ▼
            │ threads joined            ┌─────────────┐
            │ stream closed             │   LISTEN    │◄──────┐
      ┌─────┴─────┐                     │ VAD silent  │       │ utterance end
      │  CLOSING  │◄────────────────────┤  (no speech)│       │
      │ poison +  │  ScrollLock press   └──────┬──────┘       │
      │ drain qs  │                            │ speech start │
      └───────────┘                            ▼              │
            ▲                            ┌─────────────┐      │
            │ ScrollLock press           │  CAPTURING  │──────┘
            └────────────────────────────┤ VAD active  │
                                         └─────────────┘
```

Transitions:
- `IDLE` + press → `OPENING` → `LISTEN` (when `stream.started`).
- `LISTEN` + speech-start event → `CAPTURING`.
- `CAPTURING` + speech-end event → `LISTEN` (utterance emitted).
- `LISTEN` or `CAPTURING` + press → `CLOSING` → `IDLE` (when threads joined).
- Presses during `OPENING` / `CLOSING` — ignored, logged.
- `CLOSING` drains in-flight utterance through pipeline before `IDLE`.

## Subsystem contracts

### Deleted
- `Recorder` (`src/voice_commander/recorder.py`)
- `Phase1Daemon`, `Phase2Daemon`, `Phase3Daemon` + all `build_phaseN` factories (`src/voice_commander/daemon.py`)

### New

**`StreamingRecorder(device, channels, utterance_sink)`** — `src/voice_commander/streaming_recorder.py`
- `open_session() -> None` — opens `sd.InputStream` at device-native rate, starts VAD worker thread.
- `close_session() -> None` — stops stream, poisons queue, joins VAD worker.
- `is_open: bool`
- `utterance_sink: Callable[[np.ndarray], None]` — injected, called on each completed utterance (pipeline worker pushes to `utt_q` via this).

**`VADGate(model, threshold, min_speech_ms, min_silence_ms, speech_pad_ms, pre_roll_ms, max_utterance_ms)`** — `src/voice_commander/vad_gate.py`
- `process(frame_16k: np.ndarray) -> Optional[np.ndarray]` — fed 512-sample float32 frames. Returns completed utterance ndarray (pre-roll + speech + post-roll) on speech-end, else `None`.
- `reset() -> None` — clears VAD state (calls silero `reset_states()`) + ring buffer. Called on session open/close.
- Internal: wraps `silero_vad.VADIterator` + `collections.deque(maxlen=pre_roll_frames)`.

**`Resampler(src_rate, dst_rate=16000)`** — `src/voice_commander/resampler.py`
- `process(chunk: np.ndarray) -> np.ndarray` — stateful streaming resample via `soxr.ResampleStream`.
- `reset() -> None`.

**`StreamingDaemon`** — rewrite of `src/voice_commander/daemon.py`
- Owns `HotkeyController`, `StreamingRecorder`, `Transcriber`, `Matcher`, `Dispatcher`, `FeedbackSink`, pipeline worker thread, `utt_q`.
- Hotkey `on_toggle` → `recorder.open_session()` / `recorder.close_session()`.
- `StreamingRecorder.utterance_sink` = `utt_q.put` bound method.
- Pipeline worker loop identical in spirit to current `Phase3Daemon._worker_loop` but consumes ndarrays instead of WAV paths.

### Unchanged
`HotkeyController`, `Transcriber` (adds `transcribe(ndarray)` overload; keeps `transcribe(Path)` for test fixtures), `Matcher`, `Dispatcher`, `FeedbackSink`, `ToolRegistry`, `Config`, all tools under `tools/*`.

## Config schema

`config.toml` additions:

```toml
[vad]
threshold = 0.4                  # silero activation (0..1)
min_speech_duration_ms = 100     # drop pops/clicks below this
min_silence_duration_ms = 250    # hangover before utterance end
speech_pad_ms = 30               # post-roll kept in chunk
pre_roll_ms = 300                # pre-speech ring buffer kept
max_utterance_ms = 8000          # hard cap, force-end runaway
sample_rate = 16000              # silero requirement; 8000 also valid
window_samples = 512             # silero requirement @ 16k (32 ms)

[vad.gates]
min_word_count = 1               # drop empty transcripts
max_no_speech_prob = 0.6         # drop Whisper "silence" misfires
```

Existing sections unchanged. `config.py` gains `VadConfig` + `VadGatesConfig` dataclasses.

## Error handling

| Failure | Detection | Response |
|---|---|---|
| Mic disconnect mid-session | `sd` callback status / `CallbackAbort` | Log, miss-chime, force `CLOSING` → `IDLE`. Next press retries. |
| Xrun / input overflow | `status.input_overflow` in callback | Log warn, drop chunk, continue. |
| `raw_q` full (VAD worker starved) | `put_nowait` raises `queue.Full` | Log warn, drop chunk. Shouldn't happen with 32ms frames + soxr. |
| `utt_q` full (Whisper saturated) | same | Log warn, drop utterance, miss-chime. User talking faster than pipeline. |
| Resampler init fail | exception in `open_session()` | Log, `FeedbackSink.on_error`, stay `IDLE`. |
| silero model load fail (daemon start) | exception at load | Log, abort startup (same pattern as current `Transcriber.load`). |
| Whisper transcribe fail | exception in pipeline worker | Log, miss-chime, continue (don't kill worker). |
| VAD onnxruntime error | exception in VAD worker | Log, miss-chime on in-flight utterance, continue. **Two strikes in 5s → abort session.** |
| Hotkey press during OPENING/CLOSING | state check | Ignore, log debug. |
| Tool fn raises | caught in `Dispatcher` (existing) | Existing `FeedbackSink.on_error` path. |

Two-strike rule on VAD errors prevents a misbehaving ONNX session from spamming miss-chimes.

## Testing strategy

### Unit tests (`tests/unit/`)

- `test_resampler.py` — soxr wrapper: 48k→16k sine sweep, frame-count correctness, state preservation across chunks, `reset()` behaviour.
- `test_vad_gate.py` — feed canned 16k frames (synthetic speech bursts + silence), assert start/end events + pre-roll contents + speech_pad. Fixture: short WAV with known speech intervals.
- `test_streaming_recorder.py` — monkeypatch `sd.InputStream`, push frames on background thread, assert utterances emitted match expected boundaries. No real mic.
- `test_streaming_daemon.py` — inject stub `Transcriber` / `Matcher` / `Dispatcher`, feed fake utterance ndarrays to the pipeline queue, assert dispatch + miss paths + state transitions. No real mic, no real Whisper.
- `test_gates.py` — word-count / no-speech-prob / fuzzy-threshold gating matrix.

Replaces: `test_recorder.py`, `test_phase1_daemon.py`, `test_phase2_daemon.py`, `test_phase3_daemon.py` (any of those that exist).

### Integration tests (`tests/integration/`)

- `test_streaming_pipeline.py` — canned multi-command WAV ("copy" <silence> "new tab" <silence> "reload") piped through `StreamingRecorder` with a file-backed fake stream → assert 3 utterances transcribed and dispatched. Real silero + real faster-whisper, CUDA-guarded (skip if no CUDA).
- `test_session_lifecycle.py` — open session, feed silence-only audio for 3s, close — assert zero utterances, zero misses, clean thread shutdown.
- `test_hotkey_toggles_session.py` — real `HotkeyController` + stub recorder; assert open/close calls match press count.

### Manual validation gate (human)

1. Press Scroll Lock → say "copy" (with text selected) → ~250ms later clipboard updates (no second press). Then say "new tab" → new tab opens. Then "reload" → page reloads. Press Scroll Lock → session closes. **Three commands fired live in one session.**
2. Press, say gibberish → miss-chime, no tool fires.
3. Press, silence 10s, press → no error, no chimes.
4. Press, talk, press during utterance → last command still completes (CLOSING drains).
5. Disconnect mic mid-session → clean recovery, no crash.

### Fixtures
- `tests/fixtures/vad_commands.wav` — recorded or synthesised multi-command clip.
- Canned silero bypass (stub `VADIterator`) for pure-logic tests that don't need the real ONNX model.

## File changes

```
src/voice_commander/
  recorder.py              [DELETE]
  streaming_recorder.py    [NEW]
  vad_gate.py              [NEW]
  resampler.py             [NEW]
  daemon.py                [REWRITE]
  config.py                [EDIT] add VadConfig + VadGatesConfig dataclasses, parse [vad] / [vad.gates]
  transcriber.py           [EDIT] add transcribe(ndarray) overload; keep transcribe(Path)
  __main__.py              [EDIT] call build_streaming_daemon

config.toml                [EDIT] add [vad] + [vad.gates] sections

tests/unit/
  test_recorder.py         [DELETE]
  test_phase*_daemon.py    [DELETE if exist]
  test_streaming_recorder.py  [NEW]
  test_vad_gate.py         [NEW]
  test_resampler.py        [NEW]
  test_streaming_daemon.py [NEW]
  test_gates.py            [NEW]

tests/integration/
  test_streaming_pipeline.py  [NEW]
  test_session_lifecycle.py   [NEW]
  test_hotkey_toggles_session.py  [NEW]

tests/fixtures/
  vad_commands.wav         [NEW]

docs/decisions/
  0015-vad-streaming-mode.md              [NEW]
  0016-silero-vad-over-webrtcvad.md       [NEW]
  0017-soxr-streaming-resampler.md        [NEW]
  0018-ndarray-handoff-to-whisper.md      [NEW]
  0019-supersede-single-shot-recorder.md  [NEW] retires Recorder + Phase1/2/3Daemon; addends 0001-0014 where session model changes key semantics
docs/architecture.md       [EDIT] rewrite pipeline section
docs/gotchas.md            [EDIT] add VAD RT-callback warning, onnxruntime thread-safety note, resampler state lifetime
docs/libraries.md          [EDIT] add silero-vad, onnxruntime, soxr

pyproject.toml             [EDIT] add silero-vad, onnxruntime, soxr
```

## References

- `docs/references/silero-vad.md` — API, streaming pattern, gotchas.
- `docs/references/vad-tuning-for-commands.md` — parameter values for short command utterances.
- `docs/references/streaming-audio-pipeline.md` — soxr streaming resampler, ring buffer, callback→queue topology. **Caveat:** asserts faster-whisper needs WAV path — that is incorrect, `transcribe()` accepts ndarray directly at 16kHz float32 mono. This spec uses ndarray handoff; reference doc to be corrected during implementation.

## Phase positioning

Lands as **Phase 6 — VAD streaming mode** in the overall plan (after Phase 5 hardening). Replaces single-shot recording as the default and only mode.
