# Library Rationale

Every runtime and development dependency in `pyproject.toml`, plus `uv` (the package manager) and `winsound` (stdlib), is documented here. Each section explains what the library does in this project, what alternatives were evaluated, why this library won, and why the version pin is set where it is.

Cross-references to Architecture Decision Records live in [`docs/decisions/`](decisions/).

---

## `pynput` — Hotkey listener

**Purpose in this project:** `pynput` powers `HotkeyController`, which listens for the Scroll Lock key globally (system-wide, not just when the window is focused) and fires an `on_toggle` callback that starts or stops audio recording. The listener runs on its own thread managed by `pynput`.

**Alternatives considered:** `keyboard` (PyPI package by BoppreH) is the most common alternative. It is simpler and its API is ergonomic for single-key global hooks.

**Why `pynput` won:** `pynput` does not require elevated privileges on Windows. `keyboard` sometimes requires running as Administrator to grab low-level key events, which is a blocker for a tool meant to run as a normal user daemon. `pynput` is also cross-platform by design, which keeps the door open for a future Linux or macOS port. `keyboard` remains documented as the fallback if `pynput` causes unexpected issues.

**Pin reason:** `>=1.7.7` is the first release with stable Windows backend fixes for media keys. No upper bound — we trust semantic versioning here.

**ADR:** [`decisions/0002-pynput-over-keyboard.md`](decisions/0002-pynput-over-keyboard.md)

---

## `sounddevice` — Microphone capture

**Purpose in this project:** `sounddevice` drives the `StreamingRecorder` subsystem. It opens a `sd.InputStream` at the device's native sample rate (e.g. 48 kHz on WASAPI) when a session is opened. The PortAudio callback copies each PCM chunk to `raw_q` without blocking; the VAD worker thread drains `raw_q`, resamples to 16 kHz via `soxr`, and feeds frames to `VADGate`. The stream is closed when the session ends.

**Alternatives considered:** `pyaudio` is the historical standard for PortAudio bindings in Python.

**Why `sounddevice` won:** `pyaudio` requires a compiled native extension that historically has been painful to install on Windows (missing `portaudio.dll`, mismatched architectures). `sounddevice` ships wheels with PortAudio bundled and installs cleanly with `uv add`. Its callback-based streaming API is also cleaner and more Pythonic than `pyaudio`'s blocking read loop. Both libraries ultimately wrap the same PortAudio C library, so there is no capability difference for our use case.

**Pin reason:** `>=0.4.7` is the version that introduced `dtype` support for float32 arrays and stable `InputStream` callback semantics required for the streaming pipeline.

**ADR:** [`decisions/0003-sounddevice-over-pyaudio.md`](decisions/0003-sounddevice-over-pyaudio.md)

---

## `soundfile` — WAV writing

**Purpose in this project:** `soundfile` serialises the in-memory PCM buffer to a `.wav` file that is then passed to the transcriber. It is a thin wrapper around `libsndfile` and handles all the WAV header encoding, sample format conversion, and channel interleaving automatically.

**Alternatives considered:** `scipy.io.wavfile` (stdlib-adjacent, in scipy) and `wave` (stdlib). `wave` can only write PCM without any dependency, but its API is low-level and does not handle numpy arrays natively.

**Why `soundfile` won:** `soundfile` accepts numpy arrays directly, supports all the sample rates and bit depths we care about, and pairs naturally with `sounddevice` — the two libraries share the same PortAudio-idiomatic conventions. Using `wave` would require manual byte-packing. `scipy` would add a heavyweight dependency for a single function call.

**Pin reason:** `>=0.12.1` for stable `numpy` 2.x compatibility in the WAV write path.

**ADR:** [`decisions/0003-sounddevice-over-pyaudio.md`](decisions/0003-sounddevice-over-pyaudio.md) (same ADR covers the audio stack)

---

## `numpy` — Numerical arrays

**Purpose in this project:** `numpy` is the glue between `sounddevice` (which yields raw PCM frames as numpy arrays) and `soundfile` (which writes numpy arrays to disk). It is also used internally by `faster-whisper` for audio preprocessing.

**Alternatives considered:** Not meaningfully substitutable — `sounddevice` and `faster-whisper` both require numpy as a hard dependency.

**Why `numpy` won:** It is the de facto standard for numerical array work in Python and is a transitive requirement of several other deps.

**Pin reason:** `>=1.26` is the first release series with full Python 3.11 support and stable `dtype` semantics for 16-bit PCM arrays. No upper bound; we rely on the ecosystem's own compatibility guarantees.

**ADR:** [`decisions/0003-sounddevice-over-pyaudio.md`](decisions/0003-sounddevice-over-pyaudio.md)

---

## `faster-whisper` — Speech-to-text (ASR)

**Purpose in this project:** `faster-whisper` powers the `Transcriber` subsystem. At daemon startup, it loads the `small.en` Whisper model into GPU VRAM via CTranslate2. On each recording, `Transcriber.transcribe(wav_path)` runs inference and returns a `TranscriptionResult` with the recognised text, language, duration, and a normalised confidence score derived from segment-level log-probabilities.

**Alternatives considered:**
- `openai-whisper` — the reference implementation from OpenAI. Correct and well-documented, but runs on PyTorch, which adds a multi-GB dependency and is significantly slower on GPU than CTranslate2's optimised CUDA kernels.
- `whisper.cpp` — C++ port with Python bindings. Excellent performance, but the Python bindings (`pywhispercpp`) are less mature and require a separate native build step.

**Why `faster-whisper` won:** CTranslate2 provides the best GPU throughput of the pure-Python options. Benchmarks show 4–8× speed-up over `openai-whisper` on the same hardware for the `small` model. The API is clean and idiomatic Python, and the `vad_filter=True` option suppresses hallucinations on silence — a specific problem with Whisper described in `gotchas.md`.

**Pin reason:** `>=1.0.3` introduced the stable `WhisperModel.transcribe()` API with `vad_filter` support. The `1.x` line is the first semantically-versioned stable series.

**ADR:** [`decisions/0004-faster-whisper-cuda.md`](decisions/0004-faster-whisper-cuda.md)

---

## `rapidfuzz` — Parameter resolution (fuzzy scoring for tool arguments)

**Purpose in this project:** `rapidfuzz` is **no longer used for command matching** — that path was removed with ADR 0040. It is retained for a narrower role: scoring fuzzy tool-argument strings onto concrete OS objects inside the `resolver` module. Specifically:

- `resolver.resolve_window(target)` scores every visible window as `max(WRatio(target, proc_name), WRatio(target, title))` and picks argmax above `focus_fuzzy_threshold` (default 70).
- `resolver.resolve_app(target)` scores `target` against the cached `(display_name, launch_token)` list assembled from Start-Menu `.lnk` files and `shell:AppsFolder`, picking argmax above `open_fuzzy_threshold` (default 70).
- `primitives._verify_open(target)` polls `EnumWindows` post-launch and uses `WRatio` ≥ 60 to confirm a new window actually appeared.

**Alternatives considered:** A plain Levenshtein implementation (stdlib `difflib.SequenceMatcher` or a custom function). `WRatio` combines several algorithms (`partial_ratio`, `token_sort_ratio`, `ratio`) in a way that handles acronyms, word-order reshuffles, and partial overlaps much better than plain Levenshtein.

**Why `rapidfuzz` won:** It was already a project dependency for the old matcher, and no new install pain. Sub-millisecond `WRatio` over lists of ~200 AppsFolder entries keeps parameter resolution off the critical latency path. Reimplementing `WRatio` correctly is more code than the dependency costs.

**Pin reason:** `>=3.9.0` for the stable `rapidfuzz.fuzz.WRatio` API used by the resolver.

**ADR:** [`decisions/0041-rapidfuzz-for-parameter-resolution.md`](decisions/0041-rapidfuzz-for-parameter-resolution.md) — supersedes the *matching* use in [`decisions/0005-rapidfuzz-matching.md`](decisions/0005-rapidfuzz-matching.md), but keeps the dependency for parameter resolution.

---

## `pyautogui` — Key and mouse automation

**Purpose in this project:** `pyautogui` is used inside individual tool functions (in `src/voice_commander/tools/`) to send keystrokes and hotkeys to the active window. For example, the `copy` tool calls `pyautogui.hotkey('ctrl', 'c')`, and `paste` calls `pyautogui.hotkey('ctrl', 'v')`.

**Alternatives considered:** `pynput.keyboard.Controller` — already a dependency for the hotkey listener, so using it for key-sending would avoid an extra package. `win32api`/`SendInput` via `pywin32` — the most accurate Windows simulation, but requires `pywin32` and more boilerplate.

**Why `pyautogui` won:** `pyautogui.hotkey()` sends multi-key combinations with correct inter-key timing in a single call, which is simpler than the `pynput.keyboard.Controller.press()/release()` dance. For the MVP toolset (clipboard, window management, browser navigation), `pyautogui` is expressive enough and well-documented. `pynput.keyboard.Controller` remains usable if a specific tool needs lower-level control.

**Pin reason:** `>=0.9.54` for Python 3.11 compatibility. The `0.9.x` series is the current stable branch.

**ADR:** [`decisions/0002-pynput-over-keyboard.md`](decisions/0002-pynput-over-keyboard.md) (covers keyboard automation broadly)

---

## ~~`windows-toasts`~~ — REMOVED (ADR 0013)

Originally used for WinRT toast notifications in `WindowsFeedbackSink`. Removed in ADR 0013 (2026-04-20) for two reasons:

1. **UX:** toasts are interruptive for a tool used in short, repeated bursts. Audio chimes already communicate recording state and match/miss outcomes from the peripheral channel (ear) without pulling focus.
2. **Technical:** eager import of `windows_toasts` at module scope corrupts the process-wide state CTranslate2 relies on when loading cuDNN kernels, causing silent `STATUS_ACCESS_VIOLATION` crashes during `WhisperModel.__init__()`. A lazy-import workaround shipped in commit `e3a8fe7` was judged too fragile to keep long-term — one careless refactor reintroduces the crash.

**ADR:** [`decisions/0013-drop-winrt-toasts-audio-only-feedback.md`](decisions/0013-drop-winrt-toasts-audio-only-feedback.md) supersedes [`decisions/0007-windows-toasts-over-tkinter.md`](decisions/0007-windows-toasts-over-tkinter.md).

See also [`docs/gotchas.md`](gotchas.md) §10 for the crash diagnosis (kept as a cautionary case study).

---

## `silero-vad` — Voice activity detection

**Purpose in this project:** `silero-vad` powers the `VADGate` subsystem inside `StreamingRecorder`. It segments a continuous audio stream into discrete utterances by detecting speech onset and offset. Each complete utterance is emitted as a numpy ndarray to the pipeline worker queue for transcription.

**Alternatives considered:**
- `webrtcvad` — Google's WebRTC VAD, a classic lightweight option. Energy-based heuristic; fast, but produces many false positives on background noise and does not model speech well at the phoneme level.
- `pysilero-vad` / `silero-vad` (torch variant) — the original PyTorch-based silero-vad. Requires a full PyTorch install (multi-GB), which is overkill when the ONNX export exists.

**Why `silero-vad` won:** The ONNX-exported silero-vad model runs in under 1 ms per 30 ms frame on CPU via `onnxruntime`, requires no GPU, and achieves high accuracy on real-world microphone audio. MIT licensed. The `VADIterator` API provides a clean frame-in / utterance-out interface that maps directly to the VAD worker thread's loop. See `gotchas.md` §12 for thread-safety constraints.

**Pin reason:** `>=5.1` for the stable ONNX export and `VADIterator` API with configurable speech/silence thresholds and minimum silence duration. The 5.x series is the current actively maintained branch.

**ADR:** [`decisions/0016-silero-vad-over-webrtcvad.md`](decisions/0016-silero-vad-over-webrtcvad.md). Context on why this ONNX engine was selected over `webrtcvad` and the torch variant. Session model and streaming architecture in [`decisions/0015-vad-streaming-mode.md`](decisions/0015-vad-streaming-mode.md).

---

## `onnxruntime` — ONNX model runtime

**Purpose in this project:** `onnxruntime` is the CPU inference backend for silero-vad. `silero-vad` distributes its model as an ONNX file; `onnxruntime` loads and runs it. No GPU execution path is needed for VAD — the model is small enough that CPU inference meets the real-time budget comfortably.

**Alternatives considered:**
- `onnxruntime-gpu` — the GPU-enabled variant. Unnecessary for this use case and adds CUDA dependency complexity to the VAD subsystem.
- `torch` — silero-vad can also run via PyTorch. Rejected because PyTorch is multi-GB and already avoided elsewhere in the stack.

**Why `onnxruntime` won:** It is the canonical runtime for ONNX models, actively maintained by Microsoft, and the CPU-only wheel installs cleanly without CUDA toolchain requirements. The CPU-only variant keeps the VAD subsystem free of GPU dependencies, which is correct — VAD runs concurrently with GPU transcription on separate threads.

**Pin reason:** `>=1.16.1` is the first release with stable `InferenceSession` behaviour on Python 3.11 and Windows. Required transitively by silero-vad.

**ADR:** [`decisions/0016-silero-vad-over-webrtcvad.md`](decisions/0016-silero-vad-over-webrtcvad.md) (covers the choice of ONNX runtime path over the torch variant and thread-safety constraints).

---

## `soxr` — High-quality streaming audio resampler

**Purpose in this project:** `soxr` powers the `Resampler` subsystem inside `StreamingRecorder`. The microphone captures at the device-native rate (typically 48 kHz via WASAPI). silero-vad and faster-whisper both require 16 kHz input. `soxr.ResampleStream` converts the raw 48 kHz float32 frames to 16 kHz in real time, chunk by chunk, on the VAD worker thread.

**Alternatives considered:**
- `scipy.signal.resample` — batch resampler, not streaming. Would require buffering a full utterance before resampling, adding latency and complexity.
- `librosa.resample` — high quality, but batch-only and adds a heavyweight dependency.
- `soundfile` + `samplerate` — `samplerate` wraps libsamplerate (SRC), which is a valid streaming alternative, but `soxr` consistently benchmarks faster and produces fewer aliasing artifacts at the ratios used here (48k→16k = 3:1).

**Why `soxr` won:** `soxr` wraps libsoxr, which is widely regarded as the highest-quality open-source resampler. The `ResampleStream` API provides true streaming resampling with internal state management — chunks go in, resampled chunks come out, with the polyphase FIR filter state maintained across calls. This maps exactly to the VAD worker's chunk-by-chunk processing loop. See `gotchas.md` §13 for state lifetime constraints.

**Pin reason:** `>=0.3.7` for the stable `ResampleStream` Python API and Windows wheel availability. The `0.3.x` series is the current stable branch.

**ADR:** [`decisions/0017-soxr-streaming-resampler.md`](decisions/0017-soxr-streaming-resampler.md). Covers rejection of `scipy.signal.resample_poly` and `librosa.resample` as non-streaming alternatives.

---

## `tomli` — TOML parsing (Python < 3.11 only)

**Purpose in this project:** `tomli` is a conditional dependency (`python_version < '3.11'`) that provides TOML parsing on older Python releases. `config.py` loads `config.toml` via `tomllib` (stdlib in Python 3.11+) with a fallback `import tomli as tomllib` for older environments.

**Alternatives considered:** `tomllib` is in the standard library from Python 3.11. `toml` (PyPI) is the older community library but has known edge-case bugs and is no longer actively maintained.

**Why `tomli` won:** `tomli` is the library that was upstreamed into CPython as `tomllib`. The two are specification-identical. Using `tomli` as the backport ensures consistent behaviour across all supported Python versions.

**Pin reason:** `>=2.0.1` for TOML 1.0 compliance. Since the project requires `python >= 3.11` this dep is effectively a no-op on any supported interpreter, but remains for explicit documentation of the intent.

**ADR:** [`decisions/0011-uv-package-manager.md`](decisions/0011-uv-package-manager.md) (covers project toolchain decisions)

---

## `pystray` — System tray icon (optional, Phase 5)

**Purpose in this project:** `pystray` (installed via the `[tray]` optional-dependency group) provides the system tray icon in Phase 5. The tray icon shows daemon status (recording / idle / error) and exposes a right-click menu for reloading config, opening the output directory, and gracefully quitting.

**Alternatives considered:**
- `infi.systray` — Windows-only tray library. Functional but lacks cross-platform design and is less actively maintained.
- `plyer` — has a tray icon API, but it is thin and lacks right-click menu customisation.

**Why `pystray` won:** Clean Python API, actively maintained, cross-platform (Windows/Linux/macOS), and handles the Win32 message loop details that make raw tray icon implementations tricky. The optional-dep placement means the core daemon runs without it in Phases 0–4.

**Pin reason:** `>=0.19.5` for stable `pystray.Menu` and `pystray.MenuItem` API with proper icon update support.

**ADR:** No dedicated ADR yet (Phase 5 scope).

---

## `Pillow` — Image handling for tray icon (optional, Phase 5)

**Purpose in this project:** `Pillow` (also in the `[tray]` optional group) is a `pystray` dependency: it is used to load and render the PNG icon image into the tray. `pystray` accepts `PIL.Image` objects as icons on all platforms.

**Alternatives considered:** Not meaningfully substitutable for `pystray` icon loading.

**Why `Pillow` won:** It is the required image backend for `pystray` on Windows. No standalone rationale needed.

**Pin reason:** `>=10.2.0` for Python 3.11 compatibility and CVE-clean status. Pillow has had security fixes in the 10.x series that make a lower bound important.

**ADR:** No dedicated ADR yet (Phase 5 scope — pulls in Pillow as a pystray dependency).

---

## `winsound` — Audio chimes (stdlib)

**Purpose in this project:** `winsound` provides the start, stop, and miss chimes in `WindowsFeedbackSink`. `winsound.PlaySound(path, SND_FILENAME | SND_ASYNC)` plays a WAV file asynchronously on a system audio thread without blocking the hotkey or worker thread.

**Alternatives considered:**
- `playsound` (PyPI) — cross-platform, but has known issues with async playback on Windows and has been effectively unmaintained since 2021.
- `pygame.mixer` — correct and well-tested, but adds a large multimedia dependency for a single audio-playback call.

**Why `winsound` won:** Zero extra dependencies — it is part of the Python standard library on Windows. The `SND_ASYNC` flag gives non-blocking playback. For short WAV chimes (< 1 s), this is exactly the right tool. No pin needed; it ships with Python.

**ADR:** [`decisions/0008-winsound-for-chimes.md`](decisions/0008-winsound-for-chimes.md)

---

## `uv` — Package and project manager

**Purpose in this project:** `uv` manages the virtual environment, resolves and installs all dependencies from `pyproject.toml`, and produces the `uv.lock` file that pins the exact dependency graph for reproducible installs. All `uv add`, `uv sync`, and `uv run` commands are used in place of `pip install` and `python -m`.

**Alternatives considered:**
- `poetry` — the previous generation of Python project managers. Correct lockfile semantics, but significantly slower dependency resolution and a non-standard `pyproject.toml` schema.
- `pip` + `pip-tools` — the classic approach. Works, but requires multiple tools for lock file management and is slow on fresh installs.
- `hatch` — modern, but less ecosystem momentum and slower installs than `uv`.

**Why `uv` won:** `uv` is 10–100× faster than `pip` and `poetry` for environment creation and dependency resolution. It is fully `pyproject.toml`-native (PEP 517/518/621 compliant), produces a deterministic `uv.lock`, and is the recommended tool for new Python projects in 2025+. The `[tool.uv]` section in `pyproject.toml` activates `uv`'s build-system integration.

**Pin reason:** `uv` is a standalone binary managed outside `pyproject.toml`; its version is tracked in CI configuration. No PyPI pin.

**ADR:** [`decisions/0011-uv-package-manager.md`](decisions/0011-uv-package-manager.md)

---

## `pytest` — Test runner (dev)

**Purpose in this project:** `pytest` is the test framework for all unit and integration tests in `tests/`. Custom markers (`hardware`, `integration`) are declared in `pyproject.toml` so that hardware-requiring tests can be excluded in CI.

**Alternatives considered:** `unittest` (stdlib) — works, but lacks fixtures, parametrize, and the plugin ecosystem. No real alternative is worth considering here.

**Why `pytest` won:** Industry standard. Rich fixture system (`conftest.py`), parametrize, and plugins like `pytest-cov` make it the right default for any Python project.

**Pin reason:** `>=8.1.0` for the `--strict-markers` flag behaviour and `tmp_path` fixture improvements used in recorder tests.

**ADR:** [`decisions/0009-phased-delivery-with-hitl-gates.md`](decisions/0009-phased-delivery-with-hitl-gates.md) (covers the testing philosophy and phase gate model)

---

## `pytest-cov` — Coverage reporting (dev)

**Purpose in this project:** `pytest-cov` adds `--cov` flags to `pytest` invocations and produces HTML/terminal coverage reports. Used to verify that the unit test suite covers all subsystems before phase gates.

**Alternatives considered:** `coverage` (the underlying tool) can be run standalone, but `pytest-cov` integration is more ergonomic.

**Why `pytest-cov` won:** Standard pytest plugin; seamless integration with `pytest` runs.

**Pin reason:** `>=5.0.0` for `pytest` 8.x compatibility.

**ADR:** [`decisions/0009-phased-delivery-with-hitl-gates.md`](decisions/0009-phased-delivery-with-hitl-gates.md)

---

## `ruff` — Linter and formatter (dev)

**Purpose in this project:** `ruff` enforces code style and catches common errors on every commit and in CI. The `pyproject.toml` `[tool.ruff.lint]` section enables rules from the `E`, `F`, `I`, `UP`, `B`, and `SIM` rule sets (pycodestyle errors, pyflakes, isort, pyupgrade, flake8-bugbear, flake8-simplify). Line length is 100.

**Alternatives considered:** `flake8` + `isort` + `black` — the classic trio. Correct, but three tools to configure and run. `pylint` — comprehensive but very slow and noisy.

**Why `ruff` won:** `ruff` replaces `flake8`, `isort`, and `black` with a single Rust-backed tool that runs orders of magnitude faster. It is now the de facto standard for new Python projects. Single config block in `pyproject.toml`, no separate config files needed.

**Pin reason:** `>=0.4.0` for stable `SIM` and `UP` rule support and the `pyproject.toml`-first configuration model.

**ADR:** [`decisions/0011-uv-package-manager.md`](decisions/0011-uv-package-manager.md) (covers the dev toolchain)

---

## `mypy` — Static type checker (dev)

**Purpose in this project:** `mypy` runs in strict mode (`strict = true`) against all `src/voice_commander/` modules. The project uses type annotations throughout (return types, `dataclass(frozen=True)`, `Protocol`, `Callable`). `mypy` catches type errors before runtime.

**Alternatives considered:** `pyright` / `pylance` — Microsoft's type checker. Faster, excellent VS Code integration. Either would work here.

**Why `mypy` won:** `mypy` is the reference implementation of PEP 484 type checking and the CI-standard choice. Its strict mode is well-understood and widely documented. `pyright` can be added later as a second opinion if desired.

**Pin reason:** `>=1.10.0` for `strict` mode stability with Python 3.11 and the `warn_unused_ignores` flag required in `pyproject.toml`.

**ADR:** [`decisions/0011-uv-package-manager.md`](decisions/0011-uv-package-manager.md) (covers the dev toolchain)

---

## FastAPI

- **Version**: >=0.115.0
- **Purpose**: Web framework for the command management dashboard.
- **Why**: Modern async framework, built-in OpenAPI, perfect for HTMX fragment serving. Embedded as uvicorn thread inside daemon process.

---

## uvicorn

- **Version**: >=0.30.0 (with standard extras)
- **Purpose**: ASGI server running FastAPI app.
- **Why**: Production-quality, supports running as a thread (not just CLI), graceful shutdown via `should_exit`.

---

## Jinja2

- **Version**: >=3.1.0
- **Purpose**: Server-side HTML templating for the dashboard.
- **Why**: FastAPI's native template engine. Renders full pages and HTMX fragments.

---

## portalocker

- **Version**: >=2.8.0
- **Purpose**: Cross-platform file locking for sidecar TOML writes.
- **Why**: Prevents corruption when two browser tabs save the same tool's TOML simultaneously. Works on Windows (uses `msvcrt.locking`).

---

## HTMX

- **Version**: 2.0.4 (vendored)
- **Purpose**: HTML-over-the-wire for the dashboard UI.
- **Why**: Zero JS build step. Server returns HTML fragments, HTMX swaps them into the DOM. Eliminates SPA complexity.

---

## `httpx` — HTTP client for LLM router

**Purpose in this project:** `httpx` powers the `LLMRouter` subsystem's communication with local LM Studio. It sends OpenAI-compatible chat completion requests to `localhost:1234/v1/chat/completions` and parses the JSON responses. Used instead of `requests` for modern async support and configurable timeouts (separate connect/read/write/pool deadlines).

**Alternatives considered:**
- `requests` — the classic Python HTTP library. Functional, but lacks fine-grained timeout control (only a single timeout value, not split connect/read/write).
- `urllib3` — lower-level, more boilerplate for JSON request/response handling.
- `aiohttp` — async-only, overkill for synchronous pipeline worker thread usage.

**Why `httpx` won:** `httpx` provides a `requests`-like API with per-phase timeout configuration (`httpx.Timeout(connect=..., read=..., write=..., pool=...)`), which is critical for the LLM router's latency budget. The synchronous `httpx.Client` runs cleanly on the pipeline worker thread. Connection pooling and keep-alive are built in.

**Pin reason:** `>=0.27.0` for stable `httpx.Timeout` and `httpx.Client` APIs. Runtime dependency (moved from dev to runtime when LLM router shipped).

**ADR:** No dedicated ADR yet (LLM router decisions tracked in ADRs 0026+).

---

## `pyglet` — Sprite companion renderer

**Purpose in this project:** `pyglet` renders the on-screen sprite companion in the `voice_sprite` process. It provides a transparent, borderless, always-on-top, click-through OpenGL window for displaying animated pixel-art frames from the character sheet. The pyglet event loop runs on the main thread; the SSE client runs on a daemon thread.

**Alternatives considered:** Tkinter (limited transparency support), PyQt (100+ MB dependency), Electron overlay (heavyweight), web overlay via browser.

**Why `pyglet` won:** Native per-pixel alpha transparency via OpenGL. Built-in sprite-sheet primitives (TextureGrid, Animation). Direct HWND access for Win32 extended style flags. ~2 MB dependency vs 100+ MB for Qt.

**Pin reason:** `>=2.1.3` for stable Windows transparency and `pyglet.window.Window` HWND access API.

**ADR:** [`decisions/0046-pyglet-over-tkinter-pyqt-web-overlay.md`](decisions/0046-pyglet-over-tkinter-pyqt-web-overlay.md)

---

## `httpx-sse` — SSE client for sprite companion

**Purpose in this project:** `httpx-sse` provides the `connect_sse()` context manager used by `voice_sprite.event_client.SSEClient` to consume the daemon's `/events` SSE stream. Wraps httpx with proper SSE frame parsing and `Last-Event-ID` support.

**Alternatives considered:** Raw `httpx` streaming (manual SSE parsing), `aiohttp` (async-only), `sseclient-py` (unmaintained).

**Why `httpx-sse` won:** Already using `httpx` for the LLM router. `connect_sse()` adds SSE-specific parsing (event type, data, id fields) without switching HTTP libraries. Sync API fits the daemon-thread model.

**Pin reason:** `>=0.4.0` for stable `connect_sse()` API and `ServerSentEvent` field accessors.

**ADR:** [`decisions/0048-eventbus-sse-outbound-telemetry.md`](decisions/0048-eventbus-sse-outbound-telemetry.md)

---

---

## Drawflow 0.0.60 — Node-graph editor (vendored)

**Purpose in this project:** Powers the visual node-graph canvas at `/page/builder`. Users drag tools from the palette onto the canvas and draw data edges between ports. The canvas export is submitted to `builder.py` which converts it to the canonical `Graph` schema via `graph_drawflow.py`.

**Why vendored:** Single-file JS + CSS bundle. No npm, no build step — consistent with ADR 0022 (no SPA build step). Upgraded by deliberate file replacement under `web/static/`.

**Files:** `src/voice_commander/web/static/drawflow.min.js`, `src/voice_commander/web/static/drawflow.min.css`

**ADR:** [`decisions/0062-drawflow-vendored-node-graph-editor.md`](decisions/0062-drawflow-vendored-node-graph-editor.md)

---

## `winrt-runtime`, `winrt-Windows.Graphics.Imaging`, `winrt-Windows.Media.Ocr` — Windows OCR (optional)

**Purpose in this project:** Optional Windows Runtime OCR dependencies for the `ocr_region(x, y, w, h)` perception primitive in `tools/ocr.py`. When present, `ocr_region` captures a screen region via GDI BitBlt, wraps it as a `SoftwareBitmap`, feeds it to `Windows.Media.Ocr.OcrEngine`, and returns the recognised text in under 100 ms without a GPU.

**Why optional:** The winrt Python bindings are Windows-only and add ~15 MB to the environment. Tesseract subprocess is the fallback for environments where winrt cannot be installed (e.g. LTSC Windows builds without the OCR language pack). The dependency is listed in `pyproject.toml` under an optional `[ocr]` extras group.

**ADR:** [`decisions/0066-perception-primitives-layer.md`](decisions/0066-perception-primitives-layer.md)

---

## Tesseract — OCR CLI subprocess fallback

**Purpose in this project:** Fallback OCR engine for `ocr_region` when `winrt` is unavailable. `tools/ocr.py` calls `subprocess.run(["tesseract", "-", "stdout"])` with a piped PNG. Not a Python package dependency — installed separately via the UB Mannheim Windows installer (`tesseract-ocr-w64-setup-*.exe`). `tesseract` must be on `PATH` for the fallback to work; the function logs a warning and returns `""` if the subprocess call fails.

**ADR:** [`decisions/0066-perception-primitives-layer.md`](decisions/0066-perception-primitives-layer.md)

---

## Cross-reference index

| Library | ADR |
|---|---|
| `pynput` | [`0002-pynput-over-keyboard.md`](decisions/0002-pynput-over-keyboard.md) |
| `sounddevice`, `soundfile`, `numpy` | [`0003-sounddevice-over-pyaudio.md`](decisions/0003-sounddevice-over-pyaudio.md) |
| `faster-whisper` | [`0004-faster-whisper-cuda.md`](decisions/0004-faster-whisper-cuda.md) |
| `rapidfuzz` (scope: parameter resolution) | [`0041-rapidfuzz-for-parameter-resolution.md`](decisions/0041-rapidfuzz-for-parameter-resolution.md) supersedes the matching use in [`0005-rapidfuzz-matching.md`](decisions/0005-rapidfuzz-matching.md) |
| `pyautogui` | [`0002-pynput-over-keyboard.md`](decisions/0002-pynput-over-keyboard.md) |
| `windows-toasts` (removed) | [`0013-drop-winrt-toasts-audio-only-feedback.md`](decisions/0013-drop-winrt-toasts-audio-only-feedback.md) supersedes [`0007-windows-toasts-over-tkinter.md`](decisions/0007-windows-toasts-over-tkinter.md) |
| `pystray`, `Pillow` (Phase 5) | No dedicated ADR yet |
| CUDA DLL bundling (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) | [`0012-cuda-dll-bundling.md`](decisions/0012-cuda-dll-bundling.md) |
| `winsound` | [`0008-winsound-for-chimes.md`](decisions/0008-winsound-for-chimes.md) |
| `silero-vad`, `onnxruntime` | [`0016-silero-vad-over-webrtcvad.md`](decisions/0016-silero-vad-over-webrtcvad.md); session model in [`0015-vad-streaming-mode.md`](decisions/0015-vad-streaming-mode.md) |
| `soxr` | [`0017-soxr-streaming-resampler.md`](decisions/0017-soxr-streaming-resampler.md) |
| `tomli` | [`0011-uv-package-manager.md`](decisions/0011-uv-package-manager.md) |
| `uv`, `ruff`, `mypy` | [`0011-uv-package-manager.md`](decisions/0011-uv-package-manager.md) |
| `pytest`, `pytest-cov` | [`0009-phased-delivery-with-hitl-gates.md`](decisions/0009-phased-delivery-with-hitl-gates.md) |
| `@tool` decorator / registry | [`0006-tool-decorator-registry.md`](decisions/0006-tool-decorator-registry.md) |
| Threading model | [`0010-threading-model.md`](decisions/0010-threading-model.md) |
| Scroll Lock hotkey | [`0001-scroll-lock-hotkey.md`](decisions/0001-scroll-lock-hotkey.md) |
| `httpx` | No dedicated ADR yet |
| `pyglet` | [`0046-pyglet-over-tkinter-pyqt-web-overlay.md`](decisions/0046-pyglet-over-tkinter-pyqt-web-overlay.md) |
| `httpx-sse` | [`0048-eventbus-sse-outbound-telemetry.md`](decisions/0048-eventbus-sse-outbound-telemetry.md) |
