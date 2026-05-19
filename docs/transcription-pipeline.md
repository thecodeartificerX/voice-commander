# Voice Commander — Transcription Pipeline (End-to-End)

**Date:** 2026-05-18
**Status:** Authoritative
**Scope:** Both transcription paths — command (local faster-whisper) and dictation (WebSocket streaming to whisper.cpp).

This document is the single narrative reference for how audio moves from the microphone to text in Voice Commander. Everything here is grounded in the actual source code and ADRs; there is no speculation. Use the symbol names and file paths below to navigate the code directly.

---

## 1. Overview — Two Paths, One Audio Capture Stack

Voice Commander has two transcription paths that share the same audio capture infrastructure (mic → VAD) but diverge at the point where a complete utterance is handed off:

| Path | Transcriber | Trigger | Output |
|---|---|---|---|
| **Command** | `Transcriber` (local faster-whisper, CUDA) | Every VAD utterance during a normal session | Text → `VerbRouter` → `Dispatcher` |
| **Dictation** | Remote whisper.cpp server (WebSocket `/ws/transcribe`) | Session enters dictation sub-state; each VAD chunk is streamed in real-time; stabilised transcript pasted on exit | Text → clipboard paste → cursor |

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

## 4. Path 2 — Dictation Mode (WebSocket streaming to whisper.cpp)

*Superseded batch POST path removed by ADR 0092. The description below reflects the streaming implementation.*

### 4.1 ASCII flow

```
  Hotkey thread / pipeline thread        asyncio-loop thread         dictation executor thread
  ───────────────────────────────────    ──────────────────          ──────────────────────────
  [Entry: "dictate" utterance]
  VerbRouter → __dictation.start
  daemon intercepts → DictationSession.start()
      publishes "dictation.start"
      spawns asyncio-loop thread
      sprite shows ● DICTATING badge

  [OR: Right Ctrl keypress]
  on_dictation_toggle()
  → DictationSession.start()

  ──── dictation active ──────────────────────────────────────────────────────────────────────

  Each VAD utterance → _process_utterance()
      Transcriber.transcribe(ndarray)         [local faster-whisper — end-word detection only]
      publish "transcript" SSE
      DictationSession.handle_utterance(audio, text)
        → "buffered": encode_wav(audio) → chunk pushed onto _chunk_q
                       asyncio thread drains _chunk_q → ws.send(wav_bytes)
                       server replies partial JSON → LocalAgreement folds word
        → "end"    :  submit _end_owned_session_if_needed (close-before-finalize)
                       submit _finalize_dictation to dictation executor
        → "cancel" :  session.cancel() closes WS, discards transcript

  [OR: Right Ctrl pressed again]
  on_dictation_toggle()
  → DictationSession.request_end()            [hotkey thread]
      sets _pending_end Event
      enqueues _DICTATION_WAKE sentinel → wakes pipeline thread

  vc-pipeline drains with 250ms timeout
  → _finalize_pending_dictation_end()
      submit _end_owned_session_if_needed
      submit _finalize_dictation                     [dictation executor thread]

  ──── finalization ──────────────────────────────────────────────────────────────────────────────
                                                                  _finalize_dictation()
                                                                    │
                                                                    ├─ session.finish()
                                                                    │    pushes end sentinel to _chunk_q
                                                                    │    asyncio thread drains + closes WS
                                                                    │    LocalAgreement.finalize() → transcript
                                                                    │    returns stabilised text (or "" on error)
                                                                    │
                                                                    ├─ apply_corrections(text, vocab)
                                                                    ├─ apply_commands(text, vocab)
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

`DictationSession.start()` sets `_active = True`, creates a fresh `_chunk_q`, spawns the asyncio-loop thread (which connects to `ws_url` and drains the chunk queue), and publishes `dictation.start` on the EventBus.

### 4.3 Streaming transport (D3)

While `_dictation_session.active` is `True`, `_process_utterance()` branches before the word-count gate:

```python
# daemon.py — dictation branch inside _process_utterance()
if self._dictation_session is not None and self._dictation_session.active:
    kind = self._dictation_session.handle_utterance(utterance, result.text)
    if kind == "end":
        self._dictation_executor.submit(self._end_owned_session_if_needed)
        self._dictation_executor.submit(self._finalize_dictation)
    elif kind == "cancel":
        self._dictation_session.cancel()
        self._dictation_executor.submit(self._end_owned_session_if_needed)
    # "buffered" → utterance streamed inside handle_utterance
    run.set_status("ok")
    return
```

Each utterance is still transcribed by local faster-whisper (for end-word / cancel-word detection and HUD display), but the audio ndarray is also streamed immediately.

`DictationSession.handle_utterance(audio, text)`:
- Normalises the text via `_normalize_spoken()`.
- If text matches `cancel_word`: returns `"cancel"` — no audio streamed.
- If text matches `end_word` (default `"done"`): returns `"end"` — utterance NOT streamed.
- Otherwise: `encode_wav(audio)` → WAV bytes pushed onto `_chunk_q`; asyncio thread sends chunk to server; server `partial` reply folds into `LocalAgreement`; returns `"buffered"`.

### 4.4 Exit paths (D4)

**Exit A — End word**

`handle_utterance()` returns `"end"`. `_process_utterance()` submits `_end_owned_session_if_needed` then `_finalize_dictation` to `_dictation_executor` (close-before-finalize ordering from ADR 0090).

**Exit B — Right Ctrl hotkey (hotkey-end drain)**

`on_dictation_toggle()` calls `DictationSession.request_end()` (sets `_pending_end`) and enqueues `_DICTATION_WAKE` sentinel to wake the blocked pipeline thread (ADR 0089). The pipeline thread drains with a 250 ms timeout, then calls `_finalize_pending_dictation_end()`:

```python
# daemon.py — _finalize_pending_dictation_end()
self._dictation_executor.submit(self._end_owned_session_if_needed)
self._dictation_executor.submit(self._finalize_dictation)
```

**Exit C — Spoken cancel**

`handle_utterance()` returns `"cancel"`. The pipeline thread calls `session.cancel()` (closes the WebSocket, discards the transcript) and submits `_end_owned_session_if_needed`. No paste, no chime — sprite renders a transient "✕ CANCELLED" badge (ADR 0089).

**Scroll Lock close while dictating**

`on_scroll_lock()` calls `DictationSession.cancel()`, which closes the WebSocket and discards the transcript.

### 4.5 Finalization — `_finalize_dictation` (D5–D7)

Runs on the **`dictation` executor thread** (single-worker `ThreadPoolExecutor`, thread prefix `dictation`). The pipeline thread is never blocked by WebSocket teardown.

**Step 1 — Finish streaming session**

`session.finish()` in `src/voice_commander/dictation/session.py`:
- Pushes a `None` sentinel onto `_chunk_q` to signal end-of-stream to the asyncio thread.
- Joins the asyncio-loop thread (with a `_JOIN_MARGIN_S` grace on top of `idle_timeout_s`).
- Calls `LocalAgreement.finalize()` to flush any tentative tail words.
- Returns the stabilised transcript string (empty string if nothing was confirmed).

On encode failure in `handle_utterance()`: `session.error == "encode"` is set; `finish()` still returns, and the finalize method publishes `dictation.error {reason: "encode"}`, fires miss chime, returns.

On WebSocket connect failure: `session.error == "endpoint"` is set; empty transcript → `dictation.error {reason: "endpoint"}`, miss chime, returns.

**Step 2 — Post-process**

`apply_corrections(text, vocab.corrections)` fixes known mistranscriptions. `apply_commands(text, vocab.commands)` maps spoken phrases to control characters (`"newline"` → `\n`, `"paragraph"` → `\n\n`). Corrections run first so a lightly-mistranscribed command phrase can be repaired into its canonical form (ADR 0088).

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

**Step 5 — Success events**

On success, the finalize function publishes:
- `"transcript"` SSE `{text: ..., confidence: 1.0}` — the HUD displays the dictated text in light blue.
- `"dictation.result"` SSE `{text: ...}` — consumed by the web UI `/page/dictation`.

### 4.6 One-slot text retention (D7)

Every dictation overwrites one file:

| File | Content |
|---|---|
| `outputs/dictation/last.txt` | Transcription text (or error description) |

`DictationStore` manages this path. `GET /page/dictation` reads it. The batch `last.wav` / re-transcribe path was removed by ADR 0092.

### 4.7 Config keys

```toml
[dictation]
ws_url   = "ws://192.168.4.200:8765/ws/transcribe"  # WebSocket URL for whisper.cpp streaming server
end_word = "done"                                    # spoken word that ends dictation
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
| `DictationSession.finish()` | `src/voice_commander/dictation/session.py` |
| `DictationSession.request_end()` | `src/voice_commander/dictation/session.py` |
| `DictationSession.cancel()` | `src/voice_commander/dictation/session.py` |
| `LocalAgreement` | `src/voice_commander/dictation/local_agreement.py` |
| `stream_transcribe()` | `src/voice_commander/dictation/ws_client.py` |
| `pump()` | `src/voice_commander/dictation/bridge.py` |
| `DictationStore` | `src/voice_commander/dictation/store.py` |
| `encode_wav()` | `src/voice_commander/dictation/store.py` |
| `clipboard.paste_via_clipboard()` | `src/voice_commander/dictation/clipboard.py` |
| `StreamingDaemon._finalize_dictation()` | `src/voice_commander/daemon.py` |
| `StreamingDaemon._finalize_pending_dictation_end()` | `src/voice_commander/daemon.py` |
| `StreamingDaemon.on_dictation_toggle()` | `src/voice_commander/daemon.py` |

### 4.9 Governing ADRs

| Decision | ADR |
|---|---|
| Dictation as session sub-state; entry/exit paths | ADR 0086 (D1–D4) |
| Clipboard round-trip paste | ADR 0086 (D6) |
| Failure handling (error SSE + miss chime) | ADR 0086 (D9) |
| `mute_key` → `dictation_key` rename | ADR 0086 (D10) |
| Mute toggle superseded | ADR 0025 (superseded), ADR 0086 |
| Hotkey-end sentinel, 50 ms debounce, spoken cancel | ADR 0089 |
| Right Ctrl opens own session; close-before-finalize | ADR 0090 |
| Streaming transport, WebSocket, LocalAgreement | ADR 0092 (supersedes batch POST of ADR 0086 D5) |

---

## 5. SSE Events and HUD

Every transcription path publishes `"transcript"` on the EventBus. The voice_sprite process subscribes via SSE and renders a per-utterance sequence in the HUD overlay:

| Order | SSE event | HUD colour |
|---|---|---|
| 1 | `transcript` — what was heard / dictated | light blue (`#b4c8e6`) |
| 2 | `tool_fired` — tool name(s) fired (command path only) | green |
| 3 | `plan_outcome` miss or error (only if non-ok) | orange / red |

Dictation publishes `"transcript"` twice: once per VAD utterance (from local faster-whisper, used for end-word detection and HUD display) and once at the end of `_finalize_dictation()` (from the stabilised WebSocket transcript, with `confidence=1.0`).

Governing ADR: ADR 0079.

---

## 6. Remote Transcription Backend (ADR 0073)

ADR 0073 introduced `[transcription] backend = "local" | "remote"` as a toggle for the **command** path. This is distinct from the dictation remote endpoint, which is always remote. When `backend = "remote"`, `StreamingDaemon` uses a `RemoteTranscriber` (not shown above) instead of `Transcriber` for the command path. The wire format is `verbose_json` (for per-segment confidence) to `POST /inference`, matching the whisper.cpp server contract. Failure returns `TranscriptionResult(text="", confidence=0.0, no_speech_prob=1.0)`, which fires the confidence gate miss chime. There is no auto-fallback.

The dictation path (`_finalize_dictation`) does **not** use `RemoteTranscriber` — it streams each VAD chunk directly over a WebSocket to `/ws/transcribe` via `DictationSession` / `ws_client.stream_transcribe()` (ADR 0092).

---

## 7. Thread Summary

| Thread name | Owned by | Responsibilities in transcription |
|---|---|---|
| PortAudio callback thread | `sd.InputStream` | `indata.copy()` + `raw_q.put_nowait()` only |
| VAD worker thread | `StreamingRecorder` | Resample, VAD, emit utterance ndarray via `_on_utterance` |
| `vc-pipeline` | `StreamingDaemon` | Drain `_utt_q`, call `_process_utterance()` for every utterance |
| `vc-transcriber-load` | `StreamingDaemon` | Load faster-whisper model at startup; set `_transcriber_ready` |
| `wav-writer` executor | `_wav_executor` | Write `outputs/last_utterance.wav` asynchronously |
| `dictation` executor | `_dictation_executor` | Join WS asyncio thread, apply post-processing, paste via clipboard |
| pynput listener thread | `HotkeyController` | Fire `on_scroll_lock()`, `on_dictation_toggle()` |
