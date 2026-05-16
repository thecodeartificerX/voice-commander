# ADR 0086 — Dictation Mode / Remote Transcription Pipeline

**Status:** Accepted
**Date:** 2026-05-16
**Supersedes:** [ADR 0025](0025-mute-hotkey-for-external-dictation.md) — mute hotkey for external dictation coexistence

## Context

ADR 0025 solved external-dictation crosstalk by closing the audio stream on Right Ctrl (mute toggle). That design's premise was that voice-commander and an *external* dictation app (Windows voice typing) would share the microphone — so the stream had to be silenced while the other app was active.

The new direction eliminates the external app entirely: voice-commander itself becomes the dictation engine. Users say "type" (or press Right Ctrl during a session) to enter a sub-state where subsequent utterances are treated as free-form text rather than commands. The accumulated audio is encoded and sent to a remote whisper.cpp server for high-quality transcription, and the result is inserted at the cursor via a clipboard round-trip.

This makes the mute toggle unnecessary. The `HotkeyConfig.mute_key` field and all stream-close/reopen machinery (§§ 1–3 of ADR 0025) are removed. `ctrl_r` is repurposed as `dictation_key`.

## Decision

### D1 — Dictation is a voice-session sub-state

Dictation mirrors the picker sub-state introduced in ADR 0083. The microphone stays open; the session remains active throughout. Dictation mode is entered and exited without touching `StreamingRecorder` or `HotkeyController` session state.

### D2 — Entry

Two entry paths:
1. **Bare "type" utterance.** `VerbRouter` detects a transcript that normalises to the single token `type` with no argument. It emits a synthetic `__dictation.start` step (analogous to `__picker.open`). `daemon.py` intercepts the step name before calling `Dispatcher` and activates `DictationSession`.
2. **Right Ctrl keypress** (`dictation_key`, default `ctrl_r`) during an active voice session. `HotkeyController` fires `on_dictation_toggle`; if a session is active and dictation is not yet running, `DictationSession` starts.

### D3 — Buffering while active

`DictationSession` receives each completed VAD utterance's raw audio ndarray from `_process_utterance` (which forks away from `VerbRouter` while `_dictation_session` is set). The session accumulates audio chunks in an internal list. No transcription occurs during buffering — the daemon hears each chunk as a waveform only.

The sprite shows a greyed cat with a `● DICTATING` badge (new `dictating` flag on the sprite state machine) while dictation is active.

### D4 — Exit

Two exit paths:
1. **End word.** Any utterance that normalises (lowercase, strip punctuation) to the exact string configured in `[dictation] end_word` (default `done`) triggers exit. Case and trailing punctuation are ignored; multi-word transcripts are not matched (exact standalone only).
2. **Right Ctrl** (`dictation_key`) pressed a second time. `on_dictation_toggle` fires; `DictationSession` is told to finalise.

On exit, `DictationSession` is handed off to `_finalize_dictation`, a background task dispatched on a single-worker `ThreadPoolExecutor` so the pipeline thread is not blocked.

### D5 — Remote transcription

`_finalize_dictation` encodes the concatenated audio chunks to a 16 kHz mono WAV in memory (soundfile / scipy) and POSTs it (multipart form-data, field `file`) to a configurable remote whisper.cpp `/inference` endpoint:

```toml
[dictation]
endpoint = "http://192.168.4.200:8765/inference"   # default
end_word  = "done"
```

The POST uses `verbose_json` response type. The `text` field of the JSON response is the transcription. This is the same wire format as `RemoteTranscriber` (ADR 0073), reused here without that backend toggle.

### D6 — Clipboard round-trip paste

The transcription is inserted at the cursor via a clipboard round-trip:

1. Snapshot the current clipboard contents.
2. Write the transcription text to the clipboard.
3. Send `Ctrl+V` via pyautogui.
4. Restore the original clipboard contents.

This leaves the transcription as the second entry in the Win+V clipboard history (the restored value is the most recent). No new primitive tool is added — the paste is internal to `_finalize_dictation` via `dictation/clipboard.py`. The tool catalogue remains at exactly 11 primitives.

### D7 — One-slot on-disk retention

`_finalize_dictation` writes two files unconditionally:

- `outputs/dictation/last.wav` — the 16 kHz mono WAV (encoded before POST).
- `outputs/dictation/last.txt` — the transcription text (or an error description on failure).

These files are overwritten on every dictation. They are the single retained artefact for post-mortem inspection and web re-transcribe.

### D8 — Web re-transcribe page

`GET /page/dictation` serves a page showing the text of `last.txt` and a **Re-transcribe** button. Clicking the button `POST`s to `/dictation/retranscribe`, which reads `last.wav`, re-sends it to the configured endpoint, writes the new text to `last.txt`, and returns the updated page fragment.

### D9 — Failure handling

If encoding, the remote POST, or the clipboard paste fails:

1. A `dictation.error` SSE event is published (payload: `{reason: "encode" | "endpoint" | "clipboard"}` — one of these three string values depending on which step failed).
2. A miss chime fires (`winsound`).
3. `last.wav` is still written (if encoding succeeded) so the user can retry via `/page/dictation`.

The daemon continues normally; the dictation sub-state is cleaned up regardless of error.

### D10 — Mute toggle removed; `mute_key` renamed `dictation_key`

`HotkeyConfig.mute_key` is renamed `dictation_key`. The `on_mute_toggle` callback and all stream-close/reopen machinery (ADR 0025 §§1–3: `_drain_utt_q()`, the pipeline `_muted` guard, `recorder.close_session()` / `recorder.open_session()` calls in the mute path) are deleted. The generation-counter amendment (ADR 0025 §10) is retained because it guards the scroll-lock-close path, not only the mute path.

The `muted` / `unmuted` EventBus events are **retained** — `on_scroll_lock` still publishes `"muted"` on session close and `"unmuted"` on session open; the sprite still consumes them for grey-out. Only the in-session mute *toggle* (`on_mute_toggle` callback and `_muted` pipeline guard) is removed. The sprite `muted` badge that was tied to the now-deleted mid-session mute state is removed.

## Consequences

### Positive

- Users can dictate free-form text without leaving their voice session or switching to an external app.
- Remote whisper.cpp produces higher accuracy than the local `small.en` model for continuous prose.
- Clipboard round-trip preserves Win+V clipboard history.
- The in-session mute mechanism (and its PortAudio open/close churn) is eliminated entirely.
- `last.wav` + `/page/dictation` give a zero-friction re-transcribe path on endpoint failure.

### Negative

- Dictation requires a reachable whisper.cpp server on the LAN. Endpoint failure emits a miss chime and logs an error; no automatic local fallback.
- Only the most recent dictation is retained on disk (one-slot). Older dictations are overwritten.

### Neutral

- `HotkeyConfig.mute_key` → `dictation_key` is a breaking config rename. Users with `mute_key` set must update `config.toml`. A startup deprecation warning is emitted if the old key is detected.
- `DictationSession` lives under `src/voice_commander/dictation/` alongside `store.py`, `remote.py`, and `clipboard.py`.

## Alternatives considered

1. **Local transcription for dictation** — use the already-loaded `faster-whisper` model. Rejected: the daemon model (`small.en`) is optimised for short command utterances, not multi-sentence prose. The remote whisper.cpp endpoint uses a larger model and returns higher-quality results.
2. **File-based paste (write to temp file, `type` primitive)** — rejected: `type` is keystroke-based and does not handle Unicode well for long passages. Clipboard round-trip is faster and preserves formatting.
3. **Append-slot retention (keep N dictations)** — rejected for now; one-slot keeps the implementation simple and the web page trivial. Can be revisited if needed.
4. **Keep the mute toggle alongside dictation** — rejected: two mechanisms for the same Right Ctrl key would produce confusing state interactions. Dictation mode is a strict superset of what mute provided.

## References

- [ADR 0025](0025-mute-hotkey-for-external-dictation.md) — superseded mute hotkey design
- [ADR 0083](0083-bare-primitive-picker.md) — bare-primitive picker sub-state (pattern mirrored by dictation)
- [ADR 0073](0073-remote-transcription-backend.md) — remote whisper.cpp wire format (reused here)
- [ADR 0085](0085-chain-primitive.md) — `ToolCall.internal` flag used by synthetic `__dictation.start` step
- Implementation: `src/voice_commander/dictation/`, `src/voice_commander/daemon.py`, `src/voice_commander/config.py`, `src/voice_commander/web/app.py` (routes `GET /page/dictation` and `POST /dictation/retranscribe` added inline), `src/voice_commander/web/templates/page_dictation.html`, `src/voice_commander/web/templates/_dictation_result.html`
