# ADR 0092 — Streaming Dictation Integration (Batch Path Removed)

**Status: SUPERSEDED by [ADR 0096](0096-server-side-dictation.md)** — the streaming-dictation transport, ws_client, and DictationSession have been rewritten; per-chunk WAV streaming + partial-frame reassembly are gone. Some lifecycle decisions (close-before-finalize ordering, hotkey-end drain) remain valid and are reaffirmed by 0096.

**Status:** Accepted
**Date:** 2026-05-18
**Supersedes (transcription path):** [ADR 0086](0086-dictation-mode.md), [ADR 0090](0090-dictation-hotkey-opens-session.md) (transport only; lifecycle wiring unchanged)
**Amends:** [ADR 0088](0088-dictation-postprocessing.md) — post-processing now applies to the streamed transcript, not a batch POST response
**Promotes:** [ADR 0091](0091-streaming-dictation-experiment.md) from isolated experiment to shipped code

## Context

Shipped dictation (ADR 0086, amended ADR 0090) is batch: the daemon buffers the
entire utterance, POSTs one WAV to the whisper.cpp `/inference` endpoint, and pastes the
result once. Perceived latency scales with utterance length; there is no cross-chunk
decoding context.

ADR 0091 validated a streaming WebSocket pipeline (`/ws/transcribe`) with `LocalAgreement`
word stabilisation as a standalone experiment. This ADR integrates that pipeline into the
daemon and deletes the batch path. Scope is the dictation subsystem only; command routing,
VerbRouter, picker, elements, graph runtime, and audio capture/VAD are reused unchanged.

## Decision

### D1 — Approach A: Streaming `DictationSession` (chosen)

Three approaches were considered:

- **A — Streaming `DictationSession` (chosen).** Keep the daemon's dictation lifecycle
  wiring; rework `DictationSession` internals so each VAD utterance is streamed immediately
  instead of buffered. Reuses the WebSocket transport from ADR 0091, not its mic-owning
  parts.
- **B — Daemon drives the existing `StreamSession`.** Rejected: `StreamSession` owns the
  mic and runs its own `Chunker`; the daemon's VAD already chunks. Forced double VAD.
- **C — Buffer then stream at end.** Rejected: retains full buffering, only swaps the
  finalize transport — not real streaming, no progressive transcription, no latency win.

Approach A is real streaming, keeps the battle-tested dictation lifecycle, and reuses
exactly the transport worth reusing.

### D2 — Module changes

**Moved into `src/voice_commander/dictation/`** (promoted from experiment):

- `dictation_stream/ws_client.py` → `dictation/ws_client.py`
- `dictation_stream/local_agreement.py` → `dictation/local_agreement.py`
- `dictation_stream/bridge.py` → `dictation/bridge.py`

**Deleted:**

- Remaining `src/voice_commander/dictation_stream/` modules: `__init__.py`, `capture.py`,
  `chunker.py`, `config.py`, `session.py`, `sink.py`, `__main__.py`
- `src/voice_commander/dictation/remote.py` — batch `/inference` POST
- `pyproject.toml` script entry `voice-dictation-stream`
- `scripts/dictation_stream_e2e.py` — standalone experiment harness

**Kept:** `encode_wav` in `dictation/store.py` — each VAD utterance is encoded to a 16 kHz
mono WAV chunk before streaming.

### D3 — `DictationSession` streaming rework

`src/voice_commander/dictation/session.py` is reworked to own the WebSocket transport.
No audio buffer. Structurally mirrors the proven `StreamSession` pattern (asyncio-loop
thread + `bridge.pump` + `stream_transcribe` + lock-guarded `LocalAgreement`) minus
`MicCapture`/`Chunker` (the daemon's VAD supplies the chunks).

Public surface:

- **`start(vocab: Vocabulary) -> None`** — loads vocab, builds `prompt = build_prompt(vocab)`,
  stores the vocab snapshot for finalize-time corrections/commands (one consistent snapshot
  per dictation), spawns the asyncio-loop thread running `bridge.pump` +
  `ws_client.stream_transcribe`. The WebSocket config frame carries `language` and
  `initial_prompt` (the confirmed field name — verified against the live `/ws/transcribe`
  server; matches ADR 0091 D3 and the design spec; the provisional name was confirmed and
  kept). Routes partials to `LocalAgreement.commit`, accumulating confirmed words under a
  lock. Publishes `dictation.start`.
- **`handle_utterance(audio, text) -> "end" | "cancel" | "buffered"`** — unchanged
  classification. On `"buffered"`: `encode_wav(audio)` and push the WAV chunk onto the
  sync chunk queue — streamed immediately. End-word / cancel-word audio is not streamed.
- **`finish() -> str`** (replaces `take_and_finish`) — pushes the `None` end sentinel,
  joins the asyncio-loop thread (bounded by `idle_timeout_s` + margin),
  `LocalAgreement.finalize()`, returns the raw stabilised transcript. Publishes
  `dictation.end {reason:"done"}`. Returns `""` if the WebSocket never connected or
  produced nothing.
- **`cancel() -> None`** — closes the WebSocket, discards, publishes
  `dictation.end {reason:"cancel"}`.
- **`request_end()` / `pending_end` / `active`** — unchanged; drive the hotkey-end drain.

The confirmed-word accumulator and `LocalAgreement` are touched by the asyncio-loop thread
(`on_partial`) and by `finish()` (on the `_dictation_executor` thread). A single lock
serialises them — the ADR 0091 Task-9 pattern carried over verbatim.

Connect-failure handling: `start()` only spawns the thread; the WebSocket connect happens
inside the asyncio thread. A connect failure is recorded on the session; `finish()` returns
`""` and the daemon emits `dictation.error`.

### D4 — Daemon `_finalize_dictation` shrunk

Runs on `_dictation_executor` (single-worker, FIFO — unchanged thread model). Shrinks to:

1. `text = session.finish()`
2. `apply_corrections(text, vocab.corrections)` then `apply_commands(text, vocab.commands)`
3. `paste_via_clipboard(text)`
4. `DictationStore.save_text(text)` → `last.txt`
5. publish `transcript {text, confidence:1.0}` then `dictation.result {text}`

`_finalize_dictation` now takes no arguments; `session.finish()` owns the transport and
produces the raw stabilised transcript.

### D5 — Lifecycle wiring unchanged

The daemon's dictation lifecycle — `on_dictation_toggle`, bare-`dictate` handler,
`_process_utterance` dictation branch, `_DICTATION_WAKE` sentinel,
`_finalize_pending_dictation_end`, `_end_owned_session_if_needed`, and close-before-finalize
ordering (ADR 0090 D5) — is unchanged. These wrappers call the new session methods; the
plumbing is identical.

One hardening applied during implementation: `_close_voice_session` gains a
`cancel_dictation` parameter (default `True`). On the close-before-finalize path the owned
voice session is closed with `cancel_dictation=False`, so `_finalize_dictation` can still
call `session.finish()` to obtain the streamed transcript — the session is not cancelled
prematurely. Caller context: scroll-lock close passes the default (`True`); the
`_end_owned_session_if_needed` close-before-finalize path passes `cancel_dictation=False`.

### D6 — Config changes

`config.toml` / `config.toml.example` — `[dictation]` section:

```toml
[dictation]
ws_url               = "ws://192.168.4.200:8765/ws/transcribe"
language             = "en"
end_word             = "done"
cancel_word          = "cancel"
idle_timeout_seconds = 30
```

- `endpoint` (batch HTTP URL) → `ws_url`.
- New keys: `language`, `idle_timeout_seconds`.
- `end_word`, `cancel_word` — unchanged.
- `[dictation_stream]` section deleted. A stale `[dictation_stream]` table in a user's
  existing `config.toml` is harmless — the TOML loader ignores unknown tables.

`DictationConfig` dataclass: drops `endpoint`; adds `ws_url`, `language`,
`idle_timeout_seconds`; keeps `end_word`, `cancel_word`.

### D7 — Web UI changes

- `/page/dictation` — kept. Shows `last.txt` + vocab editor. Handler drops
  `store.read_audio` and the audio-tied token-budget display; keeps `read_text` and vocab
  load.
- `POST /dictation/retranscribe` — deleted (no `last.wav` to re-POST).
- `POST /dictation/vocab` — kept unchanged.
- `_dictation_result.html` partial — deleted (only the retranscribe flow used it).
- `DictationStore`: drops `save_audio` / `read_audio` / `audio_path`; keeps `save_text` /
  `read_text` / `text_path`.

### D8 — Error handling

Events and chimes match the batch path exactly.

| Failure | Behaviour |
|---|---|
| WebSocket connect fails at `start()` | asyncio thread records the error; `finish()` returns `""`; daemon emits `dictation.error {reason:"endpoint"}` + miss chime |
| WebSocket drops mid-session | `stream_transcribe` returns early; `LocalAgreement` keeps already-confirmed words; `finish()` pastes those and logs the lost tail. Nothing confirmed → treated as an endpoint error |
| `encode_wav` fails on a chunk | `dictation.error {reason:"encode"}` + miss chime |
| `paste_via_clipboard` fails | `dictation.error {reason:"clipboard"}` + miss chime |
| spoken cancel | discard, `dictation.end {reason:"cancel"}`, no chime |

Preserved events: `dictation.start`, `dictation.end {reason}`, `dictation.error {reason}`,
`dictation.result`, `transcript`. Sprite cancelled-cue and HUD line ordering are unchanged.

## Consequences

### Positive

- Real progressive transcription: words are committed as the user speaks, not after a
  full-utterance POST.
- Lower perceived latency on long dictations; cross-chunk decoding context via the server's
  `initial_prompt` carry.
- Unified dictation code path — the ADR 0091 experiment branch is deleted.
- Daemon lifecycle plumbing is battle-tested and untouched.

### Negative

- No batch fallback: a WebSocket connect failure or mid-session drop emits a miss chime
  and discards (or pastes only already-confirmed words). The old batch path was always
  available; now the user must have a reachable `/ws/transcribe` server.
- `last.wav` and the re-transcribe web UI are removed; audio is not reassembled after
  streaming.

### Neutral

- `websockets` dependency promoted from experiment to production (no new dep added — it was
  already in `uv.lock` from ADR 0091).
- Thread model unchanged: the asyncio-loop thread and `_dictation_executor` single-worker
  carry over from the experiment.

## References

- [ADR 0086](0086-dictation-mode.md) — shipped batch dictation (transcription path superseded)
- [ADR 0088](0088-dictation-postprocessing.md) — post-processing (now applied to streamed transcript)
- [ADR 0089](0089-dictation-hotkey-sentinel-cancel-debounce.md) — hotkey-end sentinel + spoken cancel
- [ADR 0090](0090-dictation-hotkey-opens-session.md) — Right Ctrl opens session (transport superseded; lifecycle unchanged)
- [ADR 0091](0091-streaming-dictation-experiment.md) — streaming experiment (promoted)
- `src/voice_commander/dictation/session.py` — streaming `DictationSession`
- `src/voice_commander/dictation/ws_client.py`, `local_agreement.py`, `bridge.py` — promoted transport
- `docs/references/websockets.md` — WebSocket library reference
