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
                          │  Transcriber ──text──▶ gates ──▶ LLMRouter ──▶ Dispatcher │
                          │  (faster-whisper)       (conf)   (httpx→LM)   (run_plan)  │
                          │                                    │                 │
                          │                                    └─ resolver.* (per-step param grounding) │
                          │                                                      │
                          │                         FeedbackSink (chime + log)  │
                          └──────────────────────────────────────────────────────┘
```

**Thread topology:**

| Thread | Owns | Does |
|---|---|---|
| PortAudio callback thread | `sd.InputStream` callback | `indata.copy()` + `raw_q.put_nowait()` — no blocking, no allocation |
| VAD worker thread | `Resampler` + `VADGate` | Drains `raw_q`; resamples 48k→16k; runs silero-vad; emits complete utterances to `utt_q` |
| Pipeline worker thread | `Transcriber` + `LLMRouter` + `Dispatcher` | Drains `utt_q`; runs transcribe → gates → `LLMRouter.route()` → `Dispatcher.run_plan()` |

**Session model:** Scroll Lock opens a session; a second press closes it. An optional mute key (configurable, disabled by default) suspends the audio stream within a session without ending it — two independent flags (`session_active`, `muted`). See ADR 0025. While a session is open, VAD auto-segments the audio stream. Each detected utterance fires the pipeline worker immediately — no keypresses required between commands.

---

## 2. Subsystem Boundaries

Subsystems are connected by the `StreamingDaemon` orchestrator. Each is independently unit-testable:

| Subsystem | Responsibility | Key dependency |
|---|---|---|
| `HotkeyController` | Listen for configurable keys, dispatch to registered callbacks | `pynput` |
| `Resampler` | Stream device-native PCM → 16 kHz float32 chunks | `soxr` |
| `VADGate` | Detect speech onset/offset; accumulate utterance ndarrays with pre-roll | `silero-vad`, `onnxruntime` |
| `StreamingRecorder` | Own `sd.InputStream` + VAD worker thread; call `utterance_sink` on speech-end | `sounddevice`, `Resampler`, `VADGate` |
| `Transcriber` | ndarray (or WAV path) → text using preloaded model | `faster-whisper` (CUDA) |
| `ToolRegistry` | Register/discover `@tool`-decorated functions; supports `@tool(name=...)` override for builtin-shadowing names (`type`, `open`) | stdlib (`importlib`) |
| `resolver` (module) | Ground `focus(target)` / `open(target)` parameters onto hwnds / launch tokens via rapidfuzz scoring | `rapidfuzz`, `pywin32`, `win32com` |
| `Dispatcher` | Execute a multi-step plan step-by-step; emit per-step INFO log; report plan start/complete/error | (no external) |
| `FeedbackSink` | Chimes + log | `winsound` |
| `LLMRouter` | Route all transcripts to local LM Studio for tool-call planning | `httpx` |
| `Validator` | Startup checks: sig/TOML drift, type support, range checks | stdlib (`inspect`, `typing`) |

---

## Process tree

```
start.ps1                         (audio-device TUI)
└── voice-commander-supervisor    (Python supervisor; long-lived parent)
    ├── voice-sprite              (subprocess; persistent across daemon restarts)
    └── voice-commander           (daemon; respawned on graceful exit 75)
```

The supervisor owns both children. The daemon's `/restart` web route triggers `os._exit(75)`; the supervisor's wait loop sees code 75 and respawns. Sprite is untouched, its SSE stream reconnects when the new daemon binds the web port.

---

## 3. Threading Model

Four long-lived threads plus the main thread:

1. **Main thread** — starts the daemon, installs signal handlers, blocks on `shutdown_event`. Does no real work.
2. **Hotkey listener thread** — owned by `pynput`. Fires `on_scroll_lock()` or `on_mute_toggle()` as callbacks on this thread. Callbacks only call `StreamingRecorder.open_session()` or `close_session()` — no blocking work.
3. **PortAudio callback thread** — owned by `sounddevice`. The `sd.InputStream` callback does `indata.copy()` + `raw_q.put_nowait()` only. No allocation, no blocking, no GIL-contested work. See `gotchas.md` §11.
4. **VAD worker thread** — drains `raw_q`; passes each chunk through `Resampler.process()` (48k→16k); slices into 512-sample frames; feeds each frame to `VADGate.process()`; when `VADGate` returns a complete utterance ndarray, calls `utterance_sink` which enqueues it on `utt_q`.
5. **Pipeline worker thread** — drains `queue.Queue[ndarray]` (`utt_q`), runs `Transcriber.transcribe() → confidence/word-count gates → LLMRouter.route() → Dispatcher.run_plan()` sequentially. One utterance at a time; if the VAD worker emits the next utterance before the previous pipeline run finishes, it queues up. A `None` return from `LLMRouter.route()` (timeout, connection error, malformed response, or `no_match` sentinel) fires `FeedbackSink.on_miss()` directly and loops back.

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

**Who it calls:** `on_scroll_lock` callback (injected by `Daemon`). That callback does nothing heavy — it just checks `StreamingRecorder._session_active` and delegates to `StreamingRecorder.open_session()` or `close_session()`.

**How it is tested:** A fake `on_scroll_lock` callable is injected. Key events are driven via `pynput.keyboard.Controller` in a test thread. Tests assert the toggle fired the expected number of times and that the callback is never invoked concurrently with itself (re-entrant safety).

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

**Who calls it:** `StreamingDaemon.on_scroll_lock()` (on the hotkey-listener thread). `utterance_sink` is wired to `StreamingDaemon._on_utterance()`, which enqueues the ndarray on `utt_q`.

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

**What it does:** Wraps `faster_whisper.WhisperModel`. `load()` is a blocking call that downloads/caches and loads the model weights into GPU VRAM — it is called once at daemon startup so `transcribe()` never incurs cold-start latency. `transcribe()` runs inference on the supplied audio (either a WAV `Path` or a 1-D float32 ndarray at 16 kHz) and returns a `TranscriptionResult`. In VAD streaming mode an ndarray is passed directly to avoid temp-file I/O on the hot path (see ADR 0018). Language is pinned to English (`small.en` is English-only so no language detection overhead). `confidence` is computed as the mean of each segment's `avg_logprob`, clamped to `[0, 1]` via `max(0.0, min(1.0, (mean_logprob + 1.0)))` — values below `config.transcription.min_confidence` (default `0.30`) are treated as misses by `StreamingDaemon`'s pipeline gate.

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
    phrases: tuple[str, ...]  # populated from TOML if phrases keys present; unused for routing. Retained for backward compatibility and web UI display.
    func: Callable[[], None]
    module: str           # e.g. "voice_commander.tools.clipboard"
    docstring: str | None
    settle_ms: int            # ms to sleep after execution
    llm_only: bool            # True → tool has parameters; not phrase-matchable
    params_schema: dict | None  # OpenAI tool JSON schema (built by tool_schema)
    internal: bool            # True → hidden from LLM tool list; still dispatchable
    origin: Literal["primitive", "command", "workflow"]  # source of entry
    args_meta: dict[str, ArgMetadata]  # per-param schema for web UI guided kwargs form (ADR 0057)

class ToolRegistry:
    def register(self, entry: ToolEntry) -> None: ...
    def all(self) -> list[ToolEntry]: ...
    def by_name(self, name: str) -> ToolEntry | None: ...
    def all_llm_visible(self) -> list[ToolEntry]: ...
        # All entries visible to the LLM router (all enabled tools)

def tool() -> Callable[[Callable], Callable]:
    """Decorator. Registers the function on the module-global registry."""

def discover(package: str = "voice_commander.tools") -> ToolRegistry:
    """Imports every submodule in `package`, triggering @tool registration."""
```

**What it does:** Maintains a name-keyed dictionary of `ToolEntry` records. The `@tool` decorator registers the decorated function on the module-global `ToolRegistry` singleton at import time. `discover()` uses `importlib` to import every submodule under `voice_commander.tools`, which triggers all `@tool` decorators as a side effect. Re-registering the same `name` raises `DuplicateToolError` to catch accidental duplicates early. The `phrases` field is populated from TOML when phrases keys are present; it is not used for routing but is retained for backward compatibility and web UI display.

**Who calls it:** `build_streaming_daemon()` factory calls `discover()` to populate the registry at startup. `LLMRouter` calls `all_llm_visible()` to build the tools array for each chat completion request. `Dispatcher.run_plan()` calls `by_name()` to look up tool functions during plan execution.

**Who it calls:** `importlib.import_module` (inside `discover()`). No external libraries.

**How it is tested:** Fake `ToolEntry` objects are registered manually. Tests assert `all()` returns all entries, `by_name()` returns the right entry or `None`, and `DuplicateToolError` is raised on duplicate `name`.

---

### 4.5 `resolver` module (parameter grounding)

```python
# src/voice_commander/resolver.py — module-level API

def resolve_window(target: str) -> int: ...       # returns hwnd; raises FocusWindowError
def resolve_app(target: str) -> str: ...          # returns launch token; raises OpenResolveError

def _set_config(config: LLMConfig) -> None: ...   # daemon startup hook
```

**What it does:** Two pure functions that ground fuzzy `target` strings emitted by the LLM onto concrete OS objects, plus a one-shot config-injection hook.

- `resolve_window(target)` enumerates visible titled windows via `win32gui.EnumWindows`, reads each owner process name via `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetModuleBaseName`, and scores every candidate as `max(WRatio(target, proc_name), WRatio(target, title))`. The top score wins above `focus_fuzzy_threshold` (default 70). Below → `FocusWindowError` with top-3 `(proc_name, title, score)` for diagnostics.
- `resolve_app(target)` branches: URI (scheme regex) → return verbatim; existing path → return resolved absolute path; otherwise rapidfuzz-score against a cached `(display_name, launch_token)` list of Start Menu `.lnk` stems (recursive `rglob` under `%ProgramData%` and `%APPDATA%`) plus `shell:AppsFolder` COM enumeration. Argmax above `open_fuzzy_threshold` (default 70) wins; below → `OpenResolveError`. Cache populated lazily on first call; never invalidated during daemon uptime.
- `_set_config(cfg.llm)` is called exactly once by `daemon.build_streaming_daemon()` at startup. It parks the `LLMConfig` on a module-level `_config_ref` slot so the threshold accessors can read `focus_fuzzy_threshold` / `open_fuzzy_threshold` without the primitives having to thread config through every call. The functions remain pure per call; the slot is set once and never mutated afterward.

Both functions are called directly from `tools/primitives.py` — `focus(target)` calls `resolve_window(target)` then runs the AttachThreadInput foreground workaround; `open(target)` calls `resolve_app(target)` then blocklist-checks the resolved token before `os.startfile`. `rapidfuzz` is the only external dependency; `pywin32` / `win32com` are imported lazily inside the functions so unit tests can monkeypatch them.

**Who calls it:** `primitives.focus` and `primitives.open_target` (on the pipeline worker thread, inside `Dispatcher.run_plan`). `daemon.build_streaming_daemon` calls `_set_config` once.

**Who it calls:** `rapidfuzz.fuzz.WRatio`, `win32gui`, `win32process`, `win32api`, `win32com.client.Dispatch`, `pythoncom.CoInitialize`, filesystem `Path.rglob`.

**How it is tested:** Monkeypatch `win32gui.EnumWindows` to a scripted callback list; assert return value on clear matches, `FocusWindowError` with expected top-3 below threshold. For `resolve_app`, seed `_cache["apps"]` directly and exercise URI / path / fuzzy branches. `_invalidate_app_cache()` is the test reset hook.

See ADR 0041 (rapidfuzz scope) and ADR 0042 (resolver module design).

---

### 4.6 `Dispatcher`

```python
class Dispatcher:
    def __init__(self, feedback: FeedbackSink) -> None: ...
    def run_plan(self, transcript: str, plan: Plan, registry: ToolRegistry) -> None: ...
```

**What it does:** The final step in the pipeline. `run_plan()` executes a multi-step `Plan` from the LLM router. It calls `feedback.on_plan_start()`, iterates through plan steps looking up each tool by name in the registry, emits a per-step `INFO` log of the form `plan step <i>/<total>: <name>(<kwargs>)`, invokes `tool.func(**step.kwargs)`, sleeps `settle_ms` between steps, and calls `feedback.on_plan_complete()`. If a step fails or a tool is unknown, `on_error` fires and the chain stops (when `plan.strict` is `True`, the default) or continues to the next step (when `plan.strict` is `False`). Only the first failure is recorded in the plan outcome regardless of mode. Tool functions run on the worker thread and must complete in a few hundred milliseconds (they perform keystroke sends via `pyautogui`).

Miss handling is owned by the pipeline worker (`StreamingDaemon._process_utterance` calls `FeedbackSink.on_miss()` directly when `LLMRouter.route()` returns `None`). `Dispatcher` only receives valid `Plan` objects.

**Who calls it:** The worker thread in `StreamingDaemon`, after `LLMRouter.route()` returns a non-None `Plan`.

**Who it calls:** `FeedbackSink` callbacks and each `ToolEntry.func` callable in the plan.

**How it is tested:** `CapturingFeedbackSink` is injected. Tests assert: `on_plan_start` + each `func()` called on a valid plan; `on_error` called and daemon does not propagate the exception when `func()` raises; `on_plan_complete` called after all steps.

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
    def on_plan_start(self, transcript: str, step_count: int) -> None: ...
    def on_plan_complete(self, transcript: str, steps_executed: int) -> None: ...
```

**What it does:** A `Protocol` (structural subtype) that decouples all user-visible feedback from the pipeline logic. Concrete implementations:

- `WindowsFeedbackSink` — plays WAV chimes via `winsound.PlaySound(path, SND_FILENAME | SND_ASYNC)` (fire-and-forget, does not block the worker thread) and logs plan/miss/error events. No visual notifications — see ADR 0013 for why toasts were dropped.
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
        llm_router: LLMRouter,
        dispatcher: Dispatcher,
        *,
        registry: ToolRegistry | None = None,
        min_confidence: float = 0.30,
        min_word_count: int = 1,
        max_no_speech_prob: float = 0.6,
        output_dir: str = "outputs",
        web_server: WebServer | None = None,
    ) -> None: ...
    def run(self, hotkey_key: str, mute_key: str = "") -> None: ...  # blocks until shutdown
    def shutdown(self) -> None: ...
    def on_scroll_lock(self) -> None: ...        # scroll-lock hotkey callback
    def on_mute_toggle(self) -> None: ...        # mute-key hotkey callback
```

**What it does:** The top-level orchestrator. `build_streaming_daemon(cfg)` factory constructs and wires all concrete subsystems, including a one-shot `resolver._set_config(cfg.llm)` to inject fuzzy-threshold config into the parameter resolver. `run()` calls `Transcriber.load()` (blocking model preload), starts the pipeline worker thread, starts the hotkey listener, installs SIGINT handler, and blocks on `threading.Event.wait(0.5)` (polled to allow Ctrl+C on Windows — see `gotchas.md` §9). `on_scroll_lock()` is the scroll-lock hotkey callback: calls `StreamingRecorder.open_session()` or `close_session()` depending on current session state. `on_mute_toggle()` is the mute-key callback: suspends or resumes the audio stream within an open session. The pipeline worker drains `utt_q` and runs `transcribe → word-count gate → no_speech_prob gate → confidence gate → LLMRouter.route() → Dispatcher.run_plan()`. Async WAV write (`outputs/last_utterance.wav`) and JSON plan dump (`outputs/last_plan.json`) are submitted to a single-threaded `ThreadPoolExecutor` after every utterance for post-mortem debugging. `shutdown()` is idempotent — multiple calls are safe. `__main__.py` handles `--validate` mode (runs `validate_or_die()` then exits), acquires a single-instance OS-level lock to prevent duplicate daemon processes, configures logging, logs environment diagnostics, installs a crash reporter, and then calls `build_streaming_daemon(Config.load()).run(cfg.hotkey.key, cfg.hotkey.mute_key)`. The pipeline worker calls `LLMRouter.route(transcript)` for every utterance that passes the gates. If `route()` returns a `Plan`, `Dispatcher.run_plan()` executes it. If `route()` returns `None`, `FeedbackSink.on_miss()` fires directly and the worker loops back to `utt_q`.

**Who calls it:** `__main__.py` (the process entry point), signal handlers, and `on_scroll_lock` / `on_mute_toggle` (hotkey-listener thread).

**Who it calls:** All other subsystems. It is the only place where concrete implementations are wired to interfaces.

**How it is tested:** Integration tests bypass `HotkeyController` and `StreamingRecorder` entirely — they inject utterance ndarrays directly into `utt_q` and assert that the right tool function was called end-to-end. Unit tests for individual subsystems do not involve `StreamingDaemon`.

---

### 4.9 `LLMRouter`

```python
class LLMRouter:
    def __init__(self, config: LLMRouterConfig, registry: ToolRegistry, reload_lock: threading.Lock) -> None: ...
    def route(self, transcript: str, env_context: str | None = None) -> Plan | None: ...
    def reload_prompt(self) -> None: ...
    def composed_prompt_data(self) -> dict[str, Any]: ...
    def warmup(self) -> bool: ...
    def close(self) -> None: ...
    @property
    def metrics(self) -> dict[str, Any]: ...
```

**What it does:** One-shot tool-call planner via local LM Studio. `route()` sends the transcript to the configured LM Studio endpoint as an OpenAI-compatible chat completion with `tool_choice="required"`. It parses the response into a `Plan` of `ToolCall` steps. Returns `None` on timeout, connection error, HTTP error, malformed response, no tool_calls in response, or if the LLM calls `no_match`. `warmup()` posts a real chat-completion request with a synthetic transcript and `max_tokens=1` to prefill LM Studio's KV cache before the first real utterance. Returns `True` on success, `False` on any error. `close()` shuts down the underlying `httpx.Client`. Tracks simple metrics (total calls, timeouts, errors, avg latency). `reload_prompt()` re-reads the external template file and rebuilds the cached system prompt — called by the web layer under `reload_lock` after a template save. `composed_prompt_data()` returns structured data (raw template, resolved template, placeholders, tools array, model/endpoint info) for the Prompt Inspector UI.

**Who calls it:** `StreamingDaemon._process_utterance()` directly, on every utterance that passes the confidence/word-count gates.

**Who it calls:** `httpx.Client` for HTTP, `ToolRegistry.all_llm_visible()` to build the tools array.

**How it is tested:** Unit tests with `httpx`-mocked responses covering: happy path single/multi-step plans, timeout, connection error, HTTP errors, malformed JSON, no tool_calls, no_match sentinel, max_plan_steps cap, metrics counters.

---

### 4.10 `Plan` / `ToolCall`

```python
@dataclass(frozen=True)
class ToolCall:
    name: str
    kwargs: dict[str, Any]

@dataclass(frozen=True)
class Plan:
    steps: tuple[ToolCall, ...]
    raw_response: dict[str, Any]
    strict: bool = True   # halt on first step failure (True) or continue-on-error (False)
```

**What it does:** Immutable value objects representing the LLM router's output. `Plan` holds an ordered tuple of `ToolCall` steps and a `strict` flag (default `True`) that controls whether `Dispatcher.run_plan()` halts on the first step failure or continues executing remaining steps. `raw_response` preserves the full LLM JSON for debugging.

---

### 4.11 `Validator`

```python
def validate(registry: ToolRegistry, store: ToolMetadataStore) -> list[str]: ...
def validate_or_die(registry: ToolRegistry, store: ToolMetadataStore) -> None: ...
```

**What it does:** Startup validator catching drift between Python tool signatures and TOML metadata. Seven rules: (1) every tool has TOML, (2) every sig param has TOML arg description, (3) no orphan TOML args, (4) all params use supported types, (5) settle_ms in [0, 5000], (6) llm_only tools have no phrases, (7) required primitives (no_match, wait) are registered and llm_only. `validate_or_die()` prints errors and exits if any fail.

**Who calls it:** `build_streaming_daemon()` factory at startup, after discovery and metadata binding.

**How it is tested:** Unit tests with crafted registries/metadata triggering each rule individually. Happy path returns empty error list.

---

## 5. Data Flow (Happy Path)

**Session open:**
1. User presses Scroll Lock. `pynput` fires `on_scroll_lock` on the listener thread.
2. `on_scroll_lock` checks `StreamingRecorder._session_active`: false → `StreamingRecorder.open_session()` + `feedback.on_recording_start()` (chime).
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
14. `LLMRouter.route(result.text)` → `Plan | None`.
14a. If `None` returned: pipeline worker calls `FeedbackSink.on_miss(text, ())` directly and loops back to `utt_q`.
14b. If `Plan` returned: `Dispatcher.run_plan(text, plan, registry)` executes multi-step plan (per-step INFO log; `resolver.resolve_window` / `resolve_app` called inside `focus` / `open` as needed).
15. Pipeline worker loops back to `utt_q`.

**Session close:**
17. User presses Scroll Lock again. `on_scroll_lock` → `StreamingRecorder.close_session()` + `feedback.on_recording_stop()` (chime).
18. Stream stops; VAD worker receives `None` sentinel, joins cleanly.

Total latency budget (speech-end detected → tool fires): ~700 ms target, 1.5 s hard ceiling.

---

## 7. Web UI Subsystem

The command management web UI runs as an embedded FastAPI server on a uvicorn daemon thread inside the main process.

### Thread Topology

```
[Main thread]           [HotkeyCtrl thread]    [Pipeline threads]    [uvicorn thread]
Daemon orchestrator     pynput listener        transcribe→route→     FastAPI app
owns registry,                                 dispatch worker       handles HTTP
reload_lock                                    shares ToolRegistry   uses reload_lock
```

### Components

- **`ToolMetadataStore`** — reads/writes sidecar TOML files next to tool Python modules. Atomic writes via tmp+rename. Per-tool file locking via `portalocker`.
- **`WebServer`** — wraps uvicorn on a daemon thread. Port-bump fallback (8765 → 8775). Graceful shutdown via `should_exit`.
- **`FastAPI app`** — 11 routes: dashboard, healthz, edit form, cancel edit, save, toggle, kwargs-form fragment, prompt inspect, prompt edit, prompt save, prompt tools. HTMX fragments for partial page updates.
- **`reload_lock`** — `threading.Lock` guards registry mutations. Held μs for reads, ms for saves.

### Data Flow (Save Cycle)

1. User clicks Edit → `GET /tool/{name}/edit` → HTMX swaps card to form.
2. User selects a primitive → `GET /command/kwargs-form?primitive=<name>&mode=guided` → HTMX swaps `#kwargs-section` with schema-driven field inputs sourced from `ToolEntry.args_meta` (ADR 0057).
3. User edits → Save → `POST /tool/{name}` with form data (either `kwarg_*` guided fields or `kwargs_json` in advanced mode).
4. Server validates (non-empty phrases, no duplicates).
5. Acquires per-tool file lock → atomic TOML write → release.
6. Acquires `reload_lock` → `registry.reload_metadata()` → release.
7. Returns updated card fragment → HTMX swaps form back to card.
8. `LLMRouter`'s next `route()` call uses the updated registry metadata via `ToolRegistry.all_llm_visible()`.

---

## 7. EventBus Subsystem

### Component

```python
class EventBus:
    def publish(self, event_type: str, data: dict | None = None) -> None: ...
    def subscribe(self) -> asyncio.Queue[Event]: ...
    def unsubscribe(self, q: asyncio.Queue[Event]) -> None: ...
    def replay_after(self, last_id: int) -> list[Event]: ...
```

**What it does:** Thread-safe pub/sub broker. `publish()` is called from daemon threads (hotkey, pipeline, heartbeat). Each SSE connection calls `subscribe()` to get a bounded `asyncio.Queue` (max 1024 events, drop-oldest on overflow). 100-event ring buffer supports `Last-Event-ID` reconnect replay.

**Who calls it:** `StreamingDaemon` (session/mute/warmup events), `Dispatcher` (tool_fired/miss/tool_error), heartbeat thread (1 Hz daemon_heartbeat).

**Who consumes it:** FastAPI `/events` SSE endpoint → sprite process via httpx-sse.

### SSE Endpoint

`GET /events` — `text/event-stream`. One JSON event per SSE frame. Keepalive every 30s. `Last-Event-ID` header rewinds through ring buffer.

---

## 8. Sprite Companion (Separate Process)

### Architecture

```
voice-commander daemon (process A)            voice_sprite (process B)
───────────────────────────────────           ─────────────────────────
HotkeyCtrl ──▶ VADGate ──▶ Dispatcher         httpx SSE client
                              │                     │
                              ▼                     ▼
                         EventBus ──SSE──▶   StateMachine
                              ▲                     │
                         FastAPI /events             ▼
                         (uvicorn thread)     pyglet Window
                                              (topmost, click-through,
                                               transparent, no titlebar)
```

### Module layout

- `state_machine.py` — 11 states, event→state mapping, heartbeat timeout
- `event_client.py` — httpx-sse with exponential backoff reconnect
- `charsheet.py` — TOML parser + PNG bounds validator
- `plan_outcome_handler.py` — `handle_plan_outcome`: parses `plan_outcome` SSE event dict into a `ChatLog` entry; called directly from the SSE thread
- `sprite_renderer.py` — frame selection + animation timing
- `window.py` — pyglet Window with Win32 click-through flags
- `speech_bubble.py` — fading last-command label
- `dpi.py` — per-monitor DPI scaling
- `win32_flags.py` — WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE

### Lifecycle

`voice-commander-supervisor` spawns the sprite once before the first daemon spawn. Sprite outlives daemon restarts; daemon never checks sprite health. Sprite polls daemon heartbeat over SSE; 3 s timeout → CRASHED state → SSE reconnect loop. On daemon restart the sprite reconnects automatically once the new daemon binds the web port.

---

---

## 10. Command HUD

### Components

- **`PlanOutcome`** (daemon — `src/voice_commander/plan.py`) — frozen
  dataclass carrying the full outcome of one command cycle.
- **`handle_plan_outcome`** (sprite — `src/voice_sprite/plan_outcome_handler.py`) — parses the `plan_outcome` SSE event dict, calls `Summarizer.summarize()`, and appends a `ChatLogEntry` directly from the SSE thread. Parse failures are swallowed; summarizer exceptions fall back to the raw transcript.
- **`Summarizer`** (sprite — `src/voice_sprite/summarizer.py`) — routes
  rule → chain-detector → LLM fallback → raw fallback.
- **`RULES` + `CHAIN_DETECTORS`** (sprite — `summary_rules.py`).
- **`LLMSummaryClient`** (sprite — `llm_summary_client.py`) — httpx client
  for LM Studio; 800 ms deadline; returns None on any failure.
- **`ChatLog`** (sprite — `chat_log.py`) — ring buffer + fade curve. `append()` is called from the SSE thread (not deferred to the pyglet main thread).
- **`ChatLogRenderer`** (sprite — `chat_log_renderer.py`) — pyglet labels.

### Event

```text
plan_outcome {
  transcript: str,
  steps: [{name, kwargs}, ...],
  status: "ok" | "error" | "miss",
  failed_step_index: int | null,
  error_msg: str | null,
  duration_ms: int,
}
```

Published by `Dispatcher.run_plan` (ok / error),
`StreamingDaemon._process_utterance` (miss on `route()=None` and on
confidence-gate drop), and `StreamingDaemon._process_utterance`
(error when `_registry is None` after routing).

---

## 11. Cursor-Follow Sprite (Multi-Monitor)

30 Hz `pyglet.clock` tick polls `GetCursorPos`, dispatches to
`MonitorFromPoint`, and (when the monitor changes) reads `MONITORINFO.rcWork`
+ `GetDpiForMonitor` to compute a new window size + location. Anchor point
is the sprite column's bottom-right — NOT the window top-left — so the
HUD's leftward extension does not push the sprite off the monitor.

`PER_MONITOR_AWARE_V2` MUST be set before the first pyglet window is
constructed; `main()` now calls `SetProcessDpiAwarenessContext(-4)` as
its first post-logging step. See ADR 0053 for the full gotcha list.

---

## 12. Command / Workflow Store (`GraphStore`)

The unified on-disk store for all graph definitions — both commands and workflows.
Replaces the previous `CommandStore` + `WorkflowStore` pair.

```python
class GraphStore:
    def __init__(self, path: Path, *, kind: Literal["command", "workflow"]) -> None: ...
    def load_all(self) -> dict[str, Graph]: ...
    def save_one(self, g: Graph) -> None: ...
    def delete(self, name: str) -> bool: ...
```

Each store instance is locked to a single `kind` (`"command"` or `"workflow"`). The
backing file uses the canonical schema:

```json
{
  "schema_version": 1,
  "graphs": {
    "<graph_name>": { "...": "GraphDef body" }
  }
}
```

Legacy files (old `commands`/`workflows` key with primitive/steps shape) are rejected
with a `GraphStoreError` that points at `scripts/migrate-graphs.py`.

`seed_if_missing(target, default_source)` is a first-run helper that copies the bundled
starter-pack JSON if the user-data path is absent.

**Module:** `src/voice_commander/commands/store.py`

---

## 13. Graph Registrar (`register_graphs` / `reload_all`)

```python
def register_graphs(
    registry: ToolRegistry,
    store: GraphStore,
    *,
    runtime_factory: Callable[[ToolRegistry, Callable[[str], Graph | None]], GraphRuntime] | None = None,
    peer_graphs: dict[str, Graph] | None = None,
) -> list[str]: ...

def reload_all(
    registry: ToolRegistry,
    command_store: GraphStore,
    workflow_store: GraphStore,
    *,
    runtime_factory: Callable[[ToolRegistry, Callable[[str], Graph | None]], GraphRuntime] | None = None,
) -> tuple[list[str], list[str]]: ...
```

**What it does:** Converts `Graph` value objects from `GraphStore.load_all()` into
`ToolEntry` closures registered in `ToolRegistry`. Each graph's `inputs[]` are mapped to
an OpenAI-compatible JSON schema so the LLM can supply typed kwargs. `register_graphs`
drops all existing entries with the matching `origin` before re-registering — making it
idempotent and hot-reload safe. When called for a single store, pass `peer_graphs` to let
the runtime resolve cross-store references (e.g. a workflow calling a command graph).
`reload_all` is the daemon startup and web-UI post-save
path: it builds a shared `GraphRuntime` that knows about both command and workflow graphs
(so cross-graph calls work).

`Graph.llm_visible = False` sets `ToolEntry.internal = True`, excluding the tool from
`LLMRouter.all_llm_visible()` without removing it from dispatch.

**Module:** `src/voice_commander/commands/registrar.py`

---

## 14. Supervisor

See §Process tree above and ADR 0058. The `voice-commander-supervisor` process owns
both the daemon child and the sprite child. The daemon's `/restart` web route triggers
`os._exit(75)`. The supervisor's wait loop recognizes code 75 and respawns the daemon.
Sprite is never restarted by the supervisor in normal operation.

---

## 15. Graph Runtime

The node-graph execution subsystem. Introduced by the Node-Graph Builder feature
(ADR 0062–0068).

### Value objects — `commands/graph.py`

```python
@dataclass(frozen=True)
class PortRef:
    node_id: str
    port: str

@dataclass(frozen=True)
class Edge:
    src: PortRef        # upstream output port
    dst: PortRef        # downstream input port

@dataclass(frozen=True)
class Node:
    id: str
    tool: str           # fully-qualified tool name, e.g. "pipeline.press"
    kwargs: dict[str, Any]

@dataclass(frozen=True)
class GraphInput:
    name: str
    type: str           # "str", "int", "bool", "float"
    required: bool
    description: str | None

@dataclass(frozen=True)
class Graph:
    name: str
    kind: Literal["command", "workflow"]
    description: str
    synonyms: tuple[str, ...]
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    inputs: tuple[GraphInput, ...]
    outputs: tuple[PortRef, ...]
    llm_visible: bool = True
    enabled: bool = True
```

### JSON schema — `commands/graph_schema.py`

```python
def parse_graph(raw: dict[str, Any]) -> Graph: ...
def serialise_graph(g: Graph) -> dict[str, Any]: ...
```

Canonical JSON uses `schema_version: 1`. `GraphSchemaError` is raised on any
structural violation.

### Topological sort — `commands/graph_topo.py`

```python
def topo_sort(nodes: Iterable[Node], edges: Iterable[Edge]) -> list[Node]: ...
```

Kahn's algorithm over node ids. Raises `CycleError` on cycles.

### DAG executor — `commands/graph_runtime.py`

```python
class GraphRuntime:
    def __init__(
        self,
        registry: ToolRegistry,
        lookup: Callable[[str], Graph | None],
    ) -> None: ...

    def run(
        self,
        graph: Graph,
        call_kwargs: dict[str, Any],
    ) -> tuple[PlanOutcome, dict[str, Any]]: ...
```

`run()` resolves nodes in topological order. For each node:
1. Builds the kwargs dict from: (a) baked-in `node.kwargs`, then (b) any data-edge
   values from upstream ports, which take precedence.
2. Calls `ToolRegistry.by_name(node.tool).func(**merged_kwargs)`.
3. Stores the return value on an output port keyed by `node.id + ".result"`.
4. Foreach nodes iterate their body once per item (see gotchas §28).

Returns a `PlanOutcome` (same wire format as linear plans — see §10) and a
`dict[output_port → value]` of the graph's declared output ports.

**The `LLMRouter.route()` and `Dispatcher.run_plan()` interfaces are unchanged.**
Graphs are registered as `ToolEntry` closures; the LLM sees them as flat tool calls.

### Validation — `commands/graph_validator.py`

```python
def validate(graph: Graph, registry: ToolRegistry) -> list[str]: ...

class ValidationError(ValueError): ...
```

Seven rules:
1. All node tool names are registered.
2. No duplicate node ids.
3. No duplicate edge src/dst pairs.
4. Edge src port names match the upstream tool's known output ports.
5. Edge dst port names match the downstream tool's known input parameters.
6. No cycles (calls `topo_sort` and catches `CycleError`).
7. Foreach body is non-empty (at least one reachable node from `<foreach_node>.item`).

### Drawflow adapter — `commands/graph_drawflow.py`

```python
def from_drawflow(export: dict[str, Any]) -> Graph: ...
def to_drawflow(graph: Graph) -> dict[str, Any]: ...
```

Translates between Drawflow's native export shape and canonical `Graph` value objects.
Used by the builder route to receive canvas saves and serve canvas loads.

### Migration — `commands/graph_migrate.py`

```python
def migrate(src_path: Path, dst_path: Path) -> int: ...
```

Reads legacy `commands.json` / `workflows.json` (old primitive/steps shape) and writes
the canonical DAG schema. Returns the count of migrated graphs. Invoked by
`scripts/migrate-graphs.py`; never called in the hot path.

---

## 16. Builder UI

The Drawflow node-graph canvas served at `/page/builder`.

### Routes — `web/builder.py`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/page/builder` | Full builder page (three-column layout) |
| `GET` | `/builder/palette` | Tool palette HTMX fragment |
| `GET` | `/builder/graph/{name}` | Load a graph into Drawflow JSON format |
| `POST` | `/builder/graph/{name}/save` | Save canvas export → canonical JSON → `GraphStore` |
| `POST` | `/builder/graph/{name}/validate` | Run `graph_validator.validate` and return errors |
| `GET` | `/builder/graph/new` | Blank canvas stub |

### Frontend — `static/builder.js`

Initialises the Drawflow canvas, populates the drag-and-drop tool palette from the
tool registry, serialises the canvas on save, and calls the `/validate` endpoint on
demand. Self-contained; no npm build step.

### Layout — `templates/page_builder.html`

Three-column layout: left sidebar (palette), centre (canvas), right sidebar (graph
metadata form). HTMX drives all save/validate interactions without a full-page reload.

### Vendored library — `static/drawflow.min.{js,css}`

Drawflow 0.0.60. Single-file vendored bundle; no CDN dependency; upgraded by
deliberate file replacement (ADR 0062).

---

## 9. See Also

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
  - [`decisions/0040-llm-only-routing-replaces-hybrid.md`](decisions/0040-llm-only-routing-replaces-hybrid.md)
  - [`decisions/0041-rapidfuzz-for-parameter-resolution.md`](decisions/0041-rapidfuzz-for-parameter-resolution.md)
  - [`decisions/0042-resolver-module-design.md`](decisions/0042-resolver-module-design.md)
  - [`decisions/0043-nine-verb-primitive-catalog.md`](decisions/0043-nine-verb-primitive-catalog.md)
  - [`decisions/0044-few-shot-system-prompt.md`](decisions/0044-few-shot-system-prompt.md)
  - [`decisions/0045-sprite-separate-process-via-sse.md`](decisions/0045-sprite-separate-process-via-sse.md)
  - [`decisions/0046-pyglet-over-tkinter-pyqt-web-overlay.md`](decisions/0046-pyglet-over-tkinter-pyqt-web-overlay.md)
  - [`decisions/0047-charsheet-grid-format-sidecar-toml.md`](decisions/0047-charsheet-grid-format-sidecar-toml.md)
  - [`decisions/0048-eventbus-sse-outbound-telemetry.md`](decisions/0048-eventbus-sse-outbound-telemetry.md)
  - [`decisions/0049-miss-chimes-retained.md`](decisions/0049-miss-chimes-retained.md)
