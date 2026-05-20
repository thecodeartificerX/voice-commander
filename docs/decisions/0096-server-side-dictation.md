# ADR 0096 — Server-Side Dictation (Major Pivot)

**Status:** Accepted
**Date:** 2026-05-20
**Supersedes:** [ADR 0091](0091-streaming-dictation-experiment.md), [ADR 0092](0092-streaming-dictation-integration.md), [ADR 0095](0095-streaming-window-dictation.md) (streaming transport and assembly; daemon lifecycle wiring unchanged)
**Reaffirms:** [ADR 0094](0094-consume-done-frame.md) — `done` frame contract is retained

## Context

### Production bugs that triggered this pivot

The streaming-window architecture (ADR 0095) shipped client-side transcript
assembly: the daemon accumulated a growing audio window, re-encoded it as WAV
every `window_step_ms`, streamed each whole-window WAV to the server for a fresh
Whisper decode, and fed consecutive whole-window hypotheses through
`LocalAgreement-2` to commit the stable prefix. Two bugs surfaced in production
within 24 hours of that landing:

**Bug 1 — LA-2 reset truncation (commit 6adc6a7).** After any audio trim
(`DictationWindow.commit()`), the next server hypothesis re-decodes only the
post-trim sub-window — it does NOT restart with the previously-committed words.
`LocalAgreement._prev` held a pre-trim hypothesis whose word list had no common
prefix with the post-trim hypothesis; `agreed` collapsed to 0 or a small number;
`hypothesis[already:agreed]` became a nonsense slice. In live use this produced
an 11-character paste (`"This is boy"`) instead of a full spoken sentence.
The fix (reset `LocalAgreement` after every trim) was applied, but it exposed
the deeper fragility: the trim/reset cycle restarts the agreement clock, meaning
late-session words are always less stable than early-session words.

**Bug 2 — Architectural class-of-bugs.** The ADR trail tells the story:

| ADR | Problem chased |
|-----|----------------|
| 0091 | LocalAgreement designed for whole-window but wired to per-chunk |
| 0092 | Promoted per-chunk transport to production, kept LocalAgreement |
| 0094 | `done` frame silently discarded — zero-code claim was wrong |
| 0095 | Per-chunk → whole-window; LocalAgreement-2 re-worked; frame tap added |
| 0095 bug fix | Trim/reset cycle breaks LA-2 index math |

Four ADRs and one in-production patch in two days chasing the consequences of
one architectural choice: **the daemon is doing client-side transcript assembly**.

### Sakib's call

Stop reassembling on the client. Move ALL transcript assembly to the server.
The daemon is a thin PCM transport and UI coordinator. The server owns
accumulation, decode, and LLM cleanup. No more LocalAgreement, no more
DictationWindow, no more frame tap, no more growing-window WAVs on the wire.

### Design outcome (locked with VPS engineer, 2026-05-20)

The server engineer confirmed the new server contract below is implementable and
is being built in a parallel branch. The daemon pivot described in this ADR can
land against a mock server in tests, then cut over when the server deploys.

## Decision

### D1 — Raw PCM wire format (no WAV headers per-message)

Binary WebSocket frames carry **raw 16 kHz mono float32 PCM** — the same byte
layout that `sounddevice` / `soxr` produce internally. No per-message WAV header.
One JSON frame `{"type":"end"}` from the daemon signals end-of-dictation.

The config frame (`{"type":"config",...}`) is **dropped entirely**. The server
hardcodes `language="en"` (this is an English-only project). Removing the config
frame eliminates one source of protocol drift and one test fixture case.

### D2 — Server contract (locked)

**Accumulate phase** (binary frames received):

- Per-connection growing byte buffer. Append raw `float32` bytes on every
  binary frame. No per-message decode. No `partial` frames. No `segments`. No
  `context` text-accumulator.

**End phase** (`{"type":"end"}` received):

1. `audio = np.frombuffer(buf, np.float32)`
2. `faster_whisper.WhisperModel.transcribe(audio, vad_filter=True)` — called
   **once per dictation**, on the final accumulated audio.
3. Join segment texts into one raw transcript.
4. `clean_transcript(raw)` — one LLM round-trip.
5. Send `{"type":"done","text":<cleaned>,"raw":<raw>}` and close.

**Edge cases (server):**

- `WebSocketDisconnect` mid-accumulation: discard buffer. No response.
- Empty or all-silence audio: `{"type":"done","text":"","raw":""}`.
- Server-side max-dictation cap: **600 s**. On hit: `{"type":"error","message":"max duration exceeded"}` + close.

### D3 — Daemon role: thin transport + UI coordination

The daemon's job during dictation is now:

1. Capture mic audio via VAD (unchanged — drives end-word detection).
2. Stream raw PCM bytes over the WebSocket to `[dictation] ws_url`.
3. On end-word / hotkey-end / spoken cancel: stop sending audio, send
   `{"type":"end"}`, enter "processing" sprite state, await `done` frame.
4. Receive `{"type":"done","text":...}`, apply post-processing, paste via
   clipboard.
5. Cancel path: close connection immediately. No end frame. Sprite shows cancel
   badge (existing behaviour, ADR 0089).

The daemon does not decode, does not stabilise, does not accumulate windows.

### D4 — Daemon-side cap: 300 s

The daemon enforces a **300-second (5-minute) hard cap** per dictation. On
timeout, it sends `{"type":"end"}` and enters the normal finalization path (the
server decodes what it has). This is distinct from the server's 600 s cap —
2× headroom to avoid a race where the daemon closes first. Config key:
`[dictation] max_dictation_s` (default 300). Replaces `window_step_ms` and
`window_cap_ms` (both deleted).

### D5 — "Processing" sprite state + HUD-quiet dictation

After sending the `{"type":"end"}` frame, the daemon publishes a new SSE event:

```
dictation.processing  {}
```

The sprite state machine adds a `processing` state between `dictating` and
`done`: the user sees the sprite is working, not frozen, during the ~0.5–25 s
wait for the Whisper + LLM round-trip.

The `transcript` SSE event is **no longer emitted during dictation**. Under the
old streaming-window model, partials drove live HUD updates. Under the new
server-side model there are no partials — the HUD stays silent until paste. The
`transcript` event continues to fire in command mode (unchanged).

### D6 — Audio path in `DictationSession`: per-VAD-utterance chunks

With the growing-window and frame tap gone, the audio path reverts to the
simpler approach from ADR 0092: `handle_utterance(audio, text)` puts the raw
PCM bytes (`audio.tobytes()`) onto the chunk queue during dictation. The asyncio
loop sends those bytes as binary WebSocket frames. End-word and cancel-word
audio are not sent (classification-only, unchanged).

This is the same chunk-delivery approach as ADR 0092 but without per-chunk WAV
encoding and without per-chunk decode on the server. The server accumulates raw
bytes; the daemon delivers them utterance-by-utterance.

### D7 — Deletion targets (daemon side)

The following are removed entirely once D3 and D6 land:

| Target | Location |
|--------|----------|
| `LocalAgreement`, `TimedWord` | `src/voice_commander/dictation/local_agreement.py` — entire file deleted |
| `DictationWindow` | `src/voice_commander/dictation/window.py` — entire file deleted |
| `StreamingRecorder.set_frame_tap` | `src/voice_commander/streaming_recorder.py` lines ~438–452 |
| `StreamingRecorder._invoke_frame_tap` | `src/voice_commander/streaming_recorder.py` lines ~454–460 |
| `StreamingRecorder._frame_tap` field | `src/voice_commander/streaming_recorder.py` line ~414 |
| `_invoke_frame_tap` call in `_vad_loop` | `src/voice_commander/streaming_recorder.py` line ~794 |
| `DictationSession._on_frame` | `src/voice_commander/dictation/session.py` |
| `DictationSession._on_partial` | `src/voice_commander/dictation/session.py` |
| `DictationSession._confirmed` field | `src/voice_commander/dictation/session.py` |
| `DictationSession._agreement` field | `src/voice_commander/dictation/session.py` |
| `DictationSession._agreement_lock` field | `src/voice_commander/dictation/session.py` |
| `DictationSession._warned_no_segments` field | `src/voice_commander/dictation/session.py` |
| `DictationSession._build_raw_transcript` | `src/voice_commander/dictation/session.py` |
| `DictationSession.set_recorder` | `src/voice_commander/dictation/session.py` |
| `DictationSession._window` field | `src/voice_commander/dictation/session.py` |
| `DictationSession._recorder` field | `src/voice_commander/dictation/session.py` |
| `ws_client.stream_transcribe` `on_partial` param | `src/voice_commander/dictation/ws_client.py` |
| `ws_client.stream_transcribe` `prompt` param | `src/voice_commander/dictation/ws_client.py` |
| `ws_client.stream_transcribe` `raw_transcript_fn` param | `src/voice_commander/dictation/ws_client.py` |
| `ws_client.stream_transcribe` `language` param | `src/voice_commander/dictation/ws_client.py` |
| `ws_client.stream_transcribe` config frame send | `src/voice_commander/dictation/ws_client.py` |
| `ws_client.stream_transcribe` segments parsing | `src/voice_commander/dictation/ws_client.py` |
| `[dictation] window_step_ms` config key | `config.toml.example`, `src/voice_commander/config.py` |
| `[dictation] window_cap_ms` config key | `config.toml.example`, `src/voice_commander/config.py` |
| `[dictation] language` config key | `config.toml.example`, `src/voice_commander/config.py` |
| `DictationSession` constructor `language` param | `src/voice_commander/dictation/session.py` |
| `DictationSession` constructor `window_step_ms` param | `src/voice_commander/dictation/session.py` |
| `DictationSession` constructor `window_cap_ms` param | `src/voice_commander/dictation/session.py` |
| Daemon `dictation_session.set_recorder(...)` call | `src/voice_commander/daemon.py` line ~1573 |
| Daemon `window_step_ms`/`window_cap_ms` pass-through | `src/voice_commander/daemon.py` lines ~1446–1447 |
| `tests/integration/_dictation_ws.py` config-frame handling | `tests/integration/_dictation_ws.py` |
| `tests/integration/_dictation_ws.py` per-chunk partial replies | `tests/integration/_dictation_ws.py` |
| `tests/unit/test_dictation_local_agreement.py` | entire file deleted |
| `tests/unit/test_dictation_window.py` | entire file deleted |
| `tests/unit/test_streaming_recorder_frame_tap.py` | entire file deleted |

### D8 — New `ws_client.stream_transcribe` signature

```python
async def stream_transcribe(
    ws_url: str,
    chunk_q: asyncio.Queue[bytes | None],
    cap_timeout_s: float = 300.0,
    done_timeout_s: float = 60.0,
) -> str | None:
```

- **No `language`, `on_partial`, `prompt`, `raw_transcript_fn`**. All removed.
- Sends raw binary bytes from `chunk_q`. On END sentinel sends `{"type":"end"}`.
- Reads until `done` frame, `error` frame, `done_timeout_s` expiry, or
  `ConnectionClosed`. Returns `done.text` (str, possibly empty) or `None`.
- `cap_timeout_s`: kills the upload loop and sends `{"type":"end"}` when the
  daemon-side cap fires (300 s default). Distinct from `done_timeout_s` (60 s)
  which covers the Whisper + LLM wait after the end frame is sent.

### D9 — Superseded ADRs

- **ADR 0091** — superseded. The LocalAgreement stabiliser it introduced is
  deleted. The WebSocket transport pattern it established is reused but simplified.
- **ADR 0092** — superseded (transport only). The per-chunk streaming pattern it
  introduced returns in simplified form (no WAV encoding, no per-chunk decode,
  no LocalAgreement), but the per-chunk `on_partial` routing is gone.
- **ADR 0095** — superseded. Growing-window streaming, frame tap, DictationWindow,
  and LocalAgreement-2 are all deleted.
- **ADR 0094** — reaffirmed. The `done` frame contract (send `{"type":"end"}`,
  read until `done`, return `done.text`, fall back to `None` on timeout) is
  unchanged. `done_timeout_s` remains; its default increases to 60 s to cover
  the longer Whisper + LLM latency on long dictations.

## Consequences

### Positive

- **Eliminates the class-of-bugs.** LA-2 index math, trim/reset races, offset
  alignment, stray-partial forwarding, segments-absent degradation — all gone.
  Client is ~180 lines shorter.
- **No client-side model of transcription state.** The only daemon state during
  dictation is "are we still sending?" (the chunk queue) and "did we get a done
  frame?" (the join result). Bug surface reduced to the wire protocol.
- **Simpler wire protocol.** Binary frames → `{"type":"end"}` → `{"type":"done"}`.
  Three frame types instead of seven (config, binary, partial, done, error, raw,
  segments).
- **One Whisper decode per dictation.** The server's `vad_filter=True` removes
  silence segments; the full audio is decoded with complete context, eliminating
  boundary word loss and stray `...`.
- **Test mocks are trivial.** A mock server that accumulates bytes and sends a
  hard-coded done frame is 20 lines of asyncio. No partial-routing logic needed.

### Negative

- **No live HUD feedback during dictation.** Under ADR 0095, the HUD showed the
  committed-prefix growing. Under this design, the HUD is silent until paste.
  The sprite "processing" state (D5) provides the only visual indication that
  dictation was captured and is being processed.
- **Latency concentrated at end.** The ~0.5–25 s wait for Whisper + LLM happens
  after the end word, not distributed across the recording. For very long
  dictations (>30 s) the user waits longer at the end.
- **Server regression risk.** If the server-side accumulation has bugs (e.g.
  dropped binary frames, buffer overflow at 600 s), the daemon has no fallback
  — it cannot reconstruct the transcript from local state. The daemon-side 300 s
  cap limits worst-case exposure.

### Neutral

- Daemon lifecycle wiring (`on_dictation_toggle`, `_finalize_dictation`,
  `_end_owned_session_if_needed`, close-before-finalize ordering, ADR 0089/0090)
  is **unchanged**.
- `_dictation_executor` thread model unchanged.
- Post-processing (`apply_corrections`, `apply_commands`), clipboard paste, and
  `last.txt` write are unchanged.
- `idle_timeout_s` config key retained; its semantics shift slightly — it now
  governs how long the daemon waits for a new PCM chunk before abandoning the
  upload (not the inter-partial wait), serving the same "server went away"
  protection role.
- `websockets` dependency unchanged.
- Tool catalogue unchanged at 11 primitives.

## References

- [ADR 0091](0091-streaming-dictation-experiment.md) — superseded
- [ADR 0092](0092-streaming-dictation-integration.md) — superseded (transport only)
- [ADR 0093](0093-transcription-proxy-endpoint.md) — proxy endpoint (unchanged)
- [ADR 0094](0094-consume-done-frame.md) — `done` frame contract (reaffirmed)
- [ADR 0095](0095-streaming-window-dictation.md) — superseded
- `src/voice_commander/dictation/session.py` — `DictationSession` (to be simplified)
- `src/voice_commander/dictation/ws_client.py` — `stream_transcribe` (to be rewritten)
- `docs/superpowers/plans/2026-05-20-server-side-dictation.md` — implementation plan
