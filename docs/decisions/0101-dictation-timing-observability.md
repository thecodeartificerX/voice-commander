# ADR 0101 — Dictation Timing Observability

**Status:** Accepted
**Date:** 2026-05-24
**Extends:** [ADR 0096](0096-server-side-dictation.md) (server-side dictation transport) and [ADR 0094](0094-consume-done-frame.md) (`done`-frame contract)

## Context

After the ADR 0096 pivot the daemon became a thin transport: it sends raw PCM to
the VPS, awaits a single `done` frame, post-processes the text, and pastes.
During that round-trip the user sees only the sprite's "processing" badge.
There is no instrumentation to answer questions such as:

- How long does Whisper actually take on the server vs. the AI cleanup step?
- How much of the perceived latency is network vs. compute?
- Are the regex `structural_format()` or the clipboard paste slow on large dictations?

Without per-phase numbers, performance tuning is guesswork, regressions are
invisible, and bug reports about "slow dictation" cannot be triaged.

What is needed is a lightweight, server-side + client-side timing record that
covers every phase the user waits through — audio-capture-to-transcript,
AI cleanup, network round-trip, post-process, and paste — and surfaces these
numbers on the `/page/dictation` web surface without adding any new config keys
or dependencies.

## Decision

### D1 — Server `done` frame gains an optional `timings` key

The server adds one optional key to the existing `done` frame.  All values are
**float milliseconds** measured with `time.monotonic()` on the server.  The
key is **additive** (old servers that omit it still interoperate):

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
| `transcribe_ms` | Wall time of the `WhisperModel.transcribe()` call alone (before AI cleanup). |
| `clean_ms` | Wall time of the `clean_transcript()` LLM round-trip. |
| `format_ms` | Wall time of `structural_format()` regex post-processing. `0.0` when the pipeline has no structural-format step (current VPS server); the panel omits the row + legend dot when `0`. |
| `server_total_ms` | Total from receiving `{"type":"end"}` to just before sending the `done` frame (≥ `transcribe_ms + clean_ms + format_ms`; includes any internal overhead). |

**Monotonic-clock rule:** all four fields are measured with `time.monotonic()`
on the server.  There is no client/server clock synchronisation requirement
because these fields are purely server-internal durations.

The `timings` key is absent (not null) on servers that do not implement it.
The daemon reads it via `.get("timings")` and degrades gracefully.

### D2 — `ws_client.stream_transcribe` returns a `TranscribeResult` dataclass

`stream_transcribe` now returns a `TranscribeResult` instead of `str | None`
(behaviour on failure: `None` unchanged):

```python
@dataclass
class TranscribeResult:
    text: str               # done.text (may be empty string)
    server_timings: dict    # done.timings or {} when absent
    roundtrip_ms: float     # wall time: send {"type":"end"} → receive done frame
```

`roundtrip_ms` is measured on the asyncio-loop thread via `time.monotonic()`:
the clock starts **immediately after** the `{"type":"end"}` frame is written to
the WebSocket and stops when the `done` frame is fully received.

The `finish()` signature of `DictationSession` is **unchanged** — it still
returns `str | None`.  The timing data is surfaced through a new accessor:

```python
DictationSession.get_timings() -> dict | None
```

Call `get_timings()` after `finish()` returns; the `loop_thread.join()` inside
`finish()` is the happens-before barrier (no extra lock needed, same rationale
as the `_final_text` field in ADR 0094).

### D3 — Locked timing record schema

`daemon._finalize_dictation` measures two additional phases on the daemon side:

- **`postprocess_ms`** — wall time of `apply_corrections()` + `apply_commands()`
  (the custom-vocabulary post-processing from ADR 0088).
- **`paste_ms`** — wall time of `clipboard.paste_via_clipboard()` (the Ctrl+V
  clipboard round-trip).

It then calls the new pure helper
`voice_commander.dictation.timing.build_timing_record(...)` which assembles and
returns a record matching this locked schema:

```python
{
    "ts":               float,        # time.time() at record creation
    "chars":            int,          # len(final_text)
    "server": {
        # copied verbatim from done.timings, or {} when absent
        "transcribe_ms":   float | None,
        "clean_ms":        float | None,
        "format_ms":       float | None,
        "server_total_ms": float | None,
    },
    "roundtrip_ms":     float | None, # from TranscribeResult; None when absent
    "network_ms":       float | None, # see D4 — None unless both present
    "postprocess_ms":   float,        # daemon-side; always present
    "paste_ms":         float,        # daemon-side; always present
    "total_ms":         float,        # roundtrip_ms + postprocess_ms + paste_ms
                                      # (or server_total_ms + postprocess_ms + paste_ms
                                      #  when roundtrip_ms is absent)
}
```

### D4 — `network_ms` honesty caveat

`network_ms` is computed as `roundtrip_ms − server_total_ms` when both are
present and `roundtrip_ms ≥ server_total_ms`, otherwise `None`.

**This value is NOT the pure return-leg latency.**  It is:
`network_ms ≈ outbound leg + inbound leg + server scheduling overhead`

There is no client/server clock synchronisation, so the two individual legs
are not separable.  The field is labelled "network/transport" in the UI and
described honestly as "round-trip minus server compute" — it is an upper bound
on network overhead, not an exact measure of either direction.

### D5 — Persistence: `outputs/dictation/last_timings.json`

`DictationStore` gains two methods:

```python
DictationStore.save_timings(record: dict) -> None
DictationStore.read_timings() -> dict | None
```

`_finalize_dictation` calls `save_timings` with the assembled record.  This
persists the last dictation's timing data alongside the existing `last.txt`.
The file is overwritten on every dictation, exactly like `last.txt`.

### D6 — `dictation.timings` EventBus event

After saving, `_finalize_dictation` publishes:

```
dictation.timings  <timing record JSON>
```

on the EventBus (and therefore the SSE `/events` stream).  The event fires on
the normal finalization path only — not on the cancel path.

### D7 — `/page/dictation` timing panel

The dictation page gains a timing breakdown panel rendered as the HTMX partial
`_dictation_timing.html` (element id `dictation-timing`), positioned directly
under the existing "last dictation" text box.

New route:

```
GET /page/dictation/timing  →  _dictation_timing.html  (reads last_timings.json)
```

The panel live-refreshes via the existing `/events` SSE stream and HTMX's
`reloadSection` pattern listening on the `dictation.timings` event — identical
to how the main dictation page already refreshes on `dictation.end`.

The panel displays the four server phases (Whisper, AI cleanup, format, server
total), `roundtrip_ms`, `network_ms` (labelled "network/transport, round-trip
minus server compute"), `postprocess_ms`, `paste_ms`, and `total_ms` as a
simple breakdown table.  When `timings` is absent from the server (older
server), the server cells render as "—".

### D8 — No new config key. No new dependency.

This feature adds no `[dictation]` config key and no new Python package.
`time.monotonic()` is stdlib.  The `dataclasses` module is already used
throughout the codebase.

## Consequences

### Positive

- **Per-phase latency is visible** without attaching a profiler.  A user who
  reports "dictation is slow" can open `/page/dictation` and immediately see
  whether the bottleneck is Whisper, the LLM cleanup, the network, or the paste.
- **Backward compatible end-to-end.** Old server (no `timings`) → daemon reads
  `.get("timings")` → `{}` → server cells show "—"; no behavioural change.
  Old daemon (no `dictation.timings`) → page remains static; no regression.
- **Minimal surface change.** `DictationSession.finish()` signature is
  unchanged.  Callers that only care about the text are unaffected.
- **`roundtrip_ms` is always measurable** even when the server omits `timings`,
  giving a useful floor observation without requiring a server upgrade.

### Negative

- **`network_ms` is approximate.** It captures both network legs plus scheduling
  jitter, not the return leg alone.  Documented honestly; see D4.
- **`total_ms` is not wall-clock dictation latency.** It covers only the
  post-end-frame window (Whisper + LLM + paste).  The audio capture and
  upload time are not included — they are the pre-end-frame period and would
  require a different instrumentation point.

### Neutral

- `TranscribeResult` is a new return type for `stream_transcribe`.  `DictationSession._async_main`
  is the only caller; no other code path is affected.
- `build_timing_record` is a pure helper (no I/O, no side effects); unit-testable
  in isolation.
- The `dictation.timings` SSE event is ignored by the sprite and does not affect
  any existing sprite state machine transitions.

## Alternatives rejected

**(a) Server-push only (no client-side measurement).** Let the server include
all fields in `timings` and skip client-side measurement.  Rejected because
`roundtrip_ms` and `paste_ms` are not observable server-side, and `postprocess_ms`
(custom-vocabulary correction) runs on the daemon, not the server.

**(b) Separate `/stats` WebSocket feed.** Push timing data over a secondary
channel.  Rejected — unnecessary complexity; the single `dictation.timings`
EventBus event through the existing SSE stream is sufficient.

**(c) Persistent ring buffer (multiple dictation history).** Store the last N
records.  Rejected for now — a single `last_timings.json` matches the existing
`last.txt` precedent and is sufficient for day-to-day tuning.  A ring buffer
can be added later without an ADR (no architectural change needed).

## References

- [ADR 0096](0096-server-side-dictation.md) — server-side dictation transport (extended here)
- [ADR 0094](0094-consume-done-frame.md) — `done`-frame contract (extended here)
- [ADR 0088](0088-dictation-custom-vocabulary.md) — `apply_corrections`/`apply_commands` (timed by D3)
- [ADR 0089](0089-dictation-hotkey-sentinel-cancel-debounce.md) — cancel path (timing event skipped on cancel)
- `docs/references/ws-transcribe-server.md` — server frame protocol (updated)
- `src/voice_commander/dictation/ws_client.py` — `TranscribeResult`, `stream_transcribe`
- `src/voice_commander/dictation/session.py` — `DictationSession.get_timings()`
- `src/voice_commander/dictation/timing.py` — `build_timing_record` pure helper
- `src/voice_commander/dictation/store.py` — `DictationStore.save_timings` / `read_timings`
- `src/voice_commander/daemon.py` — `_finalize_dictation` timing capture + publish
- `src/voice_commander/web/routes/dictation.py` — `GET /page/dictation/timing` route
- `src/voice_commander/web/templates/_dictation_timing.html` — timing panel partial
- `outputs/dictation/last_timings.json` — persisted last-dictation timing record
