# Voice Commander — Architecture

**Date:** 2026-04-19
**Status:** Authoritative
**Source spec:** [superpowers/specs/2026-04-19-voice-commander-design.md](superpowers/specs/2026-04-19-voice-commander-design.md)

This document is the canonical reference for Voice Commander's subsystem design and component contracts. It expands Sections 2 and 3 of the design spec into a standalone, agent-readable form. All type stubs in Section 3 are normative — implementations must match these signatures exactly.

---

## 1. High-level Flow

```
┌──────────────┐  toggle  ┌─────────────────────────────────────────────────────┐
│  HotkeyCtrl  │─────────▶│              StreamingRecorder                      │
│  (pynput)    │          │  sd.InputStream + Resampler + VADGate               │
└──────────────┘          │                                                     │
                          │  ┌─────────────────┐   raw_q   ┌─────────────────┐ │
                          │  │PortAudio callback│──────────▶│  VAD worker     │ │
                          │  │   (RT thread)    │  float32  │ Resampler 48k→  │ │
                          │  │  indata.copy() + │  frames   │ 16k + VADGate   │ │
                          │  │  put_nowait()    │           │ (silero-vad)    │ │
                          │  └─────────────────┘           └────────┬────────┘ │
                          └───────────────────────────────────────── │ ─────────┘
                                                            utt_q   │  utterance ndarray
                                                                     ▼
                          ┌──────────────────────────────────────────────────────┐
                          │              StreamingDaemon pipeline worker         │
                          │                                                      │
                          │  Transcriber ──text──▶ gates ──▶ Matcher ──▶ Dispatcher │
                          │  (faster-whisper)       (conf)   (rapidfuzz)  (tool fn) │
                          │                                                      │
                          │                         FeedbackSink (chime + log)  │
                          └──────────────────────────────────────────────────────┘
```

**Thread topology:**

| Thread | Owns | Does |
|---|---|---|
| PortAudio callback thread | `sd.InputStream` callback | `indata.copy()` + `raw_q.put_nowait()` — no blocking, no allocation |
| VAD worker thread | `Resampler` + `VADGate` | Drains `raw_q`; resamples 48k→16k; runs silero-vad; emits complete utterances to `utt_q` |
| Pipeline worker thread | `Transcriber` + `Matcher` + `Dispatcher` | Drains `utt_q`; runs full inference + match + dispatch pipeline |

**Session model:** Scroll Lock opens a session; a second press closes it. An optional mute key (configurable, disabled by default) suspends the audio stream within a session without ending it — two independent flags (`session_active`, `muted`). See ADR 0025. While a session is open, VAD auto-segments the audio stream. Each detected utterance fires the pipeline worker immediately — no keypresses required between commands.

---

## 2. Subsystem Boundaries

Eight subsystems connected by the `StreamingDaemon` orchestrator. Each is independently unit-testable:

| Subsystem | Responsibility | Key dependency |
|---|---|---|
| `HotkeyController` | Listen for configurable keys, dispatch to registered callbacks | `pynput` |
| `Resampler` | Stream device-native PCM → 16 kHz float32 chunks | `soxr` |
| `VADGate` | Detect speech onset/offset; accumulate utterance ndarrays with pre-roll | `silero-vad`, `onnxruntime` |
| `StreamingRecorder` | Own `sd.InputStream` + VAD worker thread; call `utterance_sink` on speech-end | `sounddevice`, `Resampler`, `VADGate` |
| `Transcriber` | ndarray (or WAV path) → text using preloaded model | `faster-whisper` (CUDA) |
| `ToolRegistry` | Register/discover `@tool`-decorated functions | stdlib (`importlib`) |
| `Matcher` | Fuzzy-match transcript → tool | `rapidfuzz` |
| `Dispatcher` | Invoke tool function, report outcome | (no external) |
| `FeedbackSink` | Chimes + log | `winsound` |

---

## 3. Threading Model

Four long-lived threads plus the main thread:

1. **Main thread** — starts the daemon, installs signal handlers, blocks on `shutdown_event`. Does no real work.
2. **Hotkey listener thread** — owned by `pynput`. Fires `on_toggle()` as a callback on this thread. Callback only calls `StreamingRecorder.open_session()` or `close_session()` — no blocking work.
3. **PortAudio callback thread** — owned by `sounddevice`. The `sd.InputStream` callback does `indata.copy()` + `raw_q.put_nowait()` only. No allocation, no blocking, no GIL-contested work. See `gotchas.md` §11.
4. **VAD worker thread** — drains `raw_q`; passes each chunk through `Resampler.process()` (48k→16k); slices into 512-sample frames; feeds each frame to `VADGate.process()`; when `VADGate` returns a complete utterance ndarray, calls `utterance_sink` which enqueues it on `utt_q`.
5. **Pipeline worker thread** — drains `queue.Queue[ndarray]` (`utt_q`), runs `Transcriber.transcribe() → confidence/word-count gates → Matcher.match() → Dispatcher.dispatch()` sequentially. One utterance at a time; if the VAD worker emits the next utterance before the previous pipeline run finishes, it queues up.

Queue topology:

```
PortAudio callback thread → raw_q → VAD worker thread → utt_q → pipeline worker thread
```

Graceful shutdown: Ctrl+C or SIGTERM sets `shutdown_event`; `StreamingRecorder.close_session()` drains `raw_q` and joins the VAD worker; a `None` sentinel is enqueued on `utt_q` to stop the pipeline worker; the hotkey listener is stopped; the model is unloaded.

Rationale: the PortAudio callback has a real-time deadline (10–20 ms) — any blocking work causes audio glitches. The VAD worker must not share state with the pipeline worker — silero-vad ONNX sessions are not thread-safe. See `gotchas.md` §11–12.

---

## 4. Component Contracts

### 4.1 `HotkeyController`

```python
class HotkeyController:
    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None: ...
    def start(self) -> None: ...   # non-blocking; spawns pynput listener
    def stop(self) -> None: ...
```

**What it does:** Listens for one or more configurable keys using `pynput`'s global keyboard listener (single `Listener` instance, `suppress=False`). Each key-release event dispatches to the registered callback for that key via a `bindings` dict. It resolves each string key name to a `pynput.keyboard.Key` member at construction time and validates them; invalid keys raise `ValueError` before the daemon starts.

**Who calls it:** `Daemon.__init__` constructs it; `Daemon.run()` calls `start()`; `Daemon.shutdown()` calls `stop()`.

**Who it calls:** `on_toggle` callback (injected by `Daemon`). That callback does nothing heavy — it just checks `Recorder.is_recording` and delegates to `Recorder.start()` or `Recorder.stop()`.

**How it is tested:** A fake `on_toggle` callable is injected. Key events are driven via `pynput.keyboard.Controller` in a test thread. Tests assert the toggle fired the expected number of times and that the callback is never invoked concurrently with itself (re-entrant safety).

---

### 4.2 `Resampler`

```python
class Resampler:
    def __init__(self, src_rate: int, dst_rate: int = 16000) -> None: ...
    def process(self, chunk: np.ndarray) -> np.ndarray: ...
    def flush(self) -> np.ndarray: ...
    def reset(self) -> None: ...
```

**What it does:** Wraps `soxr.ResampleStream` at `HQ` quality. `process()` accepts a 1-D float32 numpy array at `src_rate` and returns a 1-D float32 array at `dst_rate`. The resampler maintains internal polyphase FIR filter state across calls so chunk-boundary artifacts do not occur. `flush()` drains any samples held in the filter's delay line and recreates the stream for the next session. `reset()` recreates the stream without flushing — use at session open when the previous session's tail should be discarded. One `Resampler` instance per session; not thread-safe for concurrent calls.

**Who calls it:** `StreamingRecorder`'s VAD worker thread constructs a fresh `Resampler` per `open_session()` call and calls `process()` on every chunk drained from `raw_q`. See `gotchas.md` §13 for the state-lifetime constraint.

**Who it calls:** `soxr.ResampleStream.resample_chunk`. No other Voice Commander subsystems.

**How it is tested:** Synthetic 48 kHz sine wave chunks are fed in; output sample count and frequency content are verified after resampling. Edge cases: empty input, last=True flush, consecutive process calls produce artefact-free continuity at chunk boundaries.

---

### 4.2a `VADGate`

```python
class VADGate:
    def __init__(
        self,
        model: Any,           # silero-vad loaded model
        threshold: float = 0.5,
        min_speech_ms: int = 250,
        min_silence_ms: int = 100,
        speech_pad_ms: int = 30,
        pre_roll_ms: int = 320,
        max_utterance_ms: int = 30_000,
    ) -> None: ...

    def process(self, frame_16k: np.ndarray) -> np.ndarray | None: ...
    def reset(self) -> None: ...
```

**What it does:** Wraps `silero_vad.VADIterator` with two additions: a pre-roll ring buffer (keeps the last `pre_roll_ms` worth of frames so speech-start context is not lost) and a max-utterance guard (force-ends an utterance if it exceeds `max_utterance_ms` to prevent unbounded accumulation). `process()` accepts exactly 512 float32 samples at 16 kHz and returns either a concatenated ndarray (pre-roll + speech frames) when speech-end or the guard fires, or `None` while accumulating. `reset()` resets silero's internal hidden states and clears all buffers — call before each `open_session()`. Thread-safe for single-threaded use only; never share an instance across threads (see `gotchas.md` §12).

**Who calls it:** `StreamingRecorder`'s VAD worker thread — one `VADGate` instance per `StreamingRecorder`, reset on each `open_session()`.

**Who it calls:** `silero_vad.VADIterator.__call__()` internally. No other Voice Commander subsystems.

**How it is tested:** Synthetic 16 kHz audio (silence → speech → silence sequence) is fed frame-by-frame; tests assert: utterance returned on speech-end, pre-roll prepended, `None` returned while accumulating, force-end fires at `max_utterance_ms`, `reset()` discards buffered state.

---

### 4.2b `StreamingRecorder`

```python
class StreamingRecorder:
    def __init__(
        self,
        device: int | None,
        channels: int,
        vad_gate: VADGate,
        utterance_sink: Callable[[np.ndarray], None],
        vad_sample_rate: int = 16000,
    ) -> None: ...

    def open_session(self) -> None: ...
    def close_session(self) -> None: ...
    @property
    def is_open(self) -> bool: ...
```

**What it does:** Opens a `sounddevice.InputStream` on `open_session()`. The PortAudio callback does only `indata.copy()` + `raw_q.put_nowait()`. A VAD worker thread drains `raw_q`, passes chunks through a freshly created `Resampler`, slices the resampled output into 512-sample frames, and feeds each frame to `vad_gate.process()`. When `vad_gate.process()` returns a non-None ndarray, the worker calls `utterance_sink(ndarray)` on the VAD worker thread. `close_session()` stops and closes the stream, sends a `None` sentinel to `raw_q`, and joins the VAD worker (up to 5 s). If a session is not open, `close_session()` is a no-op.

The sample rate is queried via `sounddevice.query_devices()` at `open_session()` time — never hardcoded. A fresh `Resampler` is created per session to avoid filter-state bleed-through (see `gotchas.md` §13). `vad_gate.reset()` is called at `open_session()` for the same reason.

**Who calls it:** `StreamingDaemon.on_toggle()` (on the hotkey-listener thread). `utterance_sink` is wired to `StreamingDaemon._on_utterance()`, which enqueues the ndarray on `utt_q`.

**Who it calls:** `sounddevice.InputStream`; `Resampler.process()`; `VADGate.process()`; `utterance_sink` callback. No other Voice Commander subsystems.

**How it is tested:** The stream callback is driven with synthetic PCM frames via mocked `sounddevice`. Tests verify: `utterance_sink` called with correct ndarray after simulated speech-end, `is_open` state transitions, `close_session()` no-op when idle, VAD worker joins cleanly after sentinel, `RuntimeError` if previous VAD thread still alive on `open_session()`.

---

### 4.3 `Transcriber`

```python
@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    duration_ms: int
    confidence: float   # normalized avg segment logprob, [0, 1]

class Transcriber:
    def __init__(
        self,
        model_size: str = "small.en",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None: ...

    def load(self) -> None: ...   # blocks; called once at daemon start
    def transcribe(self, audio: Path | np.ndarray) -> TranscriptionResult: ...
```

**What it does:** Wraps `faster_whisper.WhisperModel`. `load()` is a blocking call that downloads/caches and loads the model weights into GPU VRAM — it is called once at daemon startup so `transcribe()` never incurs cold-start latency. `transcribe()` runs inference on the supplied audio (either a WAV `Path` or a 1-D float32 ndarray at 16 kHz) and returns a `TranscriptionResult`. In VAD streaming mode an ndarray is passed directly to avoid temp-file I/O on the hot path (see ADR 0018). Language is pinned to English (`small.en` is English-only so no language detection overhead). `confidence` is computed as the mean of each segment's `avg_logprob`, clamped to `[0, 1]` via `max(0.0, min(1.0, (mean_logprob + 1.0)))` — values below `config.transcription.min_confidence` (default `0.30`) are treated as misses by `StreamingDaemon`'s pipeline gate regardless of fuzzy score.

**Who calls it:** The worker thread (inside `Daemon`'s worker loop). `load()` is called by `Daemon.run()` before the worker thread starts. `Daemon.shutdown()` calls `Transcriber.unload()` to release the CUDA context.

**CUDA DLL loading:** At module import time, `transcriber.py` imports `_cuda_setup` and calls `_cuda_setup.register()` before `from faster_whisper import WhisperModel`. `register()` preloads cuBLAS and cuDNN DLLs from the `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` pip packages via `ctypes.WinDLL(abs_path)`, which makes them resolvable by CTranslate2's native `LoadLibrary` calls regardless of shell PATH state. See ADR 0012 and `docs/gotchas.md` §2.

**Who it calls:** `faster_whisper.WhisperModel.transcribe()`. No other Voice Commander subsystems.

**How it is tested:** The model is loaded once per test session via a pytest session-scoped fixture. Canned WAV files in `tests/fixtures/audio/` (checked into the repo) are transcribed and asserted: exact-match on clean, short, deterministic clips; `fuzzy-contains` on harder clips. The `confidence` field is asserted to be within `[0, 1]` for all fixtures.

---

### 4.4 `ToolRegistry`

```python
@dataclass(frozen=True)
class ToolEntry:
    name: str             # function name, e.g. "copy"
    phrases: tuple[str, ...]
    func: Callable[[], None]
    module: str           # e.g. "voice_commander.tools.clipboard"
    docstring: str | None

class ToolRegistry:
    def register(self, entry: ToolEntry) -> None: ...
    def all(self) -> list[ToolEntry]: ...
    def by_name(self, name: str) -> ToolEntry | None: ...
    def flat_phrases(self) -> list[tuple[str, str]]: ...
        # [(phrase, tool_name), ...] for rapidfuzz

def tool(phrases: list[str]) -> Callable[[Callable], Callable]:
    """Decorator. Registers the function on the module-global registry."""

def discover(package: str = "voice_commander.tools") -> ToolRegistry:
    """Imports every submodule in `package`, triggering @tool registration."""
```

**What it does:** Maintains a name-keyed dictionary of `ToolEntry` records. The `@tool(phrases=[...])` decorator registers the decorated function on the module-global `ToolRegistry` singleton at import time. `discover()` uses `importlib` to import every submodule under `voice_commander.tools`, which triggers all `@tool` decorators as a side effect. Phrases are normalized on registration (lowercase, single-space collapsed, punctuation stripped) so matching is case- and punctuation-insensitive. Re-registering the same `name` raises `DuplicateToolError` to catch accidental duplicates early.

**Who calls it:** `Daemon.__init__` calls `discover()` to populate the registry before constructing `Matcher`. `Matcher` calls `flat_phrases()` to build its rapidfuzz corpus.

**Who it calls:** `importlib.import_module` (inside `discover()`). No external libraries.

**How it is tested:** Fake `ToolEntry` objects are registered manually. Tests assert `all()` returns all entries, `by_name()` returns the right entry or `None`, `flat_phrases()` returns the expected `(phrase, name)` pairs, and `DuplicateToolError` is raised on duplicate `name`.

---

### 4.5 `Matcher`

```python
@dataclass(frozen=True)
class MatchResult:
    tool: ToolEntry | None
    phrase: str | None
    score: float                                 # 0-100
    candidates: tuple[tuple[str, str, float], ...]  # (phrase, tool_name, score) top-5

class Matcher:
    def __init__(self, registry: ToolRegistry, threshold: float = 85.0) -> None: ...
    def match(self, utterance: str) -> MatchResult: ...
```

**What it does:** Builds a flat phrase corpus from `registry.flat_phrases()` at construction time. `match()` normalizes the utterance the same way phrases were normalized at registration, then calls `rapidfuzz.process.extract(scorer=rapidfuzz.fuzz.WRatio, limit=5)` to retrieve the top-5 candidates. If the best score is at or above `threshold`, `tool` and `phrase` are populated; otherwise they are `None` (a "miss"), but `candidates` is always populated for logging. Ties at the top score are broken by tool-name alphabetical order, making the result deterministic and thus testable.

**Who calls it:** The worker thread in `Daemon`'s pipeline, immediately after `Transcriber.transcribe()` returns.

**Who it calls:** `rapidfuzz.process.extract` and `ToolRegistry.by_name()` to look up the winning `ToolEntry`.

**How it is tested:** A stub `ToolRegistry` with known phrases is constructed. Utterances are fed and asserted on: expected tool name, score above threshold, candidate list ordering, miss behaviour below threshold, and tie-breaking by alphabetical order.

---

### 4.6 `Dispatcher`

```python
class Dispatcher:
    def __init__(self, feedback: FeedbackSink) -> None: ...
    def dispatch(self, transcript: str, match: MatchResult) -> None: ...
```

**What it does:** The final step in the pipeline. If `match.tool is None`, it calls `feedback.on_miss(transcript, match.candidates)` and returns. If a tool matched, it calls `feedback.on_match(tool_name, phrase, score)` and then invokes `match.tool.func()` inside a `try/except BaseException`. On any exception it calls `feedback.on_error("dispatcher", err)` and logs the full traceback — the daemon continues running. Tool functions run on the worker thread and must complete in a few hundred milliseconds (they perform keystroke sends via `pyautogui`).

**Who calls it:** The worker thread in `Daemon`, after `Matcher.match()` returns.

**Who it calls:** `FeedbackSink` callbacks and the matched `ToolEntry.func` callable.

**How it is tested:** `CapturingFeedbackSink` is injected. Tests assert: `on_match` + `func()` called on a successful match; `on_miss` called on `match.tool is None`; `on_error` called and daemon does not propagate the exception when `func()` raises.

---

### 4.7 `FeedbackSink`

```python
class FeedbackSink(Protocol):
    def on_recording_start(self) -> None: ...
    def on_recording_stop(self) -> None: ...
    def on_transcript(self, text: str, confidence: float) -> None: ...
    def on_match(self, tool: str, phrase: str, score: float) -> None: ...
    def on_miss(self, transcript: str, candidates: Sequence[tuple[str, str, float]]) -> None: ...
    def on_error(self, subsystem: str, err: BaseException) -> None: ...
```

**What it does:** A `Protocol` (structural subtype) that decouples all user-visible feedback from the pipeline logic. Concrete implementations:

- `WindowsFeedbackSink` — plays WAV chimes via `winsound.PlaySound(path, SND_FILENAME | SND_ASYNC)` (fire-and-forget, does not block the worker thread) and logs match/miss/error events. No visual notifications — see ADR 0013 for why toasts were dropped.
- `NullFeedbackSink` — all methods are no-ops. The default in unit tests where feedback is irrelevant.
- `CapturingFeedbackSink` — records every call into a list for assertion in `Dispatcher` tests.

**Who calls it:** `Daemon` (for `on_recording_start` / `on_recording_stop`) and `Dispatcher` (for all other callbacks).

**Who it calls:** `winsound.PlaySound` (`WindowsFeedbackSink` only) and the module logger.

**How it is tested:** `WindowsFeedbackSink` is pure side-effects on `winsound` and the logger — covered by the unit suite's `NullFeedbackSink` / `CapturingFeedbackSink` patterns, plus smoke coverage by the Phase-3 GATE when a human runs the daemon and hears the chimes.

---

### 4.8 `StreamingDaemon`

```python
class StreamingDaemon:
    def __init__(
        self,
        feedback: FeedbackSink,
        recorder: StreamingRecorder | None,
        transcriber: Transcriber,
        matcher: Matcher,
        dispatcher: Dispatcher,
        *,
        min_confidence: float = 0.30,
        min_word_count: int = 1,
        max_no_speech_prob: float = 0.6,
        output_dir: str = "outputs",
    ) -> None: ...
    def run(self, hotkey_key: str) -> None: ...  # blocks until shutdown
    def shutdown(self) -> None: ...
    def on_toggle(self) -> None: ...             # hotkey callback
```

**What it does:** The top-level orchestrator. `build_streaming_daemon(cfg)` factory constructs and wires all concrete subsystems. `run()` calls `Transcriber.load()` (blocking model preload), starts the pipeline worker thread, starts the hotkey listener, installs SIGINT handler, and blocks on `threading.Event.wait(0.5)` (polled to allow Ctrl+C on Windows — see `gotchas.md` §9). `on_toggle()` is the hotkey callback: calls `StreamingRecorder.open_session()` or `close_session()` depending on current session state. The pipeline worker drains `utt_q` and runs `transcribe → word-count gate → no_speech_prob gate → confidence gate → match → dispatch`. Async WAV write (`outputs/last_utterance.wav`) is submitted to a single-threaded `ThreadPoolExecutor` after every utterance for post-mortem debugging. `shutdown()` is idempotent — multiple calls are safe. `__main__.py` does exactly one thing: `build_streaming_daemon(Config.load()).run(cfg.hotkey.key)`.

**Who calls it:** `__main__.py` (the process entry point), signal handlers, and `on_toggle` (hotkey-listener thread).

**Who it calls:** All other subsystems. It is the only place where concrete implementations are wired to interfaces.

**How it is tested:** Integration tests bypass `HotkeyController` and `StreamingRecorder` entirely — they inject utterance ndarrays directly into `utt_q` and assert that the right tool function was called end-to-end. Unit tests for individual subsystems do not involve `StreamingDaemon`.

---

## 5. Data Flow (Happy Path)

**Session open:**
1. User presses Scroll Lock. `pynput` fires `on_toggle` on the listener thread.
2. `on_toggle` checks `StreamingRecorder.is_open`: false → `StreamingRecorder.open_session()` + `feedback.on_recording_start()` (chime).
3. `open_session()` queries device native rate, creates fresh `Resampler`, resets `VADGate`, opens `sd.InputStream`, spawns VAD worker thread.

**Utterance detection (loops while session is open):**
4. User speaks. PortAudio callback copies PCM chunks to `raw_q`.
5. VAD worker drains `raw_q`: `Resampler.process(chunk)` → 16 kHz frames → `VADGate.process(frame)`.
6. When speech ends (or max-utterance guard fires), `VADGate.process()` returns utterance ndarray.
7. VAD worker calls `utterance_sink(ndarray)` → `StreamingDaemon._on_utterance()` → `utt_q.put_nowait(ndarray)`.
8. Pipeline worker picks up utterance: async WAV write to `outputs/last_utterance.wav` (fire-and-forget).
9. `Transcriber.transcribe(utterance)` → `TranscriptionResult`.
10. `feedback.on_transcript(...)` logs transcript.
11. Word-count gate: drop if fewer than `min_word_count` words.
12. `no_speech_prob` gate: drop if above `max_no_speech_prob`.
13. Confidence gate: `on_miss()` if below `min_confidence`.
14. `Matcher.match(result.text)` → `MatchResult`.
15. `Dispatcher.dispatch(text, match)`:
    - Match above threshold → logs match + tool function executes.
    - Below threshold → miss beep + logs miss.
16. Pipeline worker loops back to `utt_q`.

**Session close:**
17. User presses Scroll Lock again. `on_toggle` → `StreamingRecorder.close_session()` + `feedback.on_recording_stop()` (chime).
18. Stream stops; VAD worker receives `None` sentinel, joins cleanly.

Total latency budget (speech-end detected → tool fires): ~700 ms target, 1.5 s hard ceiling.

---

## 7. Web UI Subsystem

The command management web UI runs as an embedded FastAPI server on a uvicorn daemon thread inside the main process.

### Thread Topology

```
[Main thread]           [HotkeyCtrl thread]    [Pipeline threads]    [uvicorn thread]
Daemon orchestrator     pynput listener        transcribe→match→     FastAPI app
owns registry,                                 dispatch worker       handles HTTP
reload_lock                                    shares ToolRegistry   uses reload_lock
```

### Components

- **`ToolMetadataStore`** — reads/writes sidecar TOML files next to tool Python modules. Atomic writes via tmp+rename. Per-tool file locking via `portalocker`.
- **`WebServer`** — wraps uvicorn on a daemon thread. Port-bump fallback (8765 → 8775). Graceful shutdown via `should_exit`.
- **`FastAPI app`** — 6 routes: dashboard, healthz, edit form, cancel edit, save, toggle. HTMX fragments for partial page updates.
- **`reload_lock`** — `threading.Lock` guards registry mutations. Held μs for reads, ms for saves.

### Data Flow (Save Cycle)

1. User clicks Edit → `GET /tool/{name}/edit` → HTMX swaps card to form.
2. User edits → Save → `POST /tool/{name}` with form data.
3. Server validates (non-empty phrases, no duplicates).
4. Acquires per-tool file lock → atomic TOML write → release.
5. Acquires `reload_lock` → `registry.reload_metadata()` → release.
6. Returns updated card fragment → HTMX swaps form back to card.
7. Matcher's next `match()` call sees updated phrases.

---

## 6. See Also

- [`../CLAUDE.md`](../CLAUDE.md) — project-wide durable context for agents and contributors
- [`superpowers/specs/2026-04-19-voice-commander-design.md`](superpowers/specs/2026-04-19-voice-commander-design.md) — original design spec (source of truth for this document)
- ADRs in [`decisions/`](decisions/):
  - [`decisions/0001-scroll-lock-hotkey.md`](decisions/0001-scroll-lock-hotkey.md)
  - [`decisions/0002-pynput-over-keyboard.md`](decisions/0002-pynput-over-keyboard.md)
  - [`decisions/0003-sounddevice-over-pyaudio.md`](decisions/0003-sounddevice-over-pyaudio.md)
  - [`decisions/0004-faster-whisper-cuda.md`](decisions/0004-faster-whisper-cuda.md)
  - [`decisions/0005-rapidfuzz-matching.md`](decisions/0005-rapidfuzz-matching.md)
  - [`decisions/0006-tool-decorator-registry.md`](decisions/0006-tool-decorator-registry.md)
  - [`decisions/0007-windows-toasts-over-tkinter.md`](decisions/0007-windows-toasts-over-tkinter.md)
  - [`decisions/0008-winsound-for-chimes.md`](decisions/0008-winsound-for-chimes.md)
  - [`decisions/0009-phased-delivery-with-hitl-gates.md`](decisions/0009-phased-delivery-with-hitl-gates.md)
  - [`decisions/0010-threading-model.md`](decisions/0010-threading-model.md)
  - [`decisions/0011-uv-package-manager.md`](decisions/0011-uv-package-manager.md)
  - [`decisions/0012-cuda-dll-bundling.md`](decisions/0012-cuda-dll-bundling.md)
  - [`decisions/0013-drop-winrt-toasts-audio-only-feedback.md`](decisions/0013-drop-winrt-toasts-audio-only-feedback.md)
  - [`decisions/0014-miss-only-chimes.md`](decisions/0014-miss-only-chimes.md)
  - [`decisions/0015-vad-streaming-mode.md`](decisions/0015-vad-streaming-mode.md)
  - [`decisions/0016-silero-vad-over-webrtcvad.md`](decisions/0016-silero-vad-over-webrtcvad.md)
  - [`decisions/0017-soxr-streaming-resampler.md`](decisions/0017-soxr-streaming-resampler.md)
  - [`decisions/0018-ndarray-handoff-to-whisper.md`](decisions/0018-ndarray-handoff-to-whisper.md)
  - [`decisions/0019-supersede-single-shot-recorder.md`](decisions/0019-supersede-single-shot-recorder.md)
