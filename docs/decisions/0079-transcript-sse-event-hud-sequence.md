# ADR 0079 — `transcript` SSE Event + Per-Utterance HUD Sequence

**Status:** Accepted
**Date:** 2026-05-09
**Extends:** ADR 0048 (outbound telemetry / EventBus), ADR 0051 (HUD chat-log overlay), ADR 0075 (raw `tool_fired` HUD rendering)

## Context

The HUD overlay showed only `tool_fired.name` and `plan_outcome.miss/error` events. Users had no visibility into what Whisper actually transcribed before the routing decision — only the result was visible. When transcripts went sideways (e.g. "paste" → "P.A.C.T." hallucination), users could not tell whether a miss was caused by a bad transcript, a missing VerbRouter rule, or a dispatcher failure.

Users wanted three things visible per utterance, in order:

1. What was heard (raw Whisper transcript).
2. What got matched / triggered (`tool_fired` name or names).
3. Any error or miss (`plan_outcome` with non-ok status).

Without the transcript line, routing decisions were opaque: the HUD might show `"no match"` with no indication of what word caused the miss.

## Decision

### New SSE event: `transcript`

The daemon publishes a new `transcript` SSE event immediately after transcription gates pass (after `_feedback.on_transcript()` in `daemon.py`). Payload:

```json
{ "text": "<whisper transcript>", "confidence": 0.93 }
```

Published via the existing `_publish("transcript", {...})` helper. `confidence` is the mean token probability from `faster-whisper`'s segment result.

### voice_sprite handling

`plan_outcome_handler.py` gains a `handle_transcript(event)` function. It appends a `ChatLogEntry` with `status="info"` to the chat log. The transcript text is wrapped in curly quotes — `“<text>”` — to visually distinguish transcribed speech from tool names and system messages.

Empty or whitespace-only transcript text is treated as a no-op (no entry appended).

### New `ChatLogStatus` value: `"info"`

`chat_log.py` adds `"info"` to the `ChatLogStatus` `Literal` type. The chat-log renderer (`chat_log_renderer.py`) maps it to `_COLOR_INFO = (180, 200, 230)` — light blue / `#b4c8e6` — distinct from the existing ok (green), miss (orange), and error (red) palette.

### Per-utterance HUD line ordering

For each utterance the HUD now renders, in sequence:

| Order | Event | Color |
|---|---|---|
| 1 | `transcript` — what was heard | light blue (`#b4c8e6`) |
| 2 | `tool_fired` — tool name(s) fired | green (existing ok) |
| 3 | `plan_outcome` miss or error — only if non-ok | orange / red (existing) |

Step 3 is suppressed on successful utterances (no change to existing behaviour).

### Dispatch wiring

`voice_sprite/__main__.py` adds `"transcript"` to the `on_event` SSE dispatch table, routing it to `handle_transcript()`.

## Consequences

- HUD now self-explains routing decisions: users see what was heard and what fired, in utterance order.
- Transcription bugs (Whisper hallucinations, low-confidence drops) become immediately visible without needing to tail daemon logs.
- Provides a hook for future filters — e.g. suppress the transcript line when `confidence >= threshold` to reduce visual noise for power users.
- Cost: minimal — one extra SSE event per utterance (~negligible bandwidth), one extra `ChatLogEntry` per utterance (~3-second fade), no additional LLM calls.
- No change to existing `tool_fired` or `plan_outcome` event shapes.

## Alternatives considered

- **Show transcript only on miss** — rejected. Users want confirmation every time, especially when the transcript-to-command mapping is non-obvious (e.g. a short homophone like "press" vs. "mess").
- **Reuse `tool_fired` with a sentinel name like `_transcript`** — rejected. `tool_fired` is semantically narrow (a tool the dispatcher actually invoked); polluting it with a pseudo-tool name breaks consumers that filter on tool names.
- **Embed transcript in `plan_outcome` payload** — rejected. The `plan_outcome` payload already carries `transcript` as a field, but the goal is to render the transcript line *before* the outcome line, not alongside it after the fact.

## Implementation pointers

- `src/voice_commander/daemon.py` — `_publish("transcript", {"text": ..., "confidence": ...})` after `_feedback.on_transcript()`.
- `src/voice_sprite/plan_outcome_handler.py` — `handle_transcript(event)` function; appends `ChatLogEntry(text="“{text}”", status="info")`.
- `src/voice_sprite/chat_log.py` — `ChatLogStatus` `Literal` adds `"info"`.
- `src/voice_sprite/chat_log_renderer.py` — `_COLOR_INFO = (180, 200, 230)`; `_color_for_status()` branch for `"info"`.
- `src/voice_sprite/__main__.py` — `"transcript"` key in the `on_event` dispatch dict.
- `tests/unit/test_sprite_plan_outcome_handler.py` — `test_transcript_appends_info_entry`, `test_transcript_empty_text_is_noop`.
