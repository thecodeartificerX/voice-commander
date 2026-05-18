# Streaming Dictation Integration — Design Spec

**Date:** 2026-05-18
**Status:** Approved — ready for implementation planning
**Supersedes the transcription path of:** ADR 0086 (dictation mode), ADR 0090 (Right Ctrl session). Amends ADR 0088 (post-processing). Promotes ADR 0091 (streaming experiment) from isolated experiment to shipped code.

## Goal

Replace the shipped **batch** dictation transcription (buffer the whole utterance → one
HTTP POST to whisper.cpp `/inference` → paste) with the **streaming** WebSocket
pipeline built in ADR 0091. Each VAD utterance is streamed to `/ws/transcribe` as the
user speaks; `LocalAgreement` stabilises words across chunk boundaries; the final
transcript is post-processed and pasted. The streaming experiment proved out against
the live server; this integrates it into the daemon and deletes the batch path.

**Scope is the dictation subsystem only.** Command/verb routing (local faster-whisper),
VerbRouter, picker, elements, graph runtime, and the daemon's audio capture / VAD /
pipeline thread are reused unchanged.

## Background — the two pipelines

The shipped batch dictation pipeline (mapped from current code):

- **Entry:** bare spoken "dictate" (`VerbRouter` → synthetic `__dictation.start` step) or
  Right Ctrl (`dictation_key`). Right Ctrl with no open voice session auto-opens one
  (`_session_opened_by_dictation = True`).
- **Capture:** dictation reuses the daemon's `StreamingRecorder` + VAD. Each completed
  VAD utterance reaches `_process_utterance`; when dictation is active,
  `DictationSession.handle_utterance(audio, text)` buffers the raw audio.
- **Exit:** end-word "done" (exact match), hotkey-end (second Right Ctrl, drained via the
  `_DICTATION_WAKE` sentinel — ADR 0089), or spoken "cancel".
- **Finalize:** `_finalize_dictation` runs on the single-worker `_dictation_executor`:
  `encode_wav` the concatenated buffer → `build_prompt(vocab)` → `remote.post_audio`
  (batch POST to `/inference`, 30 s timeout) → `apply_corrections` → `apply_commands`
  → `paste_via_clipboard` → save `last.wav` + `last.txt` → publish events.

The streaming pipeline (ADR 0091, `src/voice_commander/dictation_stream/`, standalone):
`MicCapture` → `Chunker` (own VADGate) → `ws_client.stream_transcribe` (WebSocket) →
`LocalAgreement` → `TextSink`. Its value is the WebSocket transport + `LocalAgreement`
word stabilisation, **not** its mic capture — the daemon already owns the mic and VADs.

## Decisions (locked during brainstorming)

1. **Vocabulary prompt biasing is kept.** The `/ws/transcribe` server accepts an
   initial-prompt field in its config handshake. The config frame carries
   `build_prompt(vocab)` so streaming biasing matches the old batch behaviour. The exact
   field name (`prompt` vs `initial_prompt`) is verified against the live server during
   implementation.
2. **`last.txt` + vocab editor kept; `last.wav` + audio re-transcribe dropped.** Streaming
   sends audio in chunks and never assembles one WAV; keeping `last.wav` would force a
   redundant full-utterance accumulation. The `/page/dictation` web page stays (shows
   `last.txt`, hosts the vocab editor); the re-transcribe button and `POST
   /dictation/retranscribe` are removed.
3. **Fail clean — no batch fallback.** On WebSocket connect failure or mid-session drop,
   no fallback POST. The batch `/inference` path and `dictation/remote.py` are deleted.
4. **The standalone runner is removed.** `python -m voice_commander.dictation_stream` and
   its mic-owning components (`MicCapture`, `Chunker`, `StreamSession`, `sink`, `config`,
   `__main__`) are deleted. Only the reusable transport (`ws_client`, `local_agreement`,
   `bridge`) survives, folded into the `dictation` package.

## Chosen approach — Streaming `DictationSession`

Of three approaches considered:

- **A — Streaming `DictationSession` (chosen).** Keep the daemon's dictation lifecycle
  wiring; rework `DictationSession` internals so each VAD utterance is streamed
  immediately instead of buffered. Reuses the WebSocket transport, not the mic-owning
  parts of the experiment.
- **B — Daemon drives the existing `StreamSession`.** Rejected: `StreamSession` assumes it
  owns the mic and runs its own `Chunker`; the daemon's VAD already chunks. Forces a
  square peg, double VAD.
- **C — Buffer then stream at end.** Rejected: keeps buffering, only swaps the finalize
  transport — not real streaming, no progressive transcription, no latency win.

Approach A is real streaming, keeps the daemon's battle-tested dictation lifecycle, and
reuses exactly the transport worth reusing.

## Architecture

### Module layout

**Moved into `src/voice_commander/dictation/`** (now shipped dictation transport):

- `dictation_stream/ws_client.py` → `dictation/ws_client.py`
- `dictation_stream/local_agreement.py` → `dictation/local_agreement.py`
- `dictation_stream/bridge.py` → `dictation/bridge.py`

**Deleted:**

- The rest of `src/voice_commander/dictation_stream/`: `__init__.py`, `capture.py`,
  `chunker.py`, `config.py`, `session.py`, `sink.py`, `__main__.py`
- `src/voice_commander/dictation/remote.py` — batch `/inference` POST
- `pyproject.toml` `[project.scripts]` entry `voice-dictation-stream`
- `scripts/dictation_stream_e2e.py` — standalone harness

**Kept:** `encode_wav` in `dictation/store.py` — each VAD utterance is encoded to a 16 kHz
mono WAV chunk before streaming.

### `DictationSession` — streaming rework

`src/voice_commander/dictation/session.py`. No audio buffer. Owns the WebSocket transport.
Structurally the proven `StreamSession` pattern (asyncio-loop thread + `bridge.pump` +
`stream_transcribe` + lock-guarded `LocalAgreement`) minus `MicCapture`/`Chunker`,
because the daemon's VAD supplies the chunks.

Public surface:

- **`start(vocab: Vocabulary) -> None`** — the daemon loads `vocab.json` and passes the
  snapshot in. The session: builds `prompt = build_prompt(vocab)`; stores the vocab
  snapshot for finalize-time corrections/commands (one consistent snapshot per
  dictation); spawns one asyncio-loop thread running `bridge.pump` (sync chunk queue →
  async) + `ws_client.stream_transcribe`, whose config frame carries `language` +
  `prompt`; routes partials to `LocalAgreement.commit`, accumulating confirmed words
  under a lock; publishes `dictation.start`.
- **`handle_utterance(audio, text) -> "end" | "cancel" | "buffered"`** — unchanged
  classification (exact end-word, exact cancel-word with collision guard, else default).
  On `"buffered"`: `encode_wav(audio)` and push the WAV chunk onto the sync chunk
  queue — streamed immediately. End-word / cancel-word audio is not streamed.
- **`finish() -> str`** (replaces `take_and_finish`) — push the `None` end sentinel onto
  the chunk queue, join the asyncio-loop thread (bounded timeout = `idle_timeout_s` +
  margin), `LocalAgreement.finalize()`, return the raw stabilised transcript. Publishes
  `dictation.end {reason: "done"}`. Returns `""` if the WebSocket never connected or
  produced nothing.
- **`cancel() -> None`** — close the WebSocket, discard, publish
  `dictation.end {reason: "cancel"}`.
- **`request_end()` / `pending_end` / `active`** — unchanged; drive the hotkey-end drain.

Threading: the confirmed-word accumulator + `LocalAgreement` are touched by the
asyncio-loop thread (`on_partial`) and by `finish()` (the `_dictation_executor` thread).
A single lock serialises them — the ADR 0091 Task-9 pattern, carried over verbatim.

Connect-failure handling: `start()` only spawns the thread and returns; the WebSocket
connect happens inside the asyncio thread. A connect failure is recorded on the session;
`finish()` then returns `""` with the error surfaced so the daemon emits
`dictation.error`.

### Daemon `_finalize_dictation` — shrunk

Runs on `_dictation_executor` (single-worker, FIFO — unchanged thread model). Becomes:

1. `text = session.finish()`
2. `apply_corrections(text, vocab.corrections)` then `apply_commands(text, vocab.commands)`
   — `vocab` is the session's start-time snapshot.
3. `paste_via_clipboard(text)`
4. `DictationStore.save_text(text)` → `last.txt`
5. publish `transcript {text, confidence: 1.0}` then `dictation.result {text}`

`DictationSession` owns the transport and produces a raw stabilised transcript; the
daemon owns the executor-thread finalize (post-processing, paste, persistence, events).

### Untouched daemon wiring

`on_dictation_toggle`, the bare-`dictate` handler, the `_process_utterance` dictation
branch, the `_DICTATION_WAKE` sentinel, `_finalize_pending_dictation_end`,
`_end_owned_session_if_needed`, and the close-before-finalize ordering all remain. They
call the new session methods; the lifecycle plumbing is identical. The only daemon
constructor change: `dictation_endpoint: str` → `dictation_ws_url` + `dictation_language`
+ `dictation_idle_timeout_s`.

### Data flow per dictation

```
"dictate" / Right Ctrl
    → daemon loads vocab.json → DictationSession.start(vocab)
        → build_prompt(vocab), open WS (config frame: language + prompt)
each VAD utterance (daemon pipeline thread)
    → handle_utterance → "buffered" → encode_wav → chunk queue
        → bridge.pump → stream_transcribe → server "partial"
        → LocalAgreement.commit → confirmed words accumulate (locked)
"done" / second Right Ctrl drain / —
    → _finalize_dictation on _dictation_executor:
        session.finish(): END sentinel, join asyncio thread, LocalAgreement.finalize()
        → apply_corrections → apply_commands → paste_via_clipboard
        → DictationStore.save_text(last.txt)
        → publish transcript + dictation.result
"cancel"
    → session.cancel(): close WS, discard, dictation.end{reason:"cancel"}
```

## Config

`config.toml` / `config.toml.example` — the `[dictation]` section:

```toml
[dictation]
ws_url               = "ws://192.168.4.200:8765/ws/transcribe"
language             = "en"
end_word             = "done"
cancel_word          = "cancel"
idle_timeout_seconds = 30
```

- `endpoint` (batch `/inference` URL) → `ws_url`.
- New keys: `language`, `idle_timeout_seconds`.
- `end_word`, `cancel_word` unchanged.
- The `[dictation_stream]` section is deleted entirely (folded into `[dictation]`).

`DictationConfig` dataclass (`config.py`): drop `endpoint`; add `ws_url`, `language`,
`idle_timeout_seconds`; keep `end_word`, `cancel_word`. `[hotkey] dictation_key` is
unchanged (Right Ctrl still toggles dictation).

## Web UI

- `/page/dictation` — kept. Shows `last.txt` + the vocab editor. The `page_dictation`
  handler drops `store.read_audio` and the audio-tied token-budget display; keeps
  `read_text` and the vocab load.
- `POST /dictation/retranscribe` — deleted (no `last.wav` to re-POST).
- `POST /dictation/vocab` — kept unchanged (vocab editor persists `vocab.json`).
- Templates: `page_dictation.html` loses the re-transcribe button and its result target;
  `_dictation_result.html` is deleted (only the retranscribe flow used it).
- `DictationStore` (`dictation/store.py`): drop `save_audio` / `read_audio` /
  `audio_path`; keep `save_text` / `read_text` / `text_path`.

## Error handling

Events and chimes match the batch path's behaviour exactly.

| Failure | Behaviour |
|---|---|
| WebSocket connect fails at `start()` | asyncio thread records the error; `finish()` returns `""`; daemon emits `dictation.error {reason:"endpoint"}` + miss chime |
| WebSocket drops mid-session | `stream_transcribe` returns early; `LocalAgreement` keeps already-confirmed words; `finish()` pastes those and logs the lost tail. Nothing confirmed → treated as an endpoint error |
| `encode_wav` fails on a chunk | `dictation.error {reason:"encode"}` + miss chime |
| `paste_via_clipboard` fails | `dictation.error {reason:"clipboard"}` + miss chime |
| spoken cancel | discard, `dictation.end {reason:"cancel"}`, no chime (unchanged) |

Events preserved: `dictation.start`, `dictation.end {reason}`,
`dictation.error {reason}`, `dictation.result`, `transcript`. Sprite cancelled-cue and
HUD line ordering are unchanged.

## Testing

- `ws_client` / `local_agreement` / `bridge` unit + integration tests move with the
  files; imports updated; stay green.
- Delete tests for deleted modules: `test_stream_capture`, `test_stream_chunker`,
  `test_stream_config`, `test_stream_sink`, `test_stream_toggle`, `test_stream_session`.
- Rewrite dictation integration tests (`test_dictation_pipeline`,
  `test_dictation_cancel`, `test_dictation_opens_session`,
  `test_dictation_vocab_pipeline`): replace the mock `/inference` endpoint with an
  in-process mock WebSocket server (the ADR 0091 Task-6 `websockets.asyncio.server.serve`
  pattern).
- `test_web_dictation` — drop the retranscribe assertions.
- New integration test: streaming `DictationSession` against a mock WebSocket server —
  assert chunk-streamed → stabilised → corrections/commands → paste.
- New E2E harness `scripts/dictation_streaming_e2e.py` — real mic via the daemon, real
  `/ws/transcribe` server, human-validated. The old `dictation_stream_e2e.py` is deleted.
- The full suite stays green; command mode, picker, elements, and graph runtime are
  untouched and must not regress.

## Regression safety

The change is confined to: the `dictation/` subsystem, the `[dictation]` config section,
the `/dictation` web routes, and the daemon's dictation branch (`_process_utterance`
dictation path, `_finalize_dictation`, `on_dictation_toggle`, the daemon constructor's
dictation params). The daemon's audio capture, VAD, pipeline thread, VerbRouter,
Dispatcher, picker, elements, and graph runtime are reused unchanged. Delivery is phased:
each phase's tests pass green before the next begins.

## Documentation

- New ADR: streaming dictation integration + batch-path removal. It supersedes the
  transcription path of ADR 0086, amends ADR 0088 (post-processing now applies to the
  streamed transcript), supersedes ADR 0090's transport, and promotes ADR 0091 from
  isolated experiment to shipped code.
- Refresh: the `CLAUDE.md` **Current state** section, `docs/agents/technical-decisions.md`,
  `docs/libraries.md` (the `websockets` entry is no longer "experiment"), and
  `docs/dictation-streaming.md` (now describes the shipped path).
- `config.toml.example` updated in the same change as the config code.
