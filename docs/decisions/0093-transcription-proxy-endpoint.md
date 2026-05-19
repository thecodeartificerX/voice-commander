# ADR 0093 — Transcription Proxy Endpoint (LLM Cleanup)

**Status:** Accepted
**Date:** 2026-05-19
**Amends:** [ADR 0092](0092-streaming-dictation-integration.md) — transcription transport endpoint only; protocol, transport, and lifecycle wiring unchanged

## Context

ADR 0092 pointed streaming dictation at a raw whisper WebSocket server at
`ws://192.168.4.200:8765/ws/transcribe`. That server returns verbatim whisper
output: the stabilised transcript carries whisper's raw casing, punctuation, and
disfluencies, and the trailing end-word region is whatever whisper heard.

A new LAN service has been stood up at port **8767**. It is a **WebSocket proxy**
in front of the same whisper backend:

- It speaks the **same `/ws/transcribe` WebSocket contract** as the `:8765`
  server — same config frame (`{language, initial_prompt}`), same audio-chunk
  frames, same `partial` / `done` / `error` reply shapes. The daemon's
  `ws_client.stream_transcribe` needs no protocol change.
- **Partial frames pass through unchanged** — `LocalAgreement` word stabilisation
  and the live HUD transcript feedback continue to work exactly as before.
- **The final `done` transcript is LLM-cleaned** by the proxy before it reaches
  the voice commander (punctuation/casing normalisation, disfluency removal).
- If the LLM is temporarily unavailable, the proxy **silently falls back to the
  raw whisper text** — dictation still completes, just without the cleanup pass.

## Decision

Change the `[dictation] ws_url` default from
`ws://192.168.4.200:8765/ws/transcribe` to
`ws://192.168.4.200:8767/ws/transcribe`.

This is a **one-key config change**. Because the proxy is protocol-compatible
with the raw whisper server, no caller-side code changes beyond the
`DictationConfig.ws_url` default. The cleanup logic lives entirely server-side.

### Why a server-side proxy, not a daemon-side LLM step

ADR 0091 D1 left a `transform()` seam in the streaming pipeline for a future
LLM cleanup step. A server-side proxy is preferred over filling that seam:

- Keeps the LLM dependency and its latency **off the daemon** — voice-commander
  stays local-first, with no model/service to manage in-process.
- The proxy's **silent raw-text fallback** means an unavailable LLM degrades
  gracefully without daemon-side error handling.
- Command routing (VerbRouter, primitives) remains fully local and offline —
  only dictation transcripts traverse the proxy, exactly as before.

## Consequences

### Positive

- Dictation transcripts arrive punctuated and cleaned without a daemon-side LLM
  dependency.
- Zero protocol or code change — only the config default moved.
- Partials are untouched, so live HUD transcript feedback is unaffected.

### Negative

- Dictation now depends on the proxy at `:8767` instead of the whisper server at
  `:8765`. If the proxy is down, dictation fails with a miss chime — the same
  failure mode as ADR 0092 D8 (no batch fallback). The daemon no longer contacts
  `:8765` directly.

### Neutral

- No new dependency; ADR 0092's error handling, events, and chimes are unchanged.
- The `transform()` seam noted in ADR 0091 D1 stays unused — cleanup is now a
  proxy responsibility, not a daemon one.

## References

- [ADR 0092](0092-streaming-dictation-integration.md) — streaming dictation integration (endpoint amended here)
- [ADR 0091](0091-streaming-dictation-experiment.md) — streaming experiment (`transform()` seam)
- `src/voice_commander/config.py` — `DictationConfig.ws_url` default
- `config.toml.example` — `[dictation] ws_url`
- `docs/dictation-streaming.md` — streaming dictation overview
- `docs/transcription-pipeline.md` — transcription pipeline reference
