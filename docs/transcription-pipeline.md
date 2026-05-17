# Voice Commander — Transcription Pipeline (End-to-End)

**Date:** 2026-05-17
**Status:** Authoritative
**Scope:** Both transcription paths — command (local faster-whisper) and dictation (remote whisper.cpp).

This document is the single narrative reference for how audio moves from the microphone to text in Voice Commander. Everything here is grounded in the actual source code and ADRs; there is no speculation. Use the symbol names and file paths below to navigate the code directly.

---

## 1. Overview — Two Paths, One Audio Capture Stack

Voice Commander has two transcription paths that share the same audio capture infrastructure (mic → VAD) but diverge at the point where a complete utterance is handed off:

| Path | Transcriber | Trigger | Output |
|---|---|---|---|
| **Command** | `Transcriber` (local faster-whisper, CUDA) | Every VAD utterance during a normal session | Text → `VerbRouter` → `Dispatcher` |
| **Dictation** | Remote whisper.cpp server (`DictationRemoteError`) | Session enters dictation sub-state; accumulated audio is POSTed on exit | Text → clipboard paste → cursor |

Both paths share:
- `StreamingRecorder` — owns the `sd.InputStream` + VAD worker thread
- `_utt_q` — the `queue.Queue` bridging the VAD worker and the pipeline worker
- `StreamingDaemon._process_utterance()` — the shared hot-path entry point
- `_audio_gen` — the stale-utterance generation guard (ADR 0025 §10)

The fork happens inside `_process_utterance()` after transcription: if `_dictation_session.active` is `True`, the utterance is handed to `DictationSession`; otherwise it flows to the command routing chain.

---

## 2. Shared: Audio Capture and VAD Segmentation

### 2.1 Thread topology

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  StreamingRecorder                                                        │
 │                                                                           │
 │  PortAudio callback thread          VAD worker thread                    │
 │  ┌──────────────────────┐  raw_q   ┌──────────────────────────────────┐ │
 │  │ sd.InputStream (RT)  │─────────▶│ Resampler (soxr) 48k→16k        │ │
 │  │ indata.copy()        │ float32  │ VADGate (silero-vad / onnxruntime│ │
 │  │ raw_q.put_nowait()   │ chunks   │ 512-sample frames)               │ │
 │  └──────────────────────┘          │ → complete utterance ndarray     │ │
 │                                    └──────────────┬───────────────────┘ │
 └──────────────────────────────────────────────────│────────────────────────┘
                                                     │ utterance_sink(_on_utterance)
                                                     ▼
                              _utt_q  (queue.Queue, maxsize=8)
                                                     │
                                                     ▼
                              vc-pipeline thread  (_pipeline_loop)
```

**Key invariant:** The PortAudio callback thread does only `indata.copy()` + `raw_q.put_nowait()` — no blocking, no heavy computation. The VAD worker thread owns resampling and speech-boundary detection. The pipeline worker thread owns all transcription and routing logic.

### 2.2 Session model

Scroll Lock opens a voice session (`on_scroll_lock()` → `recorder.open_session()`). A second Scroll Lock closes it. While a session is open the audio stream runs continuously; silero-vad auto-segments on natural silence. No keypresses between commands are needed.

Each session open/close bumps `_audio_gen` (an int on `StreamingDaemon`). Each enqueued utterance is tagged with the generation at enqueue time. The pipeline worker drops utterances whose generation does not match `_audio_gen`, protecting against stale audio from a session that has already closed (ADR 0025 §10).

### 2.3 Utterance enqueue (`_on_utterance`)

- Called on the **VAD worker thread** inside `StreamingRecorder`.
- Snapshots `_audio_gen`, calls `_utt_q.put_nowait((utterance, gen))`.
- On `queue.Full`: logs a warning, calls `feedback.on_miss()`, drops the utterance.
- File: `src/voice_commander/daemon.py` — `StreamingDaemon._on_utterance()`

### 2.4 Pipeline loop (`_pipeline_loop`)

Runs forever on the **`vc-pipeline` thread**. Drains `_utt_q` and calls `_process_utterance()` for each item. Two behaviours:

- **Normal:** `_utt_q.get(timeout=None)` — blocks indefinitely until an utterance arrives.
- **Dictation hotkey-end pending:** `_utt_q.get(timeout=0.25)` — if no new utterance arrives within 250 ms, the queue is considered drained and `_finalize_pending_dictation_end()` is called (see §4.3).

A `None` sentinel in the queue signals the pipeline thread to exit (used during daemon shutdown).

File: `src/voice_commander/daemon.py` — `StreamingDaemon._pipeline_loop()`

---

## 3. Path 1 — Command Transcription (Local faster-whisper)

### 3.1 ASCII flow

```
  VAD worker thread                    vc-pipeline thread
  ─────────────────                    ──────────────────
  _on_utterance(ndarray)
  → _utt_q.put_nowait((audio, gen))
                                       _pipeline_loop()
                                       → _process_utterance(audio, gen)
                                           │
                                           ├─ async WAV write (wav_executor)
                                           │    outputs/last_utterance.wav
                                           │
                                           ├─ pre-transcribe gen check
                                           │    (drop if stale)
                                           │
                                           ├─ _transcriber_ready.wait(30s)
                                           │
                                           ├─ Transcriber.transcribe(ndarray)
                                           │    faster-whisper / WhisperModel
                                           │    → TranscriptionResult
                                           │         .text, .confidence,
                                           │         .no_speech_prob
                                           │
                                           ├─ post-transcribe gen check
                                           │    (drop if stale)
                                           │
                                           ├─ publish "transcript" SSE event
                                           │    {text, confidence}
                                           │
                                           ├─ [dictation active?] → Path 2
                                           │
                                           ├─ Gate: word_count < min_word_count
                                           │    → silent drop
                                           │
                                           ├─ Gate: no_speech_prob > max
                                           │    → silent drop
                                           │
                                           ├─ Gate: confidence < min_confidence
                                           │    → miss chime + plan_outcome(miss)
                                           │
                                           ├─ VerbRouter.route(text)
                                           │    → Plan | None
                                           │
                                           ├─ None → miss chime + plan_outcome(miss)
                                           │
                                           └─ Dispatcher.run_plan(text, plan, registry)
                                                → plan_outcome(ok | error)
```

### 3.2 Step-by-step

**Step 1 — Audio capture → VAD utterance**

`StreamingRecorder` runs a PortAudio `sd.InputStream`. The PortAudio callback copies each chunk into `raw_q`. The VAD worker drains `raw_q`, resamples via `soxr` (device-native rate → 16 kHz float32), and feeds 512-sample frames to `VADGate` (silero-vad + onnxruntime). When silero-vad detects end-of-speech, `VADGate` emits a complete utterance ndarray (shape `(N,)`, dtype `float32`, 16 kHz).

Relevant ADRs: ADR 0015 (VAD streaming mode), ADR 0016 (silero-vad), ADR 0017 (soxr resampler).

**Step 2 — Enqueue with generation tag**

`_on_utterance(utterance)` is called on the VAD worker thread. It snapshots `_audio_gen` and enqueues `(utterance, gen)` on `_utt_q` (maxsize 8). File: `daemon.py:_on_utterance`.

**Step 3 — Async debug WAV write**

Before transcribing, `_write_utterance_async()` submits a fire-and-forget write of the raw ndarray to `outputs/last_utterance.wav` via `_wav_executor` (single-worker `ThreadPoolExecutor`, thread prefix `wav-writer`). This is best-effort: failures are logged and discarded. It does not affect the transcription path. ADR 0018.

**Step 4 — Generation checks**

Two generation checks bookend the transcribe call:
1. Pre-transcribe: if `gen != _audio_gen`, drop immediately (saves GPU cost).
2. Post-transcribe: if `gen != _audio_gen`, drop silently (catches the race where `close_session()` completed while `transcribe()` was running).

ADR 0025 §10.

**Step 5 — Transcription (`Transcriber.transcribe`)**

`Transcriber` wraps `faster-whisper`'s `WhisperModel` (CTranslate2 backend, `device="cuda"`, `compute_type="float16"`, model `small.en`). The utterance ndarray is passed **directly** — no WAV file is written on the critical path (ADR 0018). When an ndarray is passed, faster-whisper skips its internal ffmpeg decode step.

```python
# src/voice_commander/transcriber.py
segments_iter, info = self._model.transcribe(
    utterance_f32,   # np.ndarray shape (N,), dtype float32, 16 kHz
    language="en",
    beam_size=5,
    vad_filter=False,  # VAD already done upstream; skip it inside Whisper
)
```

`vad_filter=False` when an ndarray is passed (the VAD gate has already segmented the audio). `vad_filter=True` only when a WAV path is passed (e.g. from a test or the single-shot legacy path).

Confidence is computed as `exp(mean(avg_logprob))` clamped to `[0, 1]`, matching the normalisation formula in `_normalize_logprob()`.

Returns: `TranscriptionResult(text, language, duration_ms, confidence, no_speech_prob)`.

ADR: 0004 (faster-whisper CUDA), ADR 0018 (ndarray handoff).

**Step 6 — transcript SSE event**

Immediately after transcription, regardless of downstream routing, the daemon publishes:

```python
self._publish("transcript", {"text": result.text, "confidence": result.confidence})
```

The voice_sprite HUD consumes this event and renders it in light blue (`#b4c8e6`) as the first line of the per-utterance sequence (transcript → tool_fired → plan_outcome). ADR 0079.

**Step 7 — Gate chain**

Three gates filter low-quality transcripts before routing:

| Gate | Condition | Effect |
|---|---|---|
| word-count | `len(text.split()) < min_word_count` (default 1) | Silent drop — no `plan_outcome`, no chime |
| no_speech_prob | `no_speech_prob > max_no_speech_prob` (default 0.6) | Silent drop |
| confidence | `confidence < min_confidence` (default 0.30) | Miss chime + `plan_outcome(miss)` SSE event |

The first two gates treat the utterance as infrastructure noise (e.g. brief silence artifacts); the confidence gate is user-visible.

**Step 8 — VerbRouter**

`VerbRouter.route(text)` matches the transcript against:
1. Registered command/workflow names and synonyms (longest-token-count match wins; underscore→space, punctuation-stripped, case-insensitive).
2. Fallback primitive verb rules (click/scroll/focus/open/type/press/wait).

Returns `Plan | None`. `None` fires a miss chime + `plan_outcome(miss)`.

**Step 9 — Dispatcher**

`Dispatcher.run_plan(transcript, plan, registry)` executes each step sequentially. It publishes `tool_fired` and `plan_outcome` SSE events. File: `src/voice_commander/dispatcher.py`.

### 3.3 Config keys

```toml
[transcription]
backend         = "local"      # "local" | "remote" (ADR 0073)
model_size      = "small.en"
device          = "cuda"
compute_type    = "float16"
min_confidence  = 0.3          # confidence gate threshold
remote_endpoint_url = ""       # only used when backend = "remote"
remote_timeout_ms   = 5000
```

### 3.4 Modules involved

| Symbol | File |
|---|---|
| `Transcriber` | `src/voice_commander/transcriber.py` |
| `TranscriptionResult` | `src/voice_commander/transcriber.py` |
| `_normalize_logprob()` | `src/voice_commander/transcriber.py` |
| `StreamingDaemon._process_utterance()` | `src/voice_commander/daemon.py` |
| `StreamingDaemon._on_utterance()` | `src/voice_commander/daemon.py` |
| `StreamingDaemon._pipeline_loop()` | `src/voice_commander/daemon.py` |
| `StreamingDaemon._write_utterance_async()` | `src/voice_commander/daemon.py` |
| `VerbRouter` | `src/voice_commander/verb_router.py` |
| `Dispatcher` | `src/voice_commander/dispatcher.py` |

### 3.5 Governing ADRs

| Decision | ADR |
|---|---|
| faster-whisper on CUDA, `small.en`, `float16` | ADR 0004 |
| ndarray direct handoff (no temp WAV on hot path) | ADR 0018 |
| Remote transcription backend toggle | ADR 0073 |
| `transcript` SSE event + HUD sequence | ADR 0079 |
| Stale-generation guard | ADR 0025 §10 |
| `TranscriberProtocol` structural type | ADR 0073 |

### 3.6 Model lifecycle

`Transcriber.load()` is called on a background daemon thread named `vc-transcriber-load` at daemon startup. The pipeline worker blocks on `_transcriber_ready` (a `threading.Event`) for up to 30 seconds before its first `transcribe()` call. This allows the web UI and hotkey listener to be available immediately while the model loads (CTranslate2 + CUDA init can take several seconds on first run). On load failure the daemon stays alive but drops all utterances with a miss chime.

CUDA DLL preloading happens at `transcriber.py` import time via `_cuda_setup.register()` — see `docs/gotchas.md §2` for the full Windows DLL loading trap.

---

## 4. Path 2 — Dictation Mode (Remote whisper.cpp)

### 4.1 ASCII flow

```
  Hotkey thread / pipeline thread        dictation executor thread
  ───────────────────────────────        ──────────────────────────
  [Entry: "dictate" utterance]
  VerbRouter → __dictation.start
  daemon intercepts → DictationSession.start()
      publishes "dictation.start"
      sprite shows ● DICTATING badge

  [OR: Right Ctrl keypress]
  on_dictation_toggle()
  → DictationSession.start()

  ──── dictation active ────────────────────────────────────────────

  Each VAD utterance → _process_utterance()
      Transcriber.transcribe(ndarray)         [local faster-whisper]
      publish "transcript" SSE
      DictationSession.handle_utterance(audio, text)
        → "buffered": audio appended to _buffer; loop continues
        → "end"    : DictationSession.take_and_finish()
                      submit _finalize_dictation(audio) to dictation executor

  [OR: Right Ctrl pressed again]
  on_dictation_toggle()
  → DictationSession.request_end()            [hotkey thread]
      sets _pending_end Event

  vc-pipeline drains with 250ms timeout
  → _finalize_pending_dictation_end()
      DictationSession.take_and_finish()      [pipeline thread, atomic]
      submit _finalize_dictation(audio)

  ──── finalization ───────────────────────────────────────────────────
                                              _finalize_dictation(audio)
                                              [dictation executor thread]
                                                │
                                                ├─ encode_wav(audio)
                                                │    float32 → PCM_16 WAV bytes
                                                │    DictationStore.save_audio()
                                                │    outputs/dictation/last.wav
                                                │
                                                ├─ remote.post_audio(wav_bytes)
                                                │    httpx POST multipart/form-data
                                                │    field: file=audio.wav
                                                │    field: response_format=json
                                                │    → JSON {"text": "..."}
                                                │    → text (whitespace-collapsed)
                                                │
                                                ├─ DictationStore.save_text(text)
                                                │    outputs/dictation/last.txt
                                                │
                                                ├─ clipboard.paste_via_clipboard(text)
                                                │    snapshot clipboard
                                                │    set clipboard to text
                                                │    pyautogui Ctrl+V
                                                │    restore original clipboard
                                                │
                                                └─ publish "transcript" SSE
                                                   publish "dictation.result" SSE
```

### 4.2 Entry paths (D2)

**Path A — Spoken "dictate"**

`VerbRouter` detects a transcript that normalises to the single token `dictate`. It returns a synthetic plan containing a single step named `__dictation.start`. `_process_utterance()` intercepts this step name before calling `Dispatcher` and calls `DictationSession.start()` directly. The trigger word is `dictate` rather than `type` because Whisper consistently mishears "type" as "tight"; the `type` verb remains reserved for keystroke-based literal text entry.

**Path B — Right Ctrl hotkey**

`HotkeyController` fires `on_dictation_toggle()` on the pynput listener thread. If `_session_active` is `True` and `_dictation_session.active` is `False`, `DictationSession.start()` is called.

`DictationSession.start()` sets `_active = True`, clears `_buffer`, and publishes `dictation.start` on the EventBus.

### 4.3 Audio buffering (D3)

While `_dictation_session.active` is `True`, `_process_utterance()` branches before the word-count gate:

```python
# daemon.py ~line 591
if self._dictation_session is not None and self._dictation_session.active:
    kind = self._dictation_session.handle_utterance(utterance, result.text)
    if kind == "end":
        audio = self._dictation_session.take_and_finish()
        if audio is not None:
            self._dictation_executor.submit(self._finalize_dictation, audio)
    run.set_status("ok")
    return
```

Each utterance is still transcribed by local faster-whisper (so the end word can be detected and the `transcript` SSE event published), but the audio ndarray is passed to `DictationSession.handle_utterance()` for buffering decisions.

`DictationSession.handle_utterance(audio, text)`:
- Normalises the text (lowercase, strip punctuation) via `_normalize_spoken()`.
- If text matches `end_word` (default `"done"`): returns `"end"` — utterance NOT appended to buffer.
- Otherwise: appends audio to `_buffer`, returns `"buffered"`.

Note: the local `small.en` model is used during dictation buffering *only for end-word detection and HUD display* — it is not used for the final dictation transcription. The final transcription is always done by the remote whisper.cpp server.

### 4.4 Exit paths (D4)

**Exit A — End word**

`handle_utterance()` returns `"end"`. `_process_utterance()` calls `DictationSession.take_and_finish()` (atomic: holds `_lock` across deactivation + buffer capture), receives the concatenated audio, and submits `_finalize_dictation(audio)` to `_dictation_executor`.

**Exit B — Right Ctrl hotkey (hotkey-end drain)**

`on_dictation_toggle()` is called on the pynput thread while dictation is active. It calls `DictationSession.request_end()`, which sets the `_pending_end` threading.Event — it does NOT deactivate the session.

The pipeline thread detects `pending_end` and switches to `_utt_q.get(timeout=0.25)`. If no new utterance arrives within 250 ms (`_DICTATION_DRAIN_TIMEOUT_S`), the queue is considered drained, and `_finalize_pending_dictation_end()` is called:

```python
# daemon.py — _finalize_pending_dictation_end()
audio = self._dictation_session.take_and_finish()
if audio is not None:
    self._dictation_executor.submit(self._finalize_dictation, audio)
```

`take_and_finish()` is the same atomic method used by the end-word path. If the end-word path wins the race (session already inactive), `take_and_finish()` returns `None` and no double-submit occurs. The lock inside `take_and_finish()` spans both the deactivation and the buffer capture, making this race-safe.

**Scroll Lock close while dictating**

`on_scroll_lock()` calls `DictationSession.cancel()`, which drops the buffer without finalising. The audio is lost.

### 4.5 Finalization — `_finalize_dictation` (D5–D7)

Runs on the **`dictation` executor thread** (single-worker `ThreadPoolExecutor`, thread prefix `dictation`). The pipeline thread is never blocked by network I/O.

**Step 1 — WAV encode**

`encode_wav(audio)` in `src/voice_commander/dictation/store.py`:
- Clips float32 to `[-1.0, 1.0]`.
- Converts to little-endian int16 PCM.
- Wraps in a `wave` container (mono, 16 kHz, 16-bit) using `io.BytesIO` — no disk I/O during encode.
- Returns raw WAV bytes.

`DictationStore.save_audio(wav_bytes)` writes to `outputs/dictation/last.wav` (overwrites).

On encode/save failure: publishes `dictation.error` SSE `{reason: "encode"}`, fires miss chime, returns.

**Step 2 — Remote POST**

`remote.post_audio(wav_bytes, endpoint)` in `src/voice_commander/dictation/remote.py`:

```
POST <dictation.endpoint>
Content-Type: multipart/form-data
  file: audio.wav  (16 kHz mono PCM_16 WAV bytes)
  response_format: json
  temperature: 0.0

→ 200 OK
{
  "text": "The transcribed dictation text."
}
```

`response_format=json` (not `verbose_json`) is deliberate: dictation needs only `text`. `verbose_json` makes whisper.cpp compute per-segment confidence + token timestamps, adding ~1.2 s on a 36-second clip for data the pipeline discards (ADR 0086 D5).

Timeout: 300 seconds (`_TIMEOUT_S` in `remote.py`) to accommodate long dictations.

The raw text is whitespace-collapsed (`" ".join(text.split())`) because whisper.cpp emits a newline at every segment boundary, which would produce spurious line breaks when pasted as prose.

On any failure (network, HTTP ≠ 200, bad JSON, missing `text` key): raises `DictationRemoteError`. The caller publishes `dictation.error` SSE `{reason: "endpoint"}`, fires miss chime, returns. `last.wav` has already been written so the user can retry via `GET /page/dictation`.

**Step 3 — Save text**

`DictationStore.save_text(text)` writes to `outputs/dictation/last.txt` (overwrites).

**Step 4 — Clipboard paste**

`clipboard.paste_via_clipboard(text)` in `src/voice_commander/dictation/clipboard.py`:

1. `read_clipboard_text()` — opens the Win32 clipboard (`win32clipboard.OpenClipboard`), reads `CF_UNICODETEXT`, closes.
2. `set_clipboard_text(text)` — opens, `EmptyClipboard()`, `SetClipboardData(CF_UNICODETEXT, text)`, closes.
3. `time.sleep(settle_ms / 1000)` — 100 ms settle (Windows clipboard ops are not instant).
4. `send_paste()` — `pyautogui.hotkey("ctrl", "v")`.
5. `time.sleep(settle_ms / 1000)` — 100 ms settle before restore.
6. `set_clipboard_text(original)` — restore original contents (if original was text).

Clipboard open is retried up to 6 times with 50 ms delay if another process holds it. On paste failure: publishes `dictation.error` SSE `{reason: "clipboard"}`, fires miss chime.

With Windows clipboard history (Win+V) enabled, the transcription becomes the second history entry and the user's original content is restored to first place.

**Step 5 — Success events**

On success, the finalize function publishes:
- `"transcript"` SSE `{text: ..., confidence: 1.0}` — the HUD displays the dictated text in light blue.
- `"dictation.result"` SSE `{text: ...}` — consumed by the web UI `/page/dictation`.

### 4.6 One-slot retention (D7)

Every dictation overwrites exactly two files:

| File | Content |
|---|---|
| `outputs/dictation/last.wav` | 16 kHz mono PCM_16 WAV of the buffered audio |
| `outputs/dictation/last.txt` | Transcription text (or error description) |

`DictationStore` manages these paths. `GET /page/dictation` reads both. `POST /dictation/retranscribe` reads `last.wav`, re-POSTs it to the configured endpoint, and writes the new text to `last.txt`.

### 4.7 Config keys

```toml
[dictation]
endpoint = "http://192.168.4.200:8765/inference"  # full URL to whisper.cpp server
end_word = "done"                                  # spoken word that ends dictation
```

The daemon also reads `dictation_key` from `[hotkey]`:

```toml
[hotkey]
hotkey    = "scroll_lock"
dictation_key = "ctrl_r"   # Right Ctrl; empty string disables
```

### 4.8 Modules involved

| Symbol | File |
|---|---|
| `DictationSession` | `src/voice_commander/dictation/session.py` |
| `DictationSession.handle_utterance()` | `src/voice_commander/dictation/session.py` |
| `DictationSession.take_and_finish()` | `src/voice_commander/dictation/session.py` |
| `DictationSession.request_end()` | `src/voice_commander/dictation/session.py` |
| `DictationStore` | `src/voice_commander/dictation/store.py` |
| `encode_wav()` | `src/voice_commander/dictation/store.py` |
| `remote.post_audio()` | `src/voice_commander/dictation/remote.py` |
| `DictationRemoteError` | `src/voice_commander/dictation/remote.py` |
| `clipboard.paste_via_clipboard()` | `src/voice_commander/dictation/clipboard.py` |
| `StreamingDaemon._finalize_dictation()` | `src/voice_commander/daemon.py` |
| `StreamingDaemon._finalize_pending_dictation_end()` | `src/voice_commander/daemon.py` |
| `StreamingDaemon.on_dictation_toggle()` | `src/voice_commander/daemon.py` |

### 4.9 Governing ADRs

| Decision | ADR |
|---|---|
| Dictation as session sub-state; entry/exit paths | ADR 0086 (D1–D4) |
| Remote whisper.cpp POST, `response_format=json` | ADR 0086 (D5) |
| Clipboard round-trip paste | ADR 0086 (D6) |
| One-slot on-disk retention + web re-transcribe | ADR 0086 (D7, D8) |
| Failure handling (error SSE + miss chime) | ADR 0086 (D9) |
| `mute_key` → `dictation_key` rename | ADR 0086 (D10) |
| Mute toggle superseded | ADR 0025 (superseded), ADR 0086 |
| Wire format (whisper.cpp server contract) | ADR 0073 (reused by ADR 0086) |

---

## 5. SSE Events and HUD

Every transcription path publishes `"transcript"` on the EventBus. The voice_sprite process subscribes via SSE and renders a per-utterance sequence in the HUD overlay:

| Order | SSE event | HUD colour |
|---|---|---|
| 1 | `transcript` — what was heard / dictated | light blue (`#b4c8e6`) |
| 2 | `tool_fired` — tool name(s) fired (command path only) | green |
| 3 | `plan_outcome` miss or error (only if non-ok) | orange / red |

Dictation publishes `"transcript"` twice: once per VAD utterance (from local faster-whisper, used for end-word detection) and once at the end of `_finalize_dictation()` (from the remote whisper.cpp result, with `confidence=1.0`).

Governing ADR: ADR 0079.

---

## 6. Remote Transcription Backend (ADR 0073)

ADR 0073 introduced `[transcription] backend = "local" | "remote"` as a toggle for the **command** path. This is distinct from the dictation remote endpoint, which is always remote. When `backend = "remote"`, `StreamingDaemon` uses a `RemoteTranscriber` (not shown above) instead of `Transcriber` for the command path. The wire format is `verbose_json` (for per-segment confidence) to `POST /inference`, matching the whisper.cpp server contract. Failure returns `TranscriptionResult(text="", confidence=0.0, no_speech_prob=1.0)`, which fires the confidence gate miss chime. There is no auto-fallback.

The dictation path (`_finalize_dictation`) does **not** use `RemoteTranscriber` — it calls `remote.post_audio()` directly and always uses `response_format=json` (no confidence needed).

---

## 7. Thread Summary

| Thread name | Owned by | Responsibilities in transcription |
|---|---|---|
| PortAudio callback thread | `sd.InputStream` | `indata.copy()` + `raw_q.put_nowait()` only |
| VAD worker thread | `StreamingRecorder` | Resample, VAD, emit utterance ndarray via `_on_utterance` |
| `vc-pipeline` | `StreamingDaemon` | Drain `_utt_q`, call `_process_utterance()` for every utterance |
| `vc-transcriber-load` | `StreamingDaemon` | Load faster-whisper model at startup; set `_transcriber_ready` |
| `wav-writer` executor | `_wav_executor` | Write `outputs/last_utterance.wav` asynchronously |
| `dictation` executor | `_dictation_executor` | Encode WAV, POST to remote, paste via clipboard |
| pynput listener thread | `HotkeyController` | Fire `on_scroll_lock()`, `on_dictation_toggle()` |
