# Streaming Dictation

Dictation is a voice-session sub-state. Saying bare "dictate" or pressing Right
Ctrl (`dictation_key`, default `ctrl_r`) enters dictation; a `StreamingRecorder`
frame tap feeds raw audio into a growing `DictationWindow` buffer, which emits
whole-window WAVs on a fixed cadence to a whisper WebSocket server. Words are
stabilised progressively by `LocalAgreement-2` as whole-window partial transcripts
arrive. When dictation ends the stabilised transcript is post-processed and pasted
at the cursor.

**ADR:** [0092 — Streaming Dictation Integration](decisions/0092-streaming-dictation-integration.md)
(supersedes the batch POST transport of ADR 0086/0090; promotes the experiment
from ADR 0091). [ADR 0093](decisions/0093-transcription-proxy-endpoint.md) moved
the endpoint to an LLM-cleanup proxy — see [Transcription proxy](#transcription-proxy) below.
[ADR 0094](decisions/0094-consume-done-frame.md) corrects ADR 0093's "zero code change" claim:
the daemon now consumes the proxy's `done` frame to receive the LLM-cleaned transcript.
[ADR 0095](decisions/0095-streaming-window-dictation.md) replaces the per-chunk model with a
growing-window model: the whole buffer is re-sent each step and `LocalAgreement-2` commits
the longest agreeing prefix of consecutive whole-window hypotheses.

## Transcription proxy

`[dictation] ws_url` points at a **WebSocket proxy** (`ws://192.168.4.200:8767/ws/transcribe`,
ADR 0093), not the raw whisper server. The proxy is protocol-compatible — same
config frame, audio-chunk frames, and `partial` / `done` / `error` replies.

- **Partial frames pass through unchanged** → `LocalAgreement-2` stabilisation and
  the live HUD transcript feedback work exactly as before.
- **The final `done` transcript is LLM-cleaned** by the proxy (punctuation,
  casing, disfluency removal). After sending `{"type":"end","raw_transcript":"..."}`,
  the daemon **reads the `done` frame** (`stream_transcribe` loops on `ws.recv()` until a
  `{"type":"done","text":"..."}` frame arrives, bounded by `done_timeout_s=15 s`).
  `DictationSession.finish()` returns the `done.text` value as the transcript
  (ADR 0094).
- If no `done` frame arrives (timeout, connection closed, or server error),
  `finish()` **falls back to the `LocalAgreement-2`-stabilised transcript** built
  from the accumulated `partial` frames — the pre-0093 behaviour, preserving
  graceful degradation.
- If the LLM is temporarily unavailable, the proxy **silently returns raw whisper
  text in the `done` frame** — dictation still completes with the raw text.
- When the server omits `segments` on `partial` frames (un-upgraded server), the
  daemon degrades gracefully: trimming is bounded by `window_cap_ms` instead of
  segment end-times (OI-1 fallback).

The cleanup is entirely server-side; the daemon stays local-first and contacts
only the proxy.

---

## Flow

```
StreamingRecorder frame tap (raw 16 kHz float32 frames)
    │
    ▼ DictationWindow._on_frame()
DictationWindow (growing 16 kHz float32 buffer)
    │  every window_step_ms (default 1000 ms) emit whole-window WAV
    │  push WAV onto sync chunk_q
    │
    ▼ (asyncio-loop thread)
ws_client.stream_transcribe(ws)   ← sends whole-window WAV; receives partial transcripts
    │  on_partial(text, segments)
    ▼
LocalAgreement-2.feed(words)      ← commits longest common prefix of two consecutive
    │  whole-window hypotheses     whole-window hypotheses; carries TimedWord end-times
    │  DictationWindow.commit(end_s) trims buffer at committed word's end-time
    │  (lock-guarded accumulator; window_cap_ms bounds uncommitted buffer)
    │
VAD utterances → handle_utterance(audio, text) [endpointing-only]
    │  classifies: "end" | "cancel" | "buffered"
    │  does NOT stream audio as chunks (audio flows via frame tap)
    │
    ▼ finish() — end-word or hotkey-end
DictationWindow flush + end sentinel pushed
asyncio thread sends {"type":"end","raw_transcript":"..."}  (committed raw text via raw_transcript_fn)
asyncio thread reads proxy's done frame → _final_text (LLM-cleaned done.text)
    │
    ▼ _finalize_dictation (daemon, _dictation_executor thread)
apply_corrections → apply_commands → paste_via_clipboard → DictationStore.save_text
```

---

## Architecture

### `DictationSession` (`src/voice_commander/dictation/session.py`)

Owns the WebSocket transport and the `DictationWindow`. On `start(vocab)`:

1. Loads `vocab.json`, builds `initial_prompt = build_prompt(vocab)`.
2. Creates a `DictationWindow` instance (growing 16 kHz float32 buffer).
3. Registers a frame tap on `StreamingRecorder` via `set_frame_tap()` — raw 16 kHz
   float32 frames flow directly into `DictationWindow._on_frame()`.
4. Spawns an asyncio-loop thread that runs `ws_client.stream_transcribe` — the
   thread drains `_chunk_q` (whole-window WAVs emitted by `DictationWindow`) and
   sends them to the server. `bridge.py` is no longer in the dictation pipeline.
5. Sends the config frame `{language, initial_prompt}` to `/ws/transcribe`.
6. Routes each partial `(text, segments)` to `LocalAgreement-2.feed` under a lock;
   on commit, calls `DictationWindow.commit(end_s)` to trim the buffer.
7. Publishes `dictation.start`.

On `handle_utterance(audio, text) -> "end" | "cancel" | "buffered"` (endpointing-only):

- Classification only: end-word / cancel-word / accumulate.
- On `"buffered"`: returns immediately — no audio encoding, no chunk push. Audio
  is flowing continuously via the frame tap.
- End-word and cancel-word VAD utterances trigger exit classification only.

On `finish() -> str` (normal exit):

- Clears the frame tap (stops new audio flowing into `DictationWindow`).
- Flushes the `DictationWindow` (emits the final partial window as WAV).
- Pushes the `None` end sentinel to the chunk queue.
- Joins the asyncio-loop thread (bounded by `idle_timeout_seconds` + margin).
  During that thread, `stream_transcribe` sends `{"type":"end","raw_transcript":"..."}`
  (the full committed raw text via `raw_transcript_fn`) then reads the proxy's
  `done` frame, storing `done.text` in `_final_text` (ADR 0094).
- Calls `LocalAgreement-2.finalize()` to flush the confirmed-word accumulator
  (always, for the fallback path).
- Returns `_final_text` (the proxy's LLM-cleaned `done.text`) when not `None`.
  Falls back to the `LocalAgreement-2`-stabilised transcript when `_final_text is
  None` (no `done` frame received — timeout / closed / error).
- Publishes `dictation.end {reason:"done"}`.
- Returns `""` if the WebSocket never connected or produced no output.

On `cancel()`:

- Clears the frame tap.
- Closes the WebSocket, discards the buffer.
- Publishes `dictation.end {reason:"cancel"}`.

`request_end()`, `pending_end`, and `active` are unchanged — they drive the
hotkey-end drain path (ADR 0089).

### Supporting modules (`src/voice_commander/dictation/`)

| Module | Responsibility |
|--------|----------------|
| `ws_client.py` | `stream_transcribe` — async WebSocket client; sends whole-window WAVs, receives `partial{text, segments}` frames, reads `done` frame (ADR 0094); `on_partial` is 2-arg `(text, segments)` |
| `window.py` | `DictationWindow` — growing 16 kHz float32 audio buffer with step-cadence emission and committed-offset trimming |
| `local_agreement.py` | `LocalAgreement-2` — whole-window prefix word stabiliser; commits longest common prefix of two consecutive whole-window hypotheses; words carry absolute-stream end-times via `TimedWord(text, end_s)` |
| `store.py` | `encode_wav`, `DictationStore.save_text` / `read_text` |
| `postprocess.py` | `apply_corrections`, `apply_commands`, `build_prompt` |

> `bridge.py` (`pump` — sync-to-async queue bridge) is no longer used in the
> dictation pipeline; the chunk queue is drained directly by `stream_transcribe`.

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

VAD is **endpointing-only** in the streaming-window model: `handle_utterance()`
classifies end/cancel/buffered but no longer encodes or streams audio as chunks.

---

## Configuration (`[dictation]` section in `config.toml`)

```toml
[dictation]
ws_url               = "ws://192.168.4.200:8767/ws/transcribe"
language             = "en"
end_word             = "done"
cancel_word          = "cancel"
idle_timeout_seconds = 30
window_step_ms       = 1000
window_cap_ms        = 25000
```

| Key | Description |
|-----|-------------|
| `ws_url` | WebSocket `/ws/transcribe` endpoint — the LLM-cleanup proxy (ADR 0093). Replaces the old `endpoint` HTTP URL |
| `language` | BCP-47 language code sent in the config frame |
| `end_word` | Standalone spoken word that ends dictation and pastes |
| `cancel_word` | Standalone spoken word that cancels dictation (no paste) |
| `idle_timeout_seconds` | Max time the asyncio-loop thread waits for final words after the end sentinel |
| `window_step_ms` | Cadence (ms) at which `DictationWindow` emits whole-window WAVs (default 1000) |
| `window_cap_ms` | Maximum uncommitted buffer size in ms before cap-bounded trim (default 25000) |

`DictationConfig` dataclass: `ws_url`, `language`, `idle_timeout_seconds`,
`end_word`, `cancel_word`, `window_step_ms`, `window_cap_ms`. The old `endpoint`
field is removed.

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
| WebSocket drops mid-session | `stream_transcribe` returns early; already-committed words are pasted; `dictation.error {reason:"endpoint"}` if nothing committed |
| `encode_wav` fails on a window | `dictation.error {reason:"encode"}` + miss chime |
| `paste_via_clipboard` fails | `dictation.error {reason:"clipboard"}` + miss chime |
| Server omits `segments` on partials | Graceful degradation: cap-bounded trimming instead of segment-based trimming (OI-1) |
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
| [0095](decisions/0095-streaming-window-dictation.md) | Growing-window model; `DictationWindow`; `LocalAgreement-2`; VAD endpointing-only |
