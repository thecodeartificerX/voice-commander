# /ws/transcribe — Streaming Transcription Server Contract

**Vendored:** 2026-05-20 (updated 2026-05-24 for ADR 0101 optional `timings` key)
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
| Done | JSON | `{"type":"done","text":"...","raw":"..."}` — **exactly one**, sent after the client's `end` frame. `text` is the LLM-cleaned transcript (or raw whisper text if LLM unavailable). `raw` is the raw whisper transcript before LLM cleanup. Optional `timings` key — see below. |
| Error | JSON | `{"type":"error","message":"..."}` — server error (e.g. `"max duration exceeded"`); daemon logs and aborts session. |

**No partial frames. No segments. No accumulated field.** The server is silent until the `end` frame is received.

#### Optional `timings` key on the `done` frame (ADR 0101)

Servers that implement timing observability include a `timings` object in the
`done` frame.  The key is **absent** (not null) on servers that do not
implement it — the daemon reads it via `.get("timings")` and degrades
gracefully (server cells in the `/page/dictation` panel render as "—").

```json
{
  "type": "done",
  "text": "...",
  "raw": "...",
  "timings": {
    "transcribe_ms": 1842.3,
    "clean_ms": 412.6,
    "format_ms": 3.1,
    "server_total_ms": 2261.8
  }
}
```

| Field | Meaning |
|---|---|
| `transcribe_ms` | Wall time of `WhisperModel.transcribe()` alone — Whisper inference, before AI cleanup. |
| `clean_ms` | Wall time of the `clean_transcript()` LLM round-trip (one AI call). |
| `format_ms` | Wall time of `structural_format()` regex post-processing. Reported as `0.0` when the server pipeline has no structural-format step; the client panel then omits the Structural-format row + legend dot. |
| `server_total_ms` | Total from receiving `{"type":"end"}` to just before sending the `done` frame (≥ `transcribe_ms + clean_ms + format_ms`; includes any internal scheduling overhead). |

**Monotonic-clock rule:** all four fields MUST be measured with
`time.monotonic()` (or equivalent per-process monotonic clock) on the server.
No wall-clock sync with the client is required — these are purely
server-internal durations.

All values are **float milliseconds**.  The `timings` key is additive: adding
it to an existing server implementation does not change the daemon's text
output or session lifecycle in any way.

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

## `ws_client.stream_transcribe` signature (ADR 0096 D8, amended ADR 0101)

```python
@dataclass
class TranscribeResult:
    text: str               # done.text (may be empty string)
    server_timings: dict    # done.timings or {} when absent
    roundtrip_ms: float     # monotonic wall time: send {"type":"end"} → receive done frame

async def stream_transcribe(
    ws_url: str,
    chunk_q: asyncio.Queue[bytes | None],
    cap_timeout_s: float = 300.0,
    done_timeout_s: float = 60.0,
) -> TranscribeResult | None:
```

Returns a `TranscribeResult` on success (`.text` may be `""`) or `None` on
timeout / connection closed / error (unchanged from ADR 0096).

`roundtrip_ms` is measured on the asyncio-loop thread: the clock starts
immediately after writing `{"type":"end"}` to the WebSocket and stops when
the `done` frame is fully received.

`DictationSession.finish()` signature is **unchanged** (returns `str | None`).
Timing data is accessed via `DictationSession.get_timings()` after `finish()`
returns (the `loop_thread.join()` inside `finish()` is the happens-before
barrier).
