# Streaming Dictation — Design Spec

- **Date:** 2026-05-18
- **Status:** Experimental (containerized — does not touch shipped dictation)
- **Branch:** `experiment/streaming-dictation`
- **Supersedes (if it ships):** the batch transcription path of ADR 0086 / 0090

## 1. Purpose

Shipped dictation (ADR 0086, amended 0090) is **batch**: VAD buffers the entire
utterance, the daemon encodes one WAV, POSTs it to the remote whisper.cpp
`/inference` endpoint, and pastes the result once at the end. Latency scales with
how long the user speaks; there is no progressive transcription and no
cross-chunk context.

This experiment builds a **streaming** dictation pipeline as a fully isolated,
self-contained module. Audio is segmented into chunks while the user is still
speaking, each chunk is streamed to a WebSocket endpoint that carries
`initial_prompt` context from chunk to chunk, and a **LocalAgreement** stabiliser
holds back the unstable suffix of each chunk's transcript until the next chunk
confirms it.

The experiment runs **standalone** — its own entrypoint, its own hotkey, zero
edits to `daemon.py` or any shipped dictation code. If it proves out, a later,
separate migration will replace the batch transcription path. That migration is
**out of scope** for this spec.

## 2. Goals / Non-goals

**Goals**
- A self-contained `dictation_stream` module, runnable via `python -m voice_commander.dictation_stream`.
- Hybrid chunking: segment on silence, with a hard time cap so the buffer never grows unbounded.
- WebSocket streaming to the **confirmed-live** `ws://192.168.4.200:8765/ws/transcribe` endpoint.
- LocalAgreement word stabilisation across chunk boundaries.
- Output: accumulate confirmed text, run it through a single `transform(text) -> text` hook, paste at session end via a clipboard round-trip.
- Zero edits to shipped dictation (`src/voice_commander/dictation/`, `daemon.py`).

**Non-goals**
- The future LLM rewrite step. The `transform` hook is identity for now; the LLM drops in behind it later. Not built here.
- Wiring the streaming pipeline into the daemon / replacing the batch path. Separate future work.
- Live progressive output into the target app. Output is accumulate-then-paste-at-end (per the LLM plan: the accumulated text is the LLM's input).
- Re-transcribe / web UI / vocab editor parity. The experiment is a CLI runner only.

## 3. Confirmed decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Code-isolated branch, new module, shipped dictation untouched. | Shipped dictation is tested and in use; the experiment must not regress it. |
| D2 | WebSocket client built straight against the doc protocol. | User confirmed `/ws/transcribe` is live and the protocol is exact. |
| D3 | Output = accumulate → `transform()` hook → paste at end. | A small-LLM rewrite step lands behind the hook later; identity for now. |
| D4 | Runs standalone first; replaces the batch path only if it proves out. | Experiment, not a migration. |
| D5 | Chunker **reuses `VADGate`** with dictation-tuned params. | `VADGate` is already the silence + max-cap state machine on the torch-free ONNX model. Approaches B/C duplicate it; B also risks pulling `torch`. |
| D6 | Standalone stop trigger = **Right Ctrl** hotkey (pynput), toggle. | Matches shipped dictation's `dictation_key`; closest to the eventual replacement UX. |

## 4. Architecture

New package `src/voice_commander/dictation_stream/`:

| File | Responsibility |
|------|----------------|
| `__main__.py` | Standalone entrypoint. Binds Right Ctrl (pynput). First press starts a session, second press ends it. Loops; Ctrl-C exits the process. |
| `config.py` | `StreamDictationConfig` frozen dataclass; loads the `[dictation_stream]` config section. |
| `capture.py` | Opens a `sounddevice.InputStream` at device-native rate; PortAudio callback pushes raw frames to a queue; a worker thread resamples to 16 kHz (reusing `Resampler`) and slices into 512-sample frames. |
| `chunker.py` | Drives `VADGate` with dictation-tuned params; on each emitted utterance **or** the 15 s hard cap, WAV-encodes the chunk (reusing `dictation.store.encode_wav`); discards chunks shorter than `min_chunk_seconds`. |
| `local_agreement.py` | Pure stabiliser. `LocalAgreement.commit(text) -> list[str]` and `.finalize() -> list[str]`. No I/O. |
| `ws_client.py` | Async `websockets` client. Sends the `config` handshake, streams binary WAV chunks, receives `partial` / `error` JSON frames. |
| `bridge.py` | Bridges the synchronous chunk `queue.Queue` to an `asyncio.Queue` via `loop.run_in_executor`. |
| `sink.py` | Accumulates committed words; on session end applies `transform(text) -> text` (identity now) and pastes via `dictation.clipboard.paste_via_clipboard`. |
| `session.py` | Orchestrator. Owns the thread + asyncio lifecycle; wires capture + chunker (threads) → bridge → ws_client → local_agreement → sink. |

**Reused without forking:** `VADGate` + `SileroVADOnnx` (`vad_gate.py`, `vad_onnx.py`),
`Resampler` (`resampler.py`), `dictation.store.encode_wav`, `dictation.clipboard.paste_via_clipboard`.

## 5. Data flow

```
mic
 └─ sounddevice InputStream callback ──▶ raw_q (native-rate float32 frames)
       └─ capture worker thread: Resampler (soxr → 16 kHz) + slice into 512-sample frames ──▶ frame_q
             └─ chunker worker thread: VADGate.process(frame)
                   • on emitted utterance  → chunk
                   • on 15 s hard cap      → force-flush partial buffer as a chunk
                   • drop chunk if  < min_chunk_seconds
                   • encode_wav(chunk) ──▶ chunk_q  (sync queue.Queue)
                         └─ bridge: run_in_executor(chunk_q.get) ──▶ async_chunk_q (asyncio.Queue)
                               └─ ws_session coroutine:
                                     send {"type":"config","language":...}  (once, at start)
                                     loop:  send <wav bytes>  →  recv JSON
                                            • "partial" → LocalAgreement.commit(text) → sink.accumulate(words)
                                            • "error"   → log detail, stop
                                     on stop: send {"type":"end"} → LocalAgreement.finalize() → sink.accumulate(tail)
                         sink.flush(): transform(full_text) → clipboard paste
```

**Threads:** PortAudio callback, capture worker, chunker worker, and the asyncio
loop (bridge + ws_session coroutines) on the main thread. The pynput hotkey
listener runs on its own thread.

**Session lifecycle:** first Right Ctrl press → `session.start()` opens the
stream and spawns workers + asyncio loop. Second Right Ctrl press (or 30 s idle,
or Ctrl-C) → `session.stop()` puts a `None` sentinel down the chunk path, sends
`{"type":"end"}`, finalizes LocalAgreement, flushes the sink, joins workers.

## 6. Hybrid chunking — `VADGate` parameters

`VADGate` already emits one utterance per detected speech-end and force-caps an
unbroken utterance at `max_utterance_ms`. That is exactly hybrid chunking. The
experiment instantiates `VADGate` with dictation-tuned values (distinct from the
command-tuned daemon defaults):

| Param | Daemon default | Streaming dictation | Why |
|-------|---------------|---------------------|-----|
| `threshold` | 0.4 | 0.5 | Fewer false speech triggers in continuous prose. |
| `min_silence_duration_ms` | 250 | 1200 | 250 ms breaks sentences on breath pauses; 1200 ms lets natural mid-sentence pauses through. |
| `speech_pad_ms` | 30 | 300 | Word starts matter more in dictation; avoid clipping onsets. |
| `max_utterance_ms` | 8000 | 15000 | Hard time cap — bounds latency during unbroken speech. |

`min_chunk_seconds` (default 1 s) is enforced **in the chunker after `VADGate`
emits** — chunks shorter than this are discarded as too short for Whisper.

If `VADGate`'s constructor does not expose all four params directly, the chunker
constructs the underlying `VadConfig` (or equivalent) — confirmed during the
research phase against the real `vad_gate.py` signature.

## 7. LocalAgreement

Pure module, no I/O — the most heavily unit-tested unit.

State:
- `hypothesis: deque[str]` — words seen but not yet confirmed.
- `committed: list[str]` — words confirmed stable since the last drain.

`commit(new_text: str) -> list[str]`:
1. Tokenise `new_text` on whitespace.
2. Find the longest suffix of `hypothesis` equal to a prefix of `new_words`.
3. Words in `hypothesis` **before** that overlap are stable → move them to `committed`.
4. Replace `hypothesis` with the overlap tail followed by the rest of `new_words`.
5. Return and clear `committed`.

`finalize() -> list[str]`: flush all remaining `hypothesis` words and return them
— the held-back tail is never lost.

Worked example (from the design doc):

```
chunk 1 "The quick brown fox"   → committed []                hypothesis [The quick brown fox]
chunk 2 "brown fox jumps over"  → committed [The, quick]       hypothesis [brown fox jumps over]
chunk 3 "jumps over the lazy"   → committed [brown, fox]       hypothesis [jumps over the lazy]
finalize()                      → committed [jumps over the lazy]
```

## 8. WebSocket protocol

Endpoint: `ws://192.168.4.200:8765/ws/transcribe` (confirmed live).

| Direction | Frame | Payload |
|-----------|-------|---------|
| client → server | text | `{"type":"config","language":"en"}` — sent once, immediately after connect. |
| client → server | binary | A self-contained WAV file (header + 16 kHz mono int16 PCM) per chunk. |
| client → server | text | `{"type":"end"}` — sent on session stop. |
| server → client | text | `{"type":"partial","text":"..."}` — fed to LocalAgreement. |
| server → client | text | `{"type":"error","detail":"..."}` — logged; session stops. |

The server carries `initial_prompt` from chunk to chunk itself — the client sends
no prompt and does no client-side context management.

The **exact** field names and frame types are re-verified against the live
endpoint during the research phase before `ws_client.py` is written; any drift is
reconciled then.

## 9. Configuration

New `[dictation_stream]` section — read **only** by `StreamDictationConfig`,
never by the shipped daemon. The shipped `[dictation]` section is untouched.

```toml
[dictation_stream]
ws_url                  = "ws://192.168.4.200:8765/ws/transcribe"
language                = "en"
vad_threshold           = 0.5
min_silence_duration_ms = 1200
speech_pad_ms           = 300
max_chunk_seconds       = 15
min_chunk_seconds       = 1
idle_timeout_seconds    = 30
```

Added to `config.toml.example` with an inline comment per key.

## 10. Error handling

| Failure | Behaviour |
|---------|-----------|
| Microphone open fails | Print device error, exit non-zero before any session starts. |
| WebSocket connect fails | Log, no paste, abort the session cleanly. |
| Server `error` frame | Log `detail`, stop the session, still call `finalize()` so already-confirmed words are not lost. |
| 30 s with no chunk | Treat as end-of-session: send `{"type":"end"}`, finalize, paste. |
| Chunk shorter than `min_chunk_seconds` | Discarded silently by the chunker. |
| Ctrl-C | Clean shutdown: stop session, finalize, paste, join threads, exit 0. |

`finalize()` always flushes `hypothesis`, so the held-back tail is delivered on
every termination path.

## 11. Dependencies

- **New:** `websockets >= 14.0` — added to `pyproject.toml` and `docs/libraries.md`.
- **Already present:** `silero-vad`, `onnxruntime`, `soxr`, `sounddevice`, `numpy`, `pynput`.
- **Not added:** `torch`, `torchaudio`, `scipy` (WAV encoding reuses `dictation.store.encode_wav`, which is `soundfile`-based).

## 12. Testing

Per the project's four-layer pyramid.

**Unit**
- `test_stream_local_agreement.py` — overlap algorithm: no overlap, partial overlap, full overlap, empty input, repeated finalize, single-word chunks.
- `test_stream_chunker.py` — synthetic frames: assert a chunk emits on silence, a chunk force-flushes at the 15 s cap, sub-`min_chunk_seconds` chunks are dropped.
- `test_stream_config.py` — `[dictation_stream]` parsing, defaults, missing-section fallback.

**Integration**
- `test_stream_ws_client.py` — against an in-process mock `websockets.serve` that replays canned `partial` frames; assert handshake, binary frames, `end`.
- `test_stream_session.py` — full pipeline with the mock WS server + synthetic audio; assert the final clipboard text equals the expected transcript.

**E2E**
- `scripts/dictation_stream_e2e.py` — real microphone + the real `192.168.4.200:8765/ws/transcribe` server. Operator speaks a scripted paragraph; the harness asserts on the resulting clipboard contents. Human-validated per `docs/agents/visual-e2e-testing.md`.

## 13. Documentation deliverables

Updated in the same change as the code:
- ADR in `docs/decisions/` (status *Experimental*) describing the streaming pipeline; row added to `docs/agents/technical-decisions.md`.
- `websockets` API reference vendored into `docs/references/`, cited from `ws_client.py`.
- `docs/libraries.md` — new `websockets` row with rationale.
- `config.toml.example` — `[dictation_stream]` section with per-key comments.
- A short overview doc under `docs/` for the new subsystem, linked from `docs/index.md`.

## 14. Research-first checkpoints (before any code)

Per `CLAUDE.md`:
1. Vendor `websockets >= 14.0` API docs (`connect`, binary vs text `send`, `recv`, `close`, exception types) into `docs/references/`.
2. Probe the live `/ws/transcribe` endpoint to confirm the exact frame contract in §8.
3. Read the real `vad_gate.py` constructor signature to confirm how the four dictation params in §6 are passed.

## 15. Open risks

- **PyPI `silero-vad` / torch:** Mitigated by D5 — the experiment reuses `VADGate`/`SileroVADOnnx`, so no new VAD dependency and no torch risk.
- **Protocol drift:** the doc's protocol is reconciled against the live endpoint in research checkpoint 2 before the client is written.
- **WASAPI 16 kHz capture:** capture stays at device-native rate + `soxr` resample (the proven daemon path), not a direct 16 kHz `InputStream`.
