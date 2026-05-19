# Streaming Dictation

Dictation is a voice-session sub-state. Saying bare "dictate" or pressing Right
Ctrl (`dictation_key`, default `ctrl_r`) enters dictation; the daemon's VAD
segments speech into utterances, and each utterance is streamed immediately to
a whisper WebSocket server. Words are stabilised progressively by `LocalAgreement`
as partials arrive. When dictation ends the stabilised transcript is
post-processed and pasted at the cursor.

**ADR:** [0092 — Streaming Dictation Integration](decisions/0092-streaming-dictation-integration.md)
(supersedes the batch POST transport of ADR 0086/0090; promotes the experiment
from ADR 0091). [ADR 0093](decisions/0093-transcription-proxy-endpoint.md) moved
the endpoint to an LLM-cleanup proxy — see [Transcription proxy](#transcription-proxy) below.
[ADR 0094](decisions/0094-consume-done-frame.md) corrects ADR 0093's "zero code change" claim:
the daemon now consumes the proxy's `done` frame to receive the LLM-cleaned transcript.

## Transcription proxy

`[dictation] ws_url` points at a **WebSocket proxy** (`ws://192.168.4.200:8767/ws/transcribe`,
ADR 0093), not the raw whisper server. The proxy is protocol-compatible — same
config frame, audio-chunk frames, and `partial` / `done` / `error` replies.

- **Partial frames pass through unchanged** → `LocalAgreement` stabilisation and
  the live HUD transcript feedback work exactly as before.
- **The final `done` transcript is LLM-cleaned** by the proxy (punctuation,
  casing, disfluency removal). After sending `{"type":"end"}`, the daemon now
  **reads the `done` frame** (`stream_transcribe` loops on `ws.recv()` until a
  `{"type":"done","text":"..."}` frame arrives, bounded by `done_timeout_s=15 s`).
  `DictationSession.finish()` returns the `done.text` value as the transcript
  (ADR 0094).
- If no `done` frame arrives (timeout, connection closed, or server error),
  `finish()` **falls back to the `LocalAgreement`-stabilised transcript** built
  from the accumulated `partial` frames — the pre-0093 behaviour, preserving
  graceful degradation.
- If the LLM is temporarily unavailable, the proxy **silently returns raw whisper
  text in the `done` frame** — dictation still completes with the raw text.

The cleanup is entirely server-side; the daemon stays local-first and contacts
only the proxy.

---

## Flow

```
daemon VAD utterance (ndarray)
    │
    ▼ handle_utterance(audio, text)
DictationSession
    │  encode_wav(audio) → WAV chunk
    │  push to sync chunk_q
    │
    ▼ (asyncio-loop thread)
bridge.pump(chunk_q, ws)          ← drains sync queue into async ws.send()
    │
    ▼
ws_client.stream_transcribe(ws)   ← receives partial transcripts from server
    │  on_partial(text)
    ▼
LocalAgreement.commit(words)      ← stabilises words across chunk boundaries
    │  (lock-guarded accumulator)
    │
    ▼ finish() — end-word or hotkey-end
LocalAgreement.finalize()         ← returns confirmed + tail words
    │
    ▼ _finalize_dictation (daemon, _dictation_executor thread)
apply_corrections → apply_commands → paste_via_clipboard → DictationStore.save_text
```

---

## Architecture

### `DictationSession` (`src/voice_commander/dictation/session.py`)

Owns the WebSocket transport. On `start(vocab)`:

1. Loads `vocab.json`, builds `initial_prompt = build_prompt(vocab)`.
2. Spawns an asyncio-loop thread that runs `bridge.pump` +
   `ws_client.stream_transcribe` concurrently as tasks.
3. Sends the config frame `{language, initial_prompt}` to `/ws/transcribe`.
4. Routes each partial transcript to `LocalAgreement.commit` under a lock.
5. Publishes `dictation.start`.

On `handle_utterance(audio, text) -> "end" | "cancel" | "buffered"`:

- Classification unchanged (end-word / cancel-word / accumulate).
- On `"buffered"`: `encode_wav(audio)` → push WAV chunk onto the sync chunk
  queue; the asyncio-loop drains it via `bridge.pump`.
- End-word and cancel-word audio are not streamed.

On `finish() -> str` (normal exit):

- Pushes the `None` end sentinel to the chunk queue.
- Joins the asyncio-loop thread (bounded by `idle_timeout_seconds` + margin).
  During that thread, `stream_transcribe` sends `{"type":"end"}` then reads the
  proxy's `done` frame, storing `done.text` in `_final_text` (ADR 0094).
- Calls `LocalAgreement.finalize()` to flush the confirmed-word accumulator
  (always, for the fallback path).
- Returns `_final_text` (the proxy's LLM-cleaned `done.text`) when not `None`.
  Falls back to the `LocalAgreement`-stabilised transcript when `_final_text is
  None` (no `done` frame received — timeout / closed / error).
- Publishes `dictation.end {reason:"done"}`.
- Returns `""` if the WebSocket never connected or produced no output.

On `cancel()`:

- Closes the WebSocket, discards the buffer.
- Publishes `dictation.end {reason:"cancel"}`.

`request_end()`, `pending_end`, and `active` are unchanged — they drive the
hotkey-end drain path (ADR 0089).

### Supporting modules (`src/voice_commander/dictation/`)

| Module | Responsibility |
|--------|----------------|
| `ws_client.py` | `stream_transcribe` — async WebSocket client; sends chunks, receives partials, reads `done` frame (ADR 0094) |
| `bridge.py` | `pump` — sync-to-async queue bridge (runs as asyncio task) |
| `local_agreement.py` | `LocalAgreement` — word stabiliser across chunk boundaries |
| `store.py` | `encode_wav`, `DictationStore.save_text` / `read_text` |
| `postprocess.py` | `apply_corrections`, `apply_commands`, `build_prompt` |

### Daemon `_finalize_dictation`

Runs on the `_dictation_executor` single-worker thread (FIFO, unchanged from
ADR 0090). Reduced to five steps:

1. `text = session.finish()`
2. `apply_corrections(text, vocab.corrections)`
3. `apply_commands(text, vocab.commands)`
4. `paste_via_clipboard(text)`
5. `DictationStore.save_text(text)` → `outputs/dictation/last.txt`

Takes no arguments. The daemon's lifecycle wiring
(`on_dictation_toggle`, `_end_owned_session_if_needed`, close-before-finalize
ordering, `_DICTATION_WAKE` sentinel) is **unchanged** from ADR 0090.

---

## Configuration (`[dictation]` section in `config.toml`)

```toml
[dictation]
ws_url               = "ws://192.168.4.200:8767/ws/transcribe"
language             = "en"
end_word             = "done"
cancel_word          = "cancel"
idle_timeout_seconds = 30
```

| Key | Description |
|-----|-------------|
| `ws_url` | WebSocket `/ws/transcribe` endpoint — the LLM-cleanup proxy (ADR 0093). Replaces the old `endpoint` HTTP URL |
| `language` | BCP-47 language code sent in the config frame |
| `end_word` | Standalone spoken word that ends dictation and pastes |
| `cancel_word` | Standalone spoken word that cancels dictation (no paste) |
| `idle_timeout_seconds` | Max time the asyncio-loop thread waits for final words after the end sentinel |

`DictationConfig` dataclass: `ws_url`, `language`, `idle_timeout_seconds`,
`end_word`, `cancel_word`. The old `endpoint` field is removed.

---

## Custom vocabulary (`outputs/dictation/vocab.json`)

Hot-reloaded on every dictation (no restart required). Three layers:

- `vocab` — word list sent as `initial_prompt` to bias the whisper decoder.
- `corrections` — deterministic mistranscription fixes applied post-finalize.
- `commands` — maps spoken phrases to control characters
  (`"newline"` → `\n`, `"paragraph"` → `\n\n`).

Managed via the `/page/dictation` web page (three editor sections,
`POST /dictation/vocab` saves). `last.wav` and re-transcribe are removed (ADR
0092 D7); the page shows `last.txt` and the vocab editor only.

---

## Error handling

| Failure | Behaviour |
|---------|-----------|
| WebSocket connect fails | asyncio thread records error; `finish()` returns `""`; `dictation.error {reason:"endpoint"}` + miss chime |
| WebSocket drops mid-session | `stream_transcribe` returns early; already-confirmed words are pasted; `dictation.error {reason:"endpoint"}` if nothing confirmed |
| `encode_wav` fails on a chunk | `dictation.error {reason:"encode"}` + miss chime |
| `paste_via_clipboard` fails | `dictation.error {reason:"clipboard"}` + miss chime |
| Spoken cancel | Buffer discarded; `dictation.end {reason:"cancel"}`; sprite shows cancelled-cue badge; no chime |

---

## Related ADRs

| ADR | Topic |
|-----|-------|
| [0086](decisions/0086-dictation-mode.md) | Dictation sub-state, lifecycle, sprite badge |
| [0088](decisions/0088-dictation-postprocessing.md) | Custom vocabulary, post-processing pipeline |
| [0089](decisions/0089-dictation-hotkey-sentinel-cancel-debounce.md) | Hotkey-end sentinel, 50 ms debounce, spoken cancel |
| [0090](decisions/0090-dictation-hotkey-opens-session.md) | Right Ctrl opens own session, close-before-finalize ordering |
| [0091](decisions/0091-streaming-dictation-experiment.md) | Original streaming experiment (promoted) |
| [0092](decisions/0092-streaming-dictation-integration.md) | Integration decision — this document's primary ADR |
| [0093](decisions/0093-transcription-proxy-endpoint.md) | LLM-cleanup proxy endpoint |
| [0094](decisions/0094-consume-done-frame.md) | Consume `done` frame; `finish()` returns LLM-cleaned text with `LocalAgreement` fallback |
