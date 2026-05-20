# ADR 0095 — Streaming-Window Dictation

**Status: SUPERSEDED by [ADR 0096](0096-server-side-dictation.md)** — growing-window streaming, LocalAgreement-2, segment-based trimming, and the frame tap have all been deleted. The 2026-05-20 bug-fix amendment that fixed LA-2 reset-on-trim is preserved here for historical record only — that code no longer exists.

**Status:** Accepted
**Date:** 2026-05-19
**Amends:** [ADR 0092](0092-streaming-dictation-integration.md) (transport: per-chunk → whole-window streaming), [ADR 0091](0091-streaming-dictation-experiment.md) (LocalAgreement semantics corrected)
**References:** [ADR 0093](0093-transcription-proxy-endpoint.md), [ADR 0094](0094-consume-done-frame.md)

## Context

ADR 0092 streams dictation by sending each VAD-segmented utterance as a separate
WAV chunk to the `/ws/transcribe` WebSocket. Whisper on the server decodes these
chunks independently. This exposes a fundamental mismatch: Whisper is a 30-second
batch model trained on complete, naturally-segmented audio. Feeding it hard-cut
short clips — sliced wherever silence exceeded the VAD threshold — violates its
training assumptions and produces well-known artefacts:

- **Stray `...` ellipses** emitted when Whisper is unsure how a short clip
  continues; common on trailing silence and short filler words.
- **Broken sentences at pause boundaries** — a sentence that spans a natural
  mid-breath pause is split into two chunks; each half is decoded without the
  other, losing grammatical coherence and punctuation.
- **Boundary word loss** — the last word of one VAD utterance and the first word
  of the next share no decoding context, causing hallucinated or dropped words at
  the join point.

The root cause is that `LocalAgreement` (ADR 0091/0092) was designed to stabilise
words across *consecutive whole-window* hypotheses — it expects each successive
hypothesis to re-decode the same growing audio from time zero, so that the prefix
of the new hypothesis can be compared against the prefix of the previous one. In
the ADR 0092 implementation `LocalAgreement` was instead fed *independent chunk
hypotheses*, which have no prefix overlap and whose text cannot be meaningfully
compared for agreement. The stabilisation logic therefore fired spuriously on
coincidental word matches across unrelated clips, and the per-chunk context reset
erased any continuity benefit.

This ADR replaces the per-chunk streaming transport with a **growing-window
model**: the daemon accumulates all 16 kHz audio from session start into a single
growing buffer and periodically re-encodes the *whole window* as a WAV for the
server to re-decode from the beginning. Whisper sees an ever-longer coherent
utterance, producing consistent punctuation and no stray `...`. `LocalAgreement`
is corrected to operate on consecutive whole-window hypotheses as originally
intended, committing stable prefixes and trimming the buffer at the committed point
to bound memory. VAD is demoted from a transcription chunker to a pure
endpointing detector — it still identifies the end word, cancel word, and silence
timeout, but its segmentation no longer gates transcription.

## Decision

### D1 — Growing-window streaming

`DictationWindow` (`dictation/window.py`) owns a growing 16 kHz float32 buffer fed
by a `StreamingRecorder` frame tap. Every `window_step_ms` (default 1000) of
accumulated audio, `append()` returns the WHOLE current window encoded as a WAV.
The server re-decodes the whole window each time. The buffer is thread-safe (lock
guards VAD-worker append and asyncio commit/flush).

### D2 — LocalAgreement-2

Fed consecutive whole-window hypotheses (each transcribes the same growing audio
from t=0). Commits the longest agreeing prefix of the last two hypotheses; discards
the unstable tail. Words carry absolute-stream end-times (`TimedWord`) so
`DictationWindow.commit` knows where to trim. Pure module — no I/O, no threads.

### D3 — VAD demoted to endpointing only

`VADGate` still detects end-word, cancel-word, and silence; its utterances are no
longer streamed as transcription chunks. `handle_utterance` is classification-only.
No `vad_gate.py` code change needed.

### D4 — `raw_transcript` finalize

`finish()` sends the daemon's full committed text as `raw_transcript` on the `end`
frame via `raw_transcript_fn`; the proxy LLM-cleans it directly (no re-decode).
`done.text` fallback to LocalAgreement output preserved (ADR 0094).

### D5 — Config

New `[dictation]` keys: `window_step_ms` (default 1000), `window_cap_ms` (default
25000).

### D6 — Server contract

Two backward-compatible server additions (implemented in a separate repo):

1. **Segment timestamps on `partial`:** `segments: [{start, end, text}]` — seconds
   relative to the sent WAV.
2. **`raw_transcript` on the `end` frame:** When present, the proxy LLM-cleans this
   text directly instead of re-decoding.

The daemon degrades gracefully when `segments` is absent (OI-1).

### Open-item resolutions

**OI-1 — Graceful degradation when `segments` absent.** When a partial arrives
without segments (un-upgraded server), the daemon falls back to a time-based cap
policy: `window_cap_ms` bounds the buffer; LocalAgreement-2 still stabilises text.
A WARNING is logged once per session.

**OI-2 — Post-trim offset alignment.** `DictationWindow` owns
`committed_offset_s` (total audio-seconds dropped by trims). The session adds this
offset to the server's window-relative segment end-times to get absolute-stream
timestamps. LocalAgreement-2 is fed words with absolute times; committed end-time
is passed back to `DictationWindow.commit`. Single source of truth — no
window-relative bookkeeping leaks into LA-2.

**OI-3 — HUD follows committed prefix.** The `transcript` event carries the
committed prefix (only grows, never rewrites), not the raw hypothesis with its
unstable tail. Stable, honest live view.

## Bug fix — 2026-05-20

**The bug.** `LocalAgreement.commit()` indexes into the hypothesis with
`hypothesis[already:agreed]`, where `already = len(self._committed)` is an
absolute committed-word count and `agreed` is the agreeing-prefix length of the
**current** whole-window hypothesis. This arithmetic is valid only when every
hypothesis is a whole-window decode starting at t=0 of the dictation — exactly
what D1 guarantees. However, `DictationSession._on_partial` called
`window.commit(end_s)` after each LA-2 commit (trimming the audio buffer), and
`_on_frame` called `window.commit(forced)` when the cap was exceeded. After
any trim the server's next hypothesis decodes only the post-trim sub-window — it
does NOT start with the previously-committed words. `prev_words` (from the
pre-trim hypothesis) and `new_words` (from the post-trim hypothesis) no longer
share the committed prefix; `agreed` collapses to 0 or a small number; and the
`hypothesis[already:agreed]` slice is nonsense. In a live test this produced an
11-character paste (`"This is boy"`) instead of the full spoken sentence
(`"This is a dictation test. And boy do we be dictating right now."`).

**The fix.** After every `window.commit(...)` call — whether triggered by LA-2
in `_on_partial` (segments branch) or by cap overflow in `_on_frame` — reset
`self._agreement = LocalAgreement()`. The post-trim window is a fresh
growing-window sub-session; LA-2 must start its prefix-agreement over from
scratch. `self._confirmed` (the session-level word accumulator) is untouched by
the reset and continues to accumulate committed words across resets, so the
global transcript is preserved. The cap-trim reset is taken under
`_agreement_lock` because `_on_partial` (asyncio-loop thread) is the only other
writer.

**Where the fix lives.** `session.py` only — `LocalAgreement`'s contract is
correct as documented; the session was violating it by feeding post-trim
hypotheses without resetting state.

## Consequences

**Positive:**
- Coherent punctuated output — no stray `...` or broken sentences at pause
  boundaries.
- Bounded latency — `window_cap_ms` caps the buffer regardless of dictation length.
- LocalAgreement-2 prefix agreement eliminates hallucinated word carryover.

**Negative:**
- More bytes on the wire — the whole window is re-sent each step (not just the new
  chunk).
- Degraded trim accuracy when `segments` absent (falls back to time-based cap).

**Neutral:**
- No new dependency. `DictationWindow` reuses existing `encode_wav` from
  `store.py`.
- Tool catalogue unchanged at 11 primitives.
