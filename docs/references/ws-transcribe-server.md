# /ws/transcribe — Streaming Transcription Server Contract

**Vendored:** 2026-05-20 (updated for ADR 0096 raw-PCM protocol)
**Source:** Custom Python / FastAPI + faster-whisper VPS pipeline (separate repo)
**Cited by:** `src/voice_commander/dictation/ws_client.py`

## Endpoint

```
WebSocket /ws/transcribe
```

## Frame protocol (ADR 0096 D1–D2)

### Client → Server

| Frame | Type | Description |
|---|---|---|
| Audio chunk | Binary | Raw 16 kHz mono float32 PCM bytes — **no WAV header per message**, no config frame. Each binary frame is a direct `audio_ndarray.tobytes()` slice from one VAD utterance. |
| End | JSON | `{"type":"end"}` — signals end-of-dictation. No `raw_transcript` field. No `initial_prompt` field. |

**Dropped from old protocol (ADR 0091/0092/0095):**
- `{"type":"config",...}` handshake frame — removed. Server hardcodes `language="en"`.
- WAV encoding per message — removed. Raw float32 bytes only.
- `raw_transcript` on the end frame — removed. Server does the only decode.

### Server → Client

| Frame | Type | Description |
|---|---|---|
| Done | JSON | `{"type":"done","text":"...","raw":"..."}` — **exactly one**, sent after the client's `end` frame. `text` is the LLM-cleaned transcript (or raw whisper text if LLM unavailable). `raw` is the raw whisper transcript before LLM cleanup. |
| Error | JSON | `{"type":"error","message":"..."}` — server error (e.g. `"max duration exceeded"`); daemon logs and aborts session. |

**No partial frames. No segments. No accumulated field.** The server is silent until the `end` frame is received.

## Server-side behaviour (ADR 0096 D2)

**Accumulate phase** (binary frames received):

- Per-connection growing byte buffer. Append raw `float32` bytes on every binary frame.
- No per-message decode. No partial responses.

**End phase** (`{"type":"end"}` received):

1. `audio = np.frombuffer(buf, np.float32)`
2. `faster_whisper.WhisperModel.transcribe(audio, vad_filter=True)` — called **once per dictation**, on the final accumulated audio.
3. Join segment texts into one raw transcript.
4. `clean_transcript(raw)` — one LLM round-trip.
5. `structural_format(cleaned)` — regex post-processor: spoken list markers → numbered list with blank-line separators, greeting/structure-cue/short-opening sentences isolated on their own paragraphs. Closing-sentence isolation and topic-shift restructuring are NOT in this regex (require semantic judgement — deferred to LLM fine-tune).
6. Send `{"type":"done","text":<formatted>,"raw":<raw>}` and close.

**Edge cases (server):**

- `WebSocketDisconnect` mid-accumulation: discard buffer. No response.
- Empty or all-silence audio: `{"type":"done","text":"","raw":""}`.
- Server-side max-dictation cap: **600 s**. On hit: `{"type":"error","message":"max duration exceeded"}` + close.

## Daemon-side timeouts

- `cap_timeout_s = 300` — daemon hard cap; fires `{"type":"end"}` and enters finalization.
- `done_timeout_s = 60` — max wait after sending `{"type":"end"}` for the `done` frame (covers Whisper + LLM latency on long dictations).

## `ws_client.stream_transcribe` signature (ADR 0096 D8)

```python
async def stream_transcribe(
    ws_url: str,
    chunk_q: asyncio.Queue[bytes | None],
    cap_timeout_s: float = 300.0,
    done_timeout_s: float = 60.0,
) -> str | None:
```

Returns `done.text` (str, possibly `""`) or `None` on timeout / connection closed / error.
