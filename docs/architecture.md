# Voice Commander — Architecture

**Date:** 2026-04-19
**Status:** Authoritative
**Source spec:** [superpowers/specs/2026-04-19-voice-commander-design.md](superpowers/specs/2026-04-19-voice-commander-design.md)

This document is the canonical reference for Voice Commander's subsystem design and component contracts. It expands Sections 2 and 3 of the design spec into a standalone, agent-readable form. All type stubs in Section 3 are normative — implementations must match these signatures exactly.

---

## 1. High-level Flow

```
┌──────────────┐   key   ┌──────────────┐   WAV    ┌──────────────┐
│  HotkeyCtrl  │────────▶│   Recorder   │─────────▶│ Transcriber  │
│ (pynput)     │ toggle  │ (sounddevice)│  path    │(faster-whisp)│
└──────────────┘         └──────────────┘          └──────┬───────┘
                                                          │ text
                                                          ▼
┌──────────────┐  result ┌──────────────┐  tool,   ┌──────────────┐
│ FeedbackSink │◀────────│  Dispatcher  │◀─────────│   Matcher    │
│ (chime+log)  │         │ (invokes fn) │  score   │ (rapidfuzz)  │
└──────────────┘         └──────────────┘          └──────┬───────┘
                                                          │ looks up
                                                          ▼
                                                   ┌──────────────┐
                                                   │ ToolRegistry │
                                                   │ (@tool decor)│
                                                   └──────┬───────┘
                                                          │ imports
                                                          ▼
                                                   ┌──────────────┐
                                                   │   tools/*    │
                                                   │ copy, paste… │
                                                   └──────────────┘
```

---

## 2. Subsystem Boundaries

Six subsystems connected by the `Daemon` orchestrator. Each is independently unit-testable:

| Subsystem | Responsibility | Key dependency |
|---|---|---|
| `HotkeyController` | Listen for Scroll Lock, fire `on_toggle` | `pynput` |
| `Recorder` | Capture microphone audio to WAV file | `sounddevice`, `soundfile` |
| `Transcriber` | WAV → text using preloaded model | `faster-whisper` (CUDA) |
| `ToolRegistry` | Register/discover `@tool`-decorated functions | stdlib (`importlib`) |
| `Matcher` | Fuzzy-match transcript → tool | `rapidfuzz` |
| `Dispatcher` | Invoke tool function, report outcome | (no external) |
| `FeedbackSink` | Chimes + log | `winsound` |

---

## 3. Threading Model

Three long-lived threads plus the main thread:

1. **Main thread** — starts the daemon, installs signal handlers, blocks on `shutdown_event`. Does no real work.
2. **Hotkey listener thread** — owned by `pynput`. Fires `on_toggle()` as a callback on this thread. Keep callbacks tiny (start/stop the recorder; do not do real work here).
3. **Recorder callback thread** — owned by `sounddevice`. Appends PCM frames to an in-memory buffer while recording is active. `stop()` flushes to WAV, enqueues the path, returns.
4. **Worker thread** — drains `queue.Queue[Path]`, runs `Transcriber → Matcher → Dispatcher` sequentially. One at a time; if the user records again before the previous run finishes, the new WAV queues up.

Graceful shutdown: Ctrl+C or SIGTERM sets `shutdown_event`; worker drains queue, stops listener, unloads model.

Rationale: keeping capture and inference off the hotkey-listener thread is the whole reason threading matters here. `pynput` callbacks that block will freeze key dispatch. Audio capture callbacks that do heavy work will glitch recordings.

---

## 4. Component Contracts

### 4.1 `HotkeyController`

```python
class HotkeyController:
    def __init__(self, key: str, on_toggle: Callable[[], None]) -> None: ...
    def start(self) -> None: ...   # non-blocking; spawns pynput listener
    def stop(self) -> None: ...
```

**What it does:** Listens for a single configurable key (default `"scroll_lock"`) using `pynput`'s global keyboard listener. Each key-release event fires `on_toggle` exactly once. It resolves the string key name to a `pynput.keyboard.Key` member at construction time and validates it; invalid keys raise `ValueError` before the daemon starts.

**Who calls it:** `Daemon.__init__` constructs it; `Daemon.run()` calls `start()`; `Daemon.shutdown()` calls `stop()`.

**Who it calls:** `on_toggle` callback (injected by `Daemon`). That callback does nothing heavy — it just checks `Recorder.is_recording` and delegates to `Recorder.start()` or `Recorder.stop()`.

**How it is tested:** A fake `on_toggle` callable is injected. Key events are driven via `pynput.keyboard.Controller` in a test thread. Tests assert the toggle fired the expected number of times and that the callback is never invoked concurrently with itself (re-entrant safety).

---

### 4.2 `Recorder`

```python
class Recorder:
    def __init__(
        self,
        output_dir: Path,
        channels: int = 1,
        device: int | None = None,
    ) -> None: ...

    def start(self) -> None: ...
    def stop(self) -> Path: ...
    @property
    def is_recording(self) -> bool: ...
    @property
    def actual_sample_rate(self) -> int | None: ...
```

**What it does:** Opens a `sounddevice.InputStream` on `start()`, accumulates PCM frames into an in-memory list via the stream callback, then on `stop()` writes the accumulated frames to a single fixed WAV file `outputs/recorded.wav` and returns the `Path`. Each call to `stop()` overwrites the same file — no timestamps, no UUIDs, no retention policy.

The sample rate is **not configured** — it is determined each time `start()` is called by querying the chosen device's `default_samplerate` via `sounddevice.query_devices()`. This ensures WASAPI devices (which only accept their native rate, e.g. 48 kHz) work correctly. The WAV is written at the device-native rate and `actual_sample_rate` exposes it as a read-only property. Phase-2's `faster-whisper` resamples the audio to 16 kHz internally on ingest, so no resampling is required at the capture layer.

Raises `RuntimeError` if `stop()` is called while `is_recording` is `False`.

**Who calls it:** `Daemon` (via the `on_toggle` closure on the hotkey-listener thread). `stop()` returns a `Path` that `Daemon` immediately enqueues on the worker queue.

**Who it calls:** `sounddevice.InputStream` internally; `soundfile.write` to flush the WAV. No outbound calls to other Voice Commander subsystems.

**How it is tested:** Either `sounddevice` virtual-device mode is used, or the stream callback is mocked to feed synthetic PCM frames directly. Tests verify: WAV file exists after `stop()` at the fixed path `recorded.wav`, correct sample rate and channel count, `RuntimeError` on double-stop, and that a second record/stop cycle overwrites the same file (only one WAV in `output_dir`).

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
    def transcribe(self, wav: Path) -> TranscriptionResult: ...
```

**What it does:** Wraps `faster_whisper.WhisperModel`. `load()` is a blocking call that downloads/caches and loads the model weights into GPU VRAM — it is called once at daemon startup so `transcribe()` never incurs cold-start latency. `transcribe()` runs inference on the supplied WAV and returns a `TranscriptionResult`. Language is pinned to English (`small.en` is English-only so no language detection overhead). `confidence` is computed as the mean of each segment's `avg_logprob`, clamped to `[0, 1]` via `max(0.0, min(1.0, (mean_logprob + 1.0)))` — values below `config.transcription.min_confidence` (default `0.30`) are treated as misses by `Dispatcher` regardless of fuzzy score.

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

### 4.8 `Daemon`

```python
class Daemon:
    def __init__(self, config: Config) -> None: ...
    def run(self) -> None: ...   # blocks until shutdown
    def shutdown(self) -> None: ...
```

**What it does:** The top-level orchestrator. `__init__` calls `discover()` to populate the `ToolRegistry`, then constructs all concrete subsystems (`HotkeyController`, `Recorder`, `Transcriber`, `Matcher`, `Dispatcher`, `FeedbackSink`) wired together. `run()` calls `Transcriber.load()` (blocking model preload), starts the hotkey listener, starts the worker thread, installs signal handlers for SIGINT/SIGTERM, and then blocks on `threading.Event` until `shutdown()` is called. `shutdown()` sets the event, drains the worker queue, stops the hotkey listener, and unloads the model. `__main__.py` does exactly one thing: `Daemon(Config.load()).run()`.

**Who calls it:** `__main__.py` (the process entry point) and signal handlers.

**Who it calls:** All other subsystems. It is the only place where concrete implementations are wired to interfaces.

**How it is tested:** Integration tests bypass `HotkeyController` and `Recorder` entirely — they inject WAV paths directly into the worker queue and assert that the right tool function was called end-to-end. Unit tests for individual subsystems do not involve `Daemon`.

---

## 5. Data Flow (Happy Path)

1. User presses Scroll Lock. `pynput` fires `on_toggle` on the listener thread.
2. `on_toggle` checks `Recorder.is_recording`: false → `Recorder.start()` + `feedback.on_recording_start()` (chime).
3. User speaks for ~2 s.
4. User presses Scroll Lock again. `on_toggle` → `Recorder.stop()` returns `wav_path`; `feedback.on_recording_stop()` (chime).
5. `wav_path` enqueued on the worker queue.
6. Worker thread picks it up: `Transcriber.transcribe(wav_path)` → `TranscriptionResult`.
7. `feedback.on_transcript(...)` logs transcript.
8. `Matcher.match(result.text)` → `MatchResult`.
9. `Dispatcher.dispatch(text, match)`:
   - Match above threshold → logs match + tool function executes.
   - Below threshold → miss beep + logs miss.
10. Worker loops back to queue.

Total latency budget (recording stop → tool fires): ~700 ms target, 1.5 s hard ceiling.

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
