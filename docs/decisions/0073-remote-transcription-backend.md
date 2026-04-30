# ADR 0073: Remote Transcription Backend

**Status:** Accepted
**Date:** 2026-04-29

## Context

Until now, transcription was hardcoded to `faster-whisper` running locally on CUDA. This works on developer hardware with a discrete GPU but rules out two real scenarios:

1. **Offload to a beefier box.** A second machine on the LAN already runs whisper.cpp's `examples/server` exposing `POST /inference` (multipart WAV in, `verbose_json` out). Pointing the daemon at that endpoint frees the local GPU for other workloads (LM Studio, training, gaming).
2. **Lower-spec clients.** Laptops and mini PCs without a CUDA GPU can still run the daemon if they offload Whisper.

The pipeline downstream of `Transcriber.transcribe()` only consumes `TranscriptionResult` (text + confidence + no_speech_prob). Any backend producing the same value object is interchangeable.

## Decision

Introduce a `[transcription] backend = "local" | "remote"` toggle. Default `"local"` preserves existing behaviour byte-for-byte. `"remote"` constructs a `RemoteTranscriber` that POSTs the utterance WAV to a configurable whisper.cpp `/inference` endpoint.

1. **Protocol over inheritance.** `TranscriberProtocol` (structural type) declares `load() / unload() / transcribe()`. Both `Transcriber` (local, unchanged) and `RemoteTranscriber` (new) satisfy it. `StreamingDaemon.__init__` types `transcriber: TranscriberProtocol`. No abstract base class — Python's `Protocol` is enough and avoids forcing `Transcriber` to inherit from anything.

2. **`response_format=verbose_json`** on every request. Whisper.cpp returns per-segment `avg_logprob` and `no_speech_prob`, so confidence is computed via the same `_normalize_logprob(mean(avg_logprob)) = exp(mean) clamped [0,1]` math used by the local backend. The daemon's `min_confidence = 0.30` miss-gate behaves identically across backends.

3. **Full URL in config**, not base + path. `remote_endpoint_url = "http://192.168.4.200:8765/inference"` is one line, supports any whisper-compat server with a different path, and avoids embedding `/inference` in code.

4. **Failure mode: treat as miss.** Any `httpx.TimeoutException`, `ConnectError`, `HTTPStatusError`, malformed JSON, or unparseable response is caught inside `RemoteTranscriber.transcribe()` and returns `TranscriptionResult(text="", confidence=0.0, no_speech_prob=1.0, ...)`. The existing confidence-gate fires `FeedbackSink.on_miss()` and the daemon stays alive — same degradation pattern as `LLMRouter.route() → None` (ADR 0040 spirit).

5. **No auto-fallback to local.** Keeping a `Transcriber` loaded as a backup would defeat the primary motivation (free local GPU) and double VRAM cost. If the remote server is unreachable, the user gets a miss chime; flip the config to `local` to recover.

6. **WAV serialisation via `soundfile.write(BytesIO, ...)` PCM_16 16 kHz mono.** `soundfile` is already a dependency (used elsewhere for the `last_utterance.wav` debug artifact). No new deps.

7. **httpx timeout split** mirrors the LLMRouter pattern (`config.timeout_ms` → `httpx.Timeout(connect=0.5, read=remainder, write=2.0, pool=2.0)`). 5 s default is comfortable for a LAN whisper.cpp call on ≤ 10 s utterances.

## Configuration

```toml
[transcription]
backend = "local"            # "local" | "remote"
model_size = "small.en"      # local-only
device = "cuda"              # local-only
compute_type = "float16"     # local-only
min_confidence = 0.3         # both backends
remote_endpoint_url = ""     # full URL, e.g. http://192.168.4.200:8765/inference
remote_timeout_ms = 5000     # per-request budget
```

`Config.load()` raises `ValueError` if `backend == "remote"` and `remote_endpoint_url == ""`, and if `remote_timeout_ms <= 0`. `backend` must be one of `"local"` / `"remote"`; anything else raises.

## Wire format (whisper.cpp `examples/server`)

```
POST /inference
Content-Type: multipart/form-data
file: <16 kHz mono PCM_16 WAV bytes>
response_format: verbose_json
language: en

→ 200 OK
{
  "text": "...",
  "language": "en",
  "duration": 1.25,
  "segments": [
    {"text": "...", "avg_logprob": -0.1, "no_speech_prob": 0.05}, ...
  ]
}
```

Source: <https://github.com/ggerganov/whisper.cpp/tree/master/examples/server>.

## Alternatives considered

- **Single concrete `Transcriber` with mode flag.** Rejected — would balloon a single class with two unrelated dependency stacks (CUDA + httpx).
- **OpenAI-compatible `/v1/audio/transcriptions` endpoint.** Rejected for v1 — the user's deployment is whisper.cpp's native server. An OpenAI-compat backend can land later as a third `backend = "openai"` value.
- **Auto-fallback (remote → local on failure).** Rejected — defeats the GPU-freeing motivation; doubles VRAM.

## Consequences

- `Transcriber` class is unchanged in API; existing unit tests pass without edit.
- New module surface: `RemoteTranscriber`, `TranscriberProtocol`. `TranscriptionResult` shape preserved.
- `daemon.py` factory branches once at startup; hot-path is unchanged (same `transcribe()` call).
- Web UI's transcription form (`_USER_EDITABLE_SECTIONS["transcription"]`) is extended so users can flip backend / set URL without editing TOML by hand.
- `pyproject.toml` unchanged — `httpx`, `soundfile`, `numpy` already present.

## References

- [ADR 0004](0004-faster-whisper-cuda.md) — local CUDA backend
- [ADR 0040](0040-llm-only-routing-replaces-hybrid.md) — failure-mode precedent (silent degradation to miss)
- whisper.cpp server: <https://github.com/ggerganov/whisper.cpp/tree/master/examples/server>
