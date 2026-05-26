# Streaming Dictation

Dictation is a voice-session sub-state. Saying bare "dictate" or pressing Right
Ctrl (`dictation_key`, default `ctrl_r`) enters dictation. The daemon streams raw
16 kHz mono float32 PCM over a WebSocket to the VPS pipeline. The server
accumulates the raw bytes, decodes once on the final audio, LLM-cleans the
result, and returns a single `done` frame. The daemon pastes the cleaned text at
the cursor.

**ADR:** [0096 — Server-Side Dictation (Major Pivot)](decisions/0096-server-side-dictation.md)
— the authoritative architecture reference. Supersedes the streaming-window model
of ADR 0095, the per-chunk integration of ADR 0092, and the LocalAgreement
experiment of ADR 0091. [ADR 0093](decisions/0093-transcription-proxy-endpoint.md)
established the LLM-cleanup proxy. [ADR 0094](decisions/0094-consume-done-frame.md)
defines the `done`-frame contract (reaffirmed by ADR 0096). Daemon lifecycle wiring
(ADR 0086/0089/0090) is unchanged.

## Transcription server

`[dictation] ws_url` points at a **WebSocket server** (`ws://192.168.4.200:8767/ws/transcribe`,
ADR 0093/0096). The server accumulates raw PCM bytes sent by the daemon, performs
a single Whisper decode on the full audio on receipt of the `end` frame, LLM-cleans
the transcript, and returns `{"type":"done","text":<cleaned>,"raw":<raw>}`.

- **No partial frames, no config frame, no segments.** The server is silent until
  the `end` frame arrives. The HUD is quiet during dictation.
- **The final transcript is LLM-cleaned** by the server before returning the `done`
  frame. The daemon reads `done.text` (ADR 0094) and pastes it. If no `done` frame
  arrives within `done_timeout_s` (60 s), `finish()` returns `None`; an empty string
  is pasted with a miss chime.
- **`vad_filter=True`** in `transcribe()` removes silence segments on the server;
  the full audio is decoded with complete context, eliminating boundary word loss
  and stray `...`.

The cleanup is entirely server-side; the daemon stays local-first.

---

## Flow

```
VAD utterances → handle_utterance(audio, text) [endpointing-only]
    │  classifies: "end" | "cancel" | "buffered"
    │  on "buffered": puts audio.tobytes() onto chunk_q (raw float32 PCM bytes)
    │
    ▼ (asyncio-loop thread)
ws_client.stream_transcribe(ws)
    │  binary frame: raw PCM bytes from chunk_q, no WAV headers
    │  on END sentinel: sends {"type":"end"}
    │  publishes "dictation.processing" SSE → sprite shows processing badge
    │  reads server's done frame → returns done.text (LLM-cleaned)
    │
    ▼ finish() — end-word or hotkey-end
DictationSession.finish() joins asyncio thread → returns done.text (or None on timeout)
    │
    ▼ _finalize_dictation (daemon, _dictation_executor thread)
apply_corrections → apply_commands → paste_via_clipboard → DictationStore.save_text
```

---

## Architecture

### `DictationSession` (`src/voice_commander/dictation/session.py`)

Owns the WebSocket transport. On `start(vocab)`:

1. Loads `vocab.json`, builds `initial_prompt = build_prompt(vocab)`.
2. Creates a `chunk_q` (`asyncio.Queue[bytes | None]`).
3. Spawns an asyncio-loop thread that runs `ws_client.stream_transcribe` — the
   thread drains `chunk_q` and sends raw PCM bytes as binary frames to the server.
4. Publishes `dictation.start`.

On `handle_utterance(audio, text) -> "end" | "cancel" | "buffered"` (endpointing-only):

- Classification only: end-word / cancel-word / accumulate.
- On `"buffered"`: puts `audio.tobytes()` (raw float32 PCM) onto `chunk_q`.
- End-word and cancel-word VAD utterances trigger exit classification only; their
  audio is not sent.

On `finish() -> str | None` (normal exit):

- Pushes the `None` end sentinel to the chunk queue.
- Joins the asyncio-loop thread (bounded by `done_timeout_s` + margin).
  During that thread, `stream_transcribe` sends `{"type":"end"}`, publishes
  `dictation.processing`, then reads the server's `done` frame, storing
  `done.text` in `_final_text` (ADR 0094).
- Returns `_final_text` (`str`, possibly `""`) when not `None`.
  Returns `None` when no `done` frame arrived (timeout / closed / error).
- Publishes `dictation.end {reason:"done"}`.

On `cancel()`:

- Closes the WebSocket immediately. No `end` frame is sent.
- Publishes `dictation.end {reason:"cancel"}`.

`request_end()`, `pending_end`, and `active` are unchanged — they drive the
hotkey-end drain path (ADR 0089).

### Supporting modules (`src/voice_commander/dictation/`)

| Module | Responsibility |
|--------|----------------|
| `ws_client.py` | `stream_transcribe` — async WebSocket client; sends raw PCM binary frames from `chunk_q`; on END sentinel sends `{"type":"end"}`; reads `done` frame (ADR 0094); returns `done.text` or `None` |
| `store.py` | `DictationStore.save_text` / `read_text` |
| `postprocess.py` | `apply_corrections`, `apply_commands`, `build_prompt` |

> `local_agreement.py`, `window.py` — deleted (ADR 0096 D7). No client-side
> transcript assembly. `bridge.py` was already unused.

### Daemon `_finalize_dictation`

Runs on the `_dictation_executor` single-worker thread (FIFO, unchanged from
ADR 0090). Five steps:

1. `text = session.finish()` — returns `done.text` or `None` (→ `""`)
2. `apply_corrections(text, vocab.corrections)`
3. `apply_commands(text, vocab.commands)`
4. `paste_via_clipboard(text)`
5. `DictationStore.save_text(text)` → `outputs/dictation/last.txt`

Takes no arguments. The daemon's lifecycle wiring
(`on_dictation_toggle`, `_end_owned_session_if_needed`, close-before-finalize
ordering, `_DICTATION_WAKE` sentinel) is **unchanged** from ADR 0090.

VAD is **endpointing-only**: `handle_utterance()` classifies end/cancel/buffered
and puts raw PCM bytes on `chunk_q` for buffered utterances. Audio does not flow
through a frame tap or DictationWindow — those are deleted.

### Backend selection — internal vs external (ADR 0102)

`[dictation] backend` selects the dictation engine:

- **`internal`** (default) — everything described above: record VAD-segmented PCM → stream to the WS server → paste the cleaned transcript.
- **`external`** — a *passthrough* sub-state. Right Ctrl / spoken "dictate" plays the same start chime and shows the same DICTATING badge + bright cat, but VC records nothing, transcribes nothing, sends nothing, and pastes nothing; its own command routing is muted while active (`_process_utterance` returns early on the `_passthrough_active` flag). An external tool — e.g. **Wispr Flow, bound by the user to the same Right Ctrl key** — performs the actual dictation. VC does not suppress Right Ctrl (the pynput listener has no `suppress`), so one press reaches both apps. A second Right Ctrl press exits and restores the prior state. External mode publishes the existing `dictation.start`/`dictation.end` events but **never** `dictation.processing` (no server round-trip), so no PROCESSING badge appears and no `last_timings.json` is written. There is no spoken exit in external mode (VC transcribes nothing); Right Ctrl is the only way out. The backend is cached at daemon startup — a change requires a restart.

**Wispr Flow setup:** bind Wispr Flow's dictation toggle to Right Ctrl (`ctrl_r`, VC's default `dictation_key`), set `backend = "external"` in `[dictation]`, and restart the daemon.

---

## Configuration (`[dictation]` section in `config.toml`)

```toml
[dictation]
backend              = "internal"  # "internal" = record→WS→paste (default); "external" = passthrough (an external tool e.g. Wispr Flow does dictation)
ws_url               = "ws://192.168.4.200:8767/ws/transcribe"
end_word             = "done"
cancel_word          = "cancel"
idle_timeout_seconds = 30
max_dictation_s      = 300
```

| Key | Description |
|-----|-------------|
| `backend` | `"internal"` = local record→WS→paste pipeline; `"external"` = passthrough (mute + animate only; an external tool does the dictation). Unknown value → WARNING + `"internal"`. Restart to change. |
| `ws_url` | WebSocket `/ws/transcribe` endpoint — the VPS LLM-cleanup server (ADR 0093/0096) |
| `end_word` | Standalone spoken word that ends dictation and pastes |
| `cancel_word` | Standalone spoken word that cancels dictation (no paste) |
| `idle_timeout_seconds` | Max time the asyncio-loop thread waits for final words after the end sentinel |
| `max_dictation_s` | Daemon-side hard cap per dictation (default 300 s); on hit, daemon sends `{"type":"end"}` and finalizes normally. Server cap is 600 s. |

`DictationConfig` dataclass: `ws_url`, `idle_timeout_seconds`, `end_word`,
`cancel_word`, `max_dictation_s`. The old `window_step_ms`, `window_cap_ms`, and
`language` fields are removed.

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

## Sprite states

| State | Trigger | Visual |
|-------|---------|--------|
| `dictating` | `dictation.start` | `● DICTATING` badge |
| `processing` | `dictation.processing` (after end frame sent) | light-blue processing badge |
| idle / done | `dictation.end` (any reason) | badge clears |
| cancelled | `dictation.end {reason:"cancel"}` | `✕ CANCELLED` badge (2.5 s, auto-clears) |

The `processing` state is new in ADR 0096 — it bridges the gap between the end
frame being sent and the Whisper + LLM round-trip completing (~0.5–25 s). The HUD
is otherwise silent during dictation (no per-utterance transcript events from the
dictation path).

---

## Error handling

| Failure | Behaviour |
|---------|-----------|
| WebSocket connect fails | asyncio thread records error; `finish()` returns `None`; `dictation.error {reason:"endpoint"}` + miss chime |
| WebSocket drops mid-session | `stream_transcribe` returns `None`; empty paste + miss chime; `dictation.error {reason:"endpoint"}` |
| `done` frame timeout (60 s) | `finish()` returns `None`; empty paste + miss chime |
| Server error frame | Treated as no `done` frame; `finish()` returns `None` |
| `paste_via_clipboard` fails | `dictation.error {reason:"clipboard"}` + miss chime |
| Spoken cancel | Connection closed (no end frame); `dictation.end {reason:"cancel"}`; sprite shows cancelled-cue badge; no chime |
| Daemon cap hit (300 s) | `{"type":"end"}` sent; enters normal finalization; server decodes what it has |

---

## Related ADRs

| ADR | Topic |
|-----|-------|
| [0086](decisions/0086-dictation-mode.md) | Dictation sub-state, lifecycle, sprite badge |
| [0088](decisions/0088-dictation-postprocessing.md) | Custom vocabulary, post-processing pipeline |
| [0089](decisions/0089-dictation-hotkey-sentinel-cancel-debounce.md) | Hotkey-end sentinel, 50 ms debounce, spoken cancel |
| [0090](decisions/0090-dictation-hotkey-opens-session.md) | Right Ctrl opens own session, close-before-finalize ordering |
| [0091](decisions/0091-streaming-dictation-experiment.md) | Original streaming experiment (superseded) |
| [0092](decisions/0092-streaming-dictation-integration.md) | Per-chunk integration decision (superseded; lifecycle reaffirmed) |
| [0093](decisions/0093-transcription-proxy-endpoint.md) | LLM-cleanup proxy endpoint |
| [0094](decisions/0094-consume-done-frame.md) | Consume `done` frame; reaffirmed by ADR 0096 |
| [0095](decisions/0095-streaming-window-dictation.md) | Growing-window model (superseded) |
| [0096](decisions/0096-server-side-dictation.md) | Server-side dictation pivot — current authoritative transport ADR |
