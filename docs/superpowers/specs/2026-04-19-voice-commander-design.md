# Voice Commander — Design Spec

**Date:** 2026-04-19
**Status:** Draft, awaiting user review
**Author:** Brainstorming session (Sakib + Claude)

---

## 1. Purpose & Vision

A Windows-native, local-first voice command launcher. The user presses a hotkey, speaks a command in natural English, and the matching tool runs. No cloud dependency, no LLM in the MVP.

The long-term vision is a Talon Voice-style tool where the user speaks **actual commands** ("copy this", "new tab", "focus browser") rather than memorizing spoken shortcuts. The MVP uses fuzzy matching against a phrase registry; a future phase will add a small local LLM for argument-bearing commands ("open readme in projects folder").

### Non-goals (explicit)

- No cloud ASR (OpenAI, Azure, Google).
- No always-on wake word in the MVP (push-to-toggle only).
- No LLM tool-calling in the MVP (phase 6+ work).
- No cross-platform support in the MVP (Windows 11 only; Linux/Mac are possible later but not designed-for).
- No GUI beyond native toasts + system tray icon.

### Success criteria

- Latency: end-of-speech → tool fires in under 1.5 s on an NVIDIA GPU.
- Match accuracy: ≥ 95 % on the documented MVP phrase list when spoken naturally.
- Stability: daemon runs for 24 h without memory growth or crashes.
- Developer experience: adding a new tool requires dropping one file with a `@tool(...)` decorated function and writing a unit test. Nothing else.

---

## 2. Architecture

### 2.1 High-level flow

```
┌──────────────┐   key   ┌──────────────┐   WAV    ┌──────────────┐
│  HotkeyCtrl  │────────▶│   Recorder   │─────────▶│ Transcriber  │
│ (pynput)     │ toggle  │ (sounddevice)│  path    │(faster-whisp)│
└──────────────┘         └──────────────┘          └──────┬───────┘
                                                          │ text
                                                          ▼
┌──────────────┐  result ┌──────────────┐  tool,   ┌──────────────┐
│ FeedbackSink │◀────────│  Dispatcher  │◀─────────│   Matcher    │
│(toast/chime) │         │ (invokes fn) │  score   │ (rapidfuzz)  │
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

### 2.2 Subsystem boundaries

Six subsystems connected by the `Daemon` orchestrator. Each is independently unit-testable:

| Subsystem | Responsibility | Key dependency |
|---|---|---|
| `HotkeyController` | Listen for Scroll Lock, fire `on_toggle` | `pynput` |
| `Recorder` | Capture microphone audio to WAV file | `sounddevice`, `soundfile` |
| `Transcriber` | WAV → text using preloaded model | `faster-whisper` (CUDA) |
| `ToolRegistry` | Register/discover `@tool`-decorated functions | stdlib (`importlib`) |
| `Matcher` | Fuzzy-match transcript → tool | `rapidfuzz` |
| `Dispatcher` | Invoke tool function, report outcome | (no external) |
| `FeedbackSink` | Chimes + native Windows toast + log | `winsound`, `windows_toasts` |

### 2.3 Threading model

Three long-lived threads plus the main thread:

1. **Main thread** — starts the daemon, installs signal handlers, blocks on `shutdown_event`. Does no real work.
2. **Hotkey listener thread** — owned by `pynput`. Fires `on_toggle()` as a callback on this thread. Keep callbacks tiny (start/stop the recorder; do not do real work here).
3. **Recorder callback thread** — owned by `sounddevice`. Appends PCM frames to an in-memory buffer while recording is active. `stop()` flushes to WAV, enqueues the path, returns.
4. **Worker thread** — drains `queue.Queue[Path]`, runs `Transcriber → Matcher → Dispatcher` sequentially. One at a time; if the user records again before the previous run finishes, the new WAV queues up.

Graceful shutdown: Ctrl+C or SIGTERM sets `shutdown_event`; worker drains queue, stops listener, unloads model.

Rationale: keeping capture and inference off the hotkey-listener thread is the whole reason threading matters here. `pynput` callbacks that block will freeze key dispatch. Audio capture callbacks that do heavy work will glitch recordings.

### 2.4 Directory layout

```
voice-commander/
├── pyproject.toml
├── config.toml
├── CLAUDE.md
├── README.md
├── docs/
│   ├── index.md
│   ├── architecture.md
│   ├── decisions/              # ADRs
│   ├── gotchas.md
│   ├── libraries.md
│   ├── testing-strategy.md
│   ├── references/             # vendored framework docs
│   └── superpowers/
│       ├── specs/              # this file lives here
│       └── plans/
├── src/voice_commander/
│   ├── __init__.py
│   ├── __main__.py             # `python -m voice_commander`
│   ├── daemon.py
│   ├── hotkey.py
│   ├── recorder.py
│   ├── transcriber.py
│   ├── matcher.py
│   ├── dispatcher.py
│   ├── feedback.py
│   ├── registry.py
│   ├── config.py
│   └── tools/
│       ├── __init__.py
│       ├── clipboard.py
│       ├── window.py
│       ├── browser.py
│       └── system.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│       └── audio/              # canned WAVs for transcriber tests
├── outputs/                    # rolling WAVs, gitignored
└── assets/sounds/              # start.wav, stop.wav, miss.wav
```

---

## 3. Component Contracts

### 3.1 `HotkeyController`

```python
class HotkeyController:
    def __init__(self, key: str, on_toggle: Callable[[], None]) -> None: ...
    def start(self) -> None: ...   # non-blocking; spawns pynput listener
    def stop(self) -> None: ...
```

- `key` is a string like `"scroll_lock"`, resolved to a `pynput.keyboard.Key` member.
- `on_toggle` is invoked once per key-press event (fires on key release).
- Re-entrant safety: the controller guarantees the callback is never called concurrently with itself.

### 3.2 `Recorder`

```python
class Recorder:
    def __init__(
        self,
        output_dir: Path,
        sample_rate: int = 16000,
        channels: int = 1,
        device: int | None = None,
    ) -> None: ...

    def start(self) -> None: ...
    def stop(self) -> Path: ...
    @property
    def is_recording(self) -> bool: ...
```

- Writes a WAV named `rec-YYYYMMDD-HHMMSS-{uuid8}.wav` into `output_dir`.
- Mono, 16 kHz, 16-bit PCM — matches whisper's expected input; no resampling step.
- If `stop()` is called while not recording, raises `RuntimeError`. (Caller — the daemon — is responsible for checking `is_recording` before calling `stop`.)
- Rolling cleanup: on daemon start, keep the newest `retention_count` WAVs; delete older ones.

### 3.3 `Transcriber`

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

- Uses `faster_whisper.WhisperModel` under the hood. Model is preloaded on daemon startup; `transcribe()` must not incur cold-start latency.
- Language is pinned to English (`small.en` is English-only).
- `confidence` is a normalized mean of segment-level `avg_logprob`, clipped to `[0, 1]`, used for downstream decisions (e.g., very low confidence → miss regardless of fuzzy score).

### 3.4 `ToolRegistry`

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

- Registration is idempotent: re-registering the same `name` raises `DuplicateToolError`.
- Phrases are normalized on registration: lowercase, single-space collapsed, stripped punctuation.

### 3.5 `Matcher`

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

- Uses `rapidfuzz.process.extract(scorer=rapidfuzz.fuzz.WRatio)` to get top-5.
- Normalizes the utterance the same way phrases are normalized at registration.
- Below threshold → `tool is None`, `phrase is None`, but `candidates` is still populated for logging.
- Ties at the top score are broken by tool-name alphabetical order (deterministic, testable).

### 3.6 `Dispatcher`

```python
class Dispatcher:
    def __init__(self, feedback: FeedbackSink) -> None: ...
    def dispatch(self, transcript: str, match: MatchResult) -> None: ...
```

- If `match.tool is None` → `feedback.on_miss(transcript, match.candidates)`.
- Else → `feedback.on_match(...)`, call `match.tool.func()` in `try/except`. On exception → `feedback.on_error(...)` and log full traceback.
- Tool execution runs on the worker thread. Tools must not block for more than a few hundred ms (they're pyautogui keystroke sends).

### 3.7 `FeedbackSink`

```python
class FeedbackSink(Protocol):
    def on_recording_start(self) -> None: ...
    def on_recording_stop(self) -> None: ...
    def on_transcript(self, text: str, confidence: float) -> None: ...
    def on_match(self, tool: str, phrase: str, score: float) -> None: ...
    def on_miss(self, transcript: str, candidates: Sequence[tuple[str, str, float]]) -> None: ...
    def on_error(self, subsystem: str, err: BaseException) -> None: ...
```

Implementations:
- `WindowsFeedbackSink` — chimes via `winsound.PlaySound(path, SND_FILENAME | SND_ASYNC)`, toasts via `windows_toasts.InteractableWindowsToaster` (fire-and-forget, WinRT).
- `NullFeedbackSink` — does nothing. Default in unit tests.
- `CapturingFeedbackSink` — records calls for test assertions.

Toast content on match: `✓ {tool}  ·  "{transcript}"  ({score:.0f})`.
Toast content on miss: `✗ no match  ·  "{transcript}"  ·  top: {candidate} ({score:.0f})`.

### 3.8 `Daemon`

```python
class Daemon:
    def __init__(self, config: Config) -> None: ...
    def run(self) -> None: ...   # blocks until shutdown
    def shutdown(self) -> None: ...
```

- Builds the registry (discovers tools), instantiates all concretes, starts the listener, blocks on `threading.Event`.
- `__main__.py` just does `Daemon(Config.load()).run()`.

---

## 4. Data flow (happy path)

1. User presses Scroll Lock. `pynput` fires `on_toggle` on the listener thread.
2. `on_toggle` asks `Recorder.is_recording`: false → `Recorder.start()` + `feedback.on_recording_start()` (chime).
3. User speaks for ~2 s.
4. User presses Scroll Lock again. `on_toggle` → `Recorder.stop()` returns `wav_path`; `feedback.on_recording_stop()` (chime).
5. `wav_path` enqueued on the worker queue.
6. Worker thread picks it up: `Transcriber.transcribe(wav_path)` → `TranscriptionResult`.
7. `feedback.on_transcript(...)` logs transcript (and in dev mode, shows a debug toast).
8. `Matcher.match(result.text)` → `MatchResult`.
9. `Dispatcher.dispatch(text, match)`:
   - Match above threshold → chime + toast + tool function executes.
   - Below threshold → miss beep + miss toast.
10. Worker loops back to queue.

Total latency budget (recording stop → tool fires): ~700 ms target, 1.5 s hard ceiling.

---

## 5. Configuration

Single `config.toml` at project root. Missing values fall back to defaults in `config.py`.

```toml
[hotkey]
key = "scroll_lock"

[audio]
sample_rate = 16000
channels = 1
device = -1               # -1 = system default
retention_count = 100
output_dir = "outputs"

[transcription]
model_size = "small.en"
device = "cuda"
compute_type = "float16"
min_confidence = 0.30     # below this, transcript is treated as unreliable

[matching]
threshold = 85.0
scorer = "WRatio"

[feedback]
sounds_dir = "assets/sounds"
start_sound = "start.wav"
stop_sound = "stop.wav"
miss_sound = "miss.wav"
toast_enabled = true
toast_show_transcript = true

[logging]
level = "INFO"
file = "voice-commander.log"
```

`Config` loads this into a frozen dataclass tree. No runtime mutation.

---

## 6. Error Handling

| Failure | Detection | Behavior |
|---|---|---|
| No mic device | `sounddevice` raises on `start()` | Log error, toast "No microphone", do not start recorder |
| CUDA not available | Whisper model load fails | Log error, toast "CUDA unavailable — check drivers", daemon exits (fail-fast — user's decision to run CUDA config) |
| Empty / silent recording | Whisper returns empty text | Miss beep + miss toast "empty transcript" |
| Very low ASR confidence | `result.confidence < min_confidence` | Same as miss, regardless of fuzzy score |
| Below fuzzy threshold | `match.tool is None` | Miss beep + miss toast with top candidates |
| Tool function raises | `try/except` in `Dispatcher` | Log traceback, error toast "tool X failed: {msg}", daemon continues |
| Worker thread dies | Uncaught exception in worker loop | Log, toast, restart the worker thread once; if it dies again, exit |
| Hotkey already held by another app | `pynput` listener setup fails | Log, exit with actionable message |

All error paths produce a log line and a user-visible toast (except the fatal exits, which still log).

---

## 7. Testing Strategy

### 7.1 Unit tests (per subsystem, fast, no hardware)

- `HotkeyController` — inject a fake `on_toggle`; drive key events via `pynput.keyboard.Controller`. Assert toggle fired N times.
- `Recorder` — use `sounddevice` in virtual device mode, or mock the stream callback to feed synthetic PCM. Verify WAV written, shape, sample rate.
- `Transcriber` — load model once per session (fixture); transcribe fixture WAVs in `tests/fixtures/audio/`. Assert exact-match on deterministic clips and fuzzy-contains on harder ones.
- `ToolRegistry` — register fakes, assert `all() / by_name() / flat_phrases()` results; assert duplicate raises.
- `Matcher` — build a stub registry, feed utterances, assert expected tool, score above threshold, and candidate ordering.
- `Dispatcher` — use `CapturingFeedbackSink`; assert the right callback is invoked, and tool errors produce `on_error`.
- `FeedbackSink` — validate `WindowsFeedbackSink` smoke tests only (chime plays, toast dispatches without raising); behavior covered by `CapturingFeedbackSink` in dispatch tests.
- `Config` — round-trip load/save, defaults applied when keys missing, invalid values raise.

### 7.2 Integration tests (canned WAVs, no mic, no hotkey)

End-to-end from WAV → tool function call, bypassing `HotkeyController` and `Recorder`. Each MVP command has a fixture WAV (recorded once, checked in). Assert the right tool function was called.

### 7.3 Hardware-in-the-loop tests (manual, human-driven)

Checklist per phase. Example for Phase 3:
- Select text in Notepad.
- Press Scroll Lock. Hear start chime.
- Say "copy".
- Press Scroll Lock. Hear stop chime.
- See toast "✓ copy · 'copy' (100)".
- Paste elsewhere — text matches.

Phase checklists live in `docs/testing-strategy.md`.

### 7.4 Soak test

Phase 5 gate: run the daemon 24 h with a scripted phrase generator piping WAVs through the worker queue every 30 s. Assert: no memory growth > 100 MB, no unhandled exceptions, log file well-formed.

---

## 8. Phase plan (validation gates)

| Phase | Scope | Validation |
|---|---|---|
| **0 — Scaffolding + docs** | `uv` project, directory layout, all doc files (CLAUDE.md, index, architecture, gotchas, libraries, testing, ADRs for locked decisions). No `src/` code. | Doc review checklist passes. |
| **1 — Hotkey + audio** | `HotkeyController`, `Recorder`, start/stop chimes, WAVs land in `outputs/`. | Press key, speak, stop. WAV exists and plays back. |
| **2 — Transcription** | `Transcriber` (CUDA, `small.en`), preload on daemon start, transcripts logged. | Speak, see transcript in log within ~1 s. |
| **3 — Router + registry + one tool** | `ToolRegistry`, `@tool` decorator, `Matcher`, `Dispatcher`, `FeedbackSink` (toast + chime), `copy` tool end-to-end. | Select text, say "copy", paste elsewhere — works. Toast shows match. |
| **4 — Full MVP toolset** | Remaining 13 tools (clipboard, window, browser, system). Phrase tuning. Miss beep wired. | Full run-through of 14 tools. Miss rate measured. |
| **5 — Hardening** | `config.toml`, retention cleanup, system tray icon (pystray), error recovery, complete test suite, 24 h soak. | Suite green, soak passes, tray icon visible and interactive. |
| **6+ (future)** | Local LLM intent router, per-app command sets, wake word. Out of MVP. | N/A |

---

## 9. Library choices (locked, each with ADR)

| Purpose | Library | Alternative considered | Rationale (short) |
|---|---|---|---|
| Hotkey listener | `pynput` | `keyboard` | `pynput` is cross-platform and doesn't require admin; `keyboard` is simpler but Windows-specific and sometimes needs admin. `keyboard` is the fallback. |
| Audio capture | `sounddevice` + `soundfile` | `pyaudio` | Cleaner API, PortAudio backing, easier to test, actively maintained; `pyaudio` has Windows install pain. |
| ASR | `faster-whisper` | `openai-whisper`, `whisper.cpp` | CTranslate2 backend gives best GPU throughput; mature Python API. |
| Fuzzy match | `rapidfuzz` | `thefuzz`, `fuzzywuzzy` | Written in C++, 10-100x faster, drop-in compatible. |
| Key sending | `pyautogui` | `pynput.keyboard.Controller` | `pyautogui` has simpler multi-key hotkey API; good enough for MVP. |
| Toasts | `windows_toasts` | `win11toast`, Tkinter popups | WinRT-backed, fire-and-forget, no thread issues; Tkinter is the classic footgun we're explicitly avoiding. |
| Chimes | stdlib `winsound` | `playsound`, `pygame` | Zero extra deps, async playback supported. |
| Tray icon (Phase 5) | `pystray` | `infi.systray`, `plyer` | Clean API, actively maintained. |
| Package manager | `uv` | `poetry`, `pip` | Fast installs, reproducible `uv.lock`, pyproject.toml-native. |

Every row above gets a full ADR in `docs/decisions/`.

---

## 10. Documentation plan

Before any code lands:

- `docs/index.md` — table of contents, reading order.
- `docs/architecture.md` — full architecture (expands on Section 2 of this spec).
- `docs/decisions/*` — one ADR per row in Section 9 + one for the phased delivery model + one for the threading model. Format: **Context · Decision · Consequences · Alternatives considered**.
- `docs/gotchas.md` — Windows-specific traps (Scroll Lock LED state, CUDA DLL loader paths, PortAudio device indices drifting across reboots, WinRT toast permissions, pynput callback threading).
- `docs/libraries.md` — long-form rationale for every dep in `pyproject.toml`.
- `docs/testing-strategy.md` — unit / integration / human-validation matrix + per-phase checklists.
- `docs/references/` — vendored framework docs (faster-whisper, rapidfuzz, sounddevice, pynput, windows_toasts, pyautogui, pystray, uv), gathered by parallel sub-agents during Phase 0.

---

## 11. Workflow (spec → plan → tickets → execution)

1. **This spec** is reviewed by the user.
2. On approval, `superpowers:writing-plans` turns it into a phased implementation plan at `docs/superpowers/plans/`.
3. The `kaizenos-cli` skill creates a Kaizen OS **Area** ("Voice Commander") with an **Epic** per phase and **Subquests** per deliverable within each phase.
4. Each subquest has explicit acceptance criteria drawn from Sections 7 and 8.
5. Agents claim subquests one at a time. Humans validate at every phase boundary before the next phase is unlocked.
6. New decisions during implementation generate new ADRs at decision time, not after.

---

## 12. Open items (resolved before Phase 1)

- Exact mic device selection strategy when multiple are present (default vs. config index). Currently spec'd as `device = -1` meaning "system default"; may need an enumeration helper.
- Tray icon UX for Phase 5 (right-click menu items: reload config, open output dir, quit).
- Whether Scroll Lock LED toggling is harmless or needs to be suppressed (`pynput` behavior on Windows — verify in Phase 1).

These don't block the plan but need answers before their respective phases.

---

## 13. Sign-off

- [ ] User (Sakib) approves this spec.
- [ ] Handoff to `superpowers:writing-plans` to produce the implementation plan.
- [ ] Handoff to `kaizenos-cli` to create Area + Epics + Subquests.
