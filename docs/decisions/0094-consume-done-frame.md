# ADR 0094 — Consume the `done` Frame for LLM-Cleaned Transcripts

**Status:** Accepted
**Reaffirmed by [ADR 0096](0096-server-side-dictation.md)** — the daemon still consumes the proxy's single `done` frame to obtain the LLM-cleaned final transcript. The end-frame `raw_transcript` field is no longer sent (server now does the only decode), but the done-frame contract is unchanged.
**Date:** 2026-05-19
**Amends:** [ADR 0093](0093-transcription-proxy-endpoint.md) — corrects the "zero code change" claim; protocol and endpoint unchanged

## Context

ADR 0093 introduced a WebSocket proxy at `:8767` that LLM-cleans the final
transcript and returns it in a `done` frame: `{"type":"done","text":"<LLM-cleaned>","raw":"<raw-whisper>"}`.
The ADR claimed "zero protocol or code change" on the daemon side.

That claim was **incorrect**. The daemon's `stream_transcribe` sent the terminal
`{"type":"end"}` frame and then returned without reading any further frames.
The proxy's `done` frame was silently discarded. The daemon pasted the raw
`LocalAgreement`-stabilised whisper text — identical to the pre-proxy behaviour.

## Confirmed `done`-frame contract (from proxy author)

- After the client sends `{"type":"end"}`, the proxy emits **exactly one** `done`
  frame.
- Sent once, NOT per-chunk. Latency = whisper processing + 1 LLM round-trip;
  sub-second expected; bounded defensively by `done_timeout_s` (default 15 s).
- The cleaned text lives ONLY in `done.text`. Partial frames remain raw whisper.
- The `raw` field is optional — the raw `:8765` server omits it; treat as optional.
- Empty `done.text` is valid ("whisper heard nothing") — maps to an empty
  transcript, which the daemon handles gracefully. No special-case branch needed.

## Decision

### `ws_client.stream_transcribe` — read the `done` frame

After sending `{"type":"end"}`, `stream_transcribe` now loops reading frames with
`asyncio.wait_for(ws.recv(), timeout=done_timeout_s)`:

- **`done` frame received** → return `frame["text"]` (the LLM-cleaned transcript,
  possibly an empty string).
- **Trailing `partial` frame** → forward through `on_partial` (defensive; proxy
  guarantees only one `done` follows, but a stray partial must not break the
  client) and continue looping.
- **`error` frame** → log and return `None`.
- **`asyncio.TimeoutError`** → log a warning and return `None`.
- **`ConnectionClosed`** → log a warning and return `None`.

New parameter: `done_timeout_s: float = 15.0`.
New return type: `str | None` (was `None`).

### `DictationSession` — store and prefer `done.text`

- New instance field `_final_text: str | None = None`, reset in `start()`.
- `_async_main` captures `stream_transcribe`'s return value:
  `self._final_text = await stream_transcribe(...)`.
  Written by the asyncio-loop thread; read by `finish()` after
  `loop_thread.join()` — the join provides the happens-before edge; no lock
  needed (same rationale as the existing `self.error` field).
- `finish()` decision after the join:
  - **`_final_text is not None`** → return `_final_text` (the proxy's
    LLM-cleaned `done.text`, even if empty string).
  - **`_final_text is None`** (no `done` frame — timeout, closed, error) →
    fall back to the `LocalAgreement`-stabilised transcript.
  `LocalAgreement.finalize()` is always called so the fallback path has the
  complete accumulated transcript available.

### Why `None` as the "no done frame" sentinel

`done.text` can be a valid empty string (whisper heard nothing). Using `None`
as the sentinel correctly distinguishes "done frame arrived with empty text"
from "no done frame arrived at all", ensuring the fallback path is not
incorrectly triggered on a legitimately silent recording.

### `cancel()` semantics unchanged

`cancel()` discards the transcript regardless; `_final_text` is irrelevant on
the cancel path and is reset on the next `start()`.

## Consequences

### Positive

- Dictation transcripts now receive the LLM cleanup pass the proxy was
  designed to provide — punctuated, cased, and disfluency-free text is
  actually pasted at the cursor.
- The fallback path (`LocalAgreement` output) preserves the pre-0093 behaviour
  when the proxy is down, the LLM is unavailable, or the `done` frame is
  delayed beyond `done_timeout_s`.
- No daemon restart required; no config key changes.

### Negative

- A new 15 s `done_timeout_s` deadline is added to the hot finalization path.
  In the normal case the `done` frame arrives in well under 1 s; the 15 s
  ceiling is a safety bound, not an expected latency.
- If the proxy drops the connection before sending `done`, finalization falls
  back to `LocalAgreement` (prior behaviour) rather than failing with an error.

### Neutral

- `ws_client.stream_transcribe` signature changes (`done_timeout_s` param,
  return type `str | None`). `DictationSession._async_main` is the only caller;
  no other code is affected.
- `LocalAgreement` accumulation continues unchanged; its output remains
  available as a fallback on every call to `finish()`.

## References

- [ADR 0093](0093-transcription-proxy-endpoint.md) — introduced the proxy endpoint (amended here)
- [ADR 0092](0092-streaming-dictation-integration.md) — streaming dictation integration
- `src/voice_commander/dictation/ws_client.py` — `stream_transcribe` (done-frame read)
- `src/voice_commander/dictation/session.py` — `DictationSession._final_text`, `finish()`
- `tests/unit/test_dictation_session.py` — done-frame and fallback test cases
- `docs/transcription-pipeline.md` — transcription pipeline reference (updated)
- `docs/dictation-streaming.md` — streaming dictation overview (updated)
