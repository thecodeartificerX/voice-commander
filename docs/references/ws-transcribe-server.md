# /ws/transcribe — Streaming Transcription Server Contract

**Vendored:** 2026-05-19
**Source:** Custom Python / FastAPI + faster-whisper proxy (separate repo)
**Cited by:** `src/voice_commander/dictation/ws_client.py`, `src/voice_commander/dictation/postprocess.py`

## Endpoint

```
WebSocket /ws/transcribe
```

## Frame protocol

### Client → Server

| Frame | Type | Description |
|---|---|---|
| Config | JSON | `{"type":"config","language":"en","initial_prompt":"..."}` — one-time handshake. `initial_prompt` biases the decoder toward custom vocabulary (optional). |
| Audio chunk | Binary | WAV-encoded audio (16 kHz mono 16-bit PCM). Under the growing-window model (ADR 0095), this is the *whole window* re-sent each step. |
| End | JSON | `{"type":"end"}` — signals end of session. Optionally carries `"raw_transcript":"..."` (the daemon's committed `LocalAgreement` text); when present, the proxy LLM-cleans this text directly. |

### Server → Client

| Frame | Type | Description |
|---|---|---|
| Partial | JSON | `{"type":"partial","text":"...","accumulated":"...","segments":[{"start":0.0,"end":1.2,"text":"..."},...]}`  — `text` is the latest hypothesis. `accumulated` is unreliable under overlapping windows (daemon ignores it). `segments` (ADR 0095) carries per-segment timestamps, seconds relative to the sent WAV; absent on un-upgraded servers. |
| Done | JSON | `{"type":"done","text":"...","raw":"..."}` — exactly one, after the client's `end` frame. `text` is the LLM-cleaned transcript (or raw whisper text if LLM unavailable). `raw` echoes the raw transcript (optional). |
| Error | JSON | `{"type":"error","detail":"..."}` — server error; daemon logs and aborts session. |

## Token limit for `initial_prompt`

Whisper's tokenizer uses a vocabulary where the average token is ~4 characters.
The hard server-side cap is **224 tokens** (~896 characters). If the string exceeds
this limit the server silently truncates it at a token boundary.

Voice Commander's `postprocess.build_prompt` enforces `_PROMPT_CHAR_CAP = 800`
characters by dropping whole trailing words, keeping the prompt safely under the
server cap regardless of the user's vocabulary size.
