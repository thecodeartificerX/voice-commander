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

**Purpose in this project:** `sounddevice` drives the `Recorder` subsystem. It opens a PortAudio input stream, collects PCM frames into an in-memory buffer while the hotkey is held, and flushes the buffer to a WAV file when the user releases the key. The captured audio is 16 kHz mono 16-bit PCM — exactly what Whisper expects, so no resampling step is needed.

**Alternatives considered:** `pyaudio` is the historical standard for PortAudio bindings in Python.

**Why `sounddevice` won:** `pyaudio` requires a compiled native extension that historically has been painful to install on Windows (missing `portaudio.dll`, mismatched architectures). `sounddevice` ships wheels with PortAudio bundled and installs cleanly with `uv add`. Its callback-based streaming API is also cleaner and more Pythonic than `pyaudio`'s blocking read loop. Both libraries ultimately wrap the same PortAudio C library, so there is no capability difference for our use case.

**Pin reason:** `>=0.4.7` is the version that introduced `dtype` support for 16-bit integer arrays. Required for the WAV pipeline.

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

## `rapidfuzz` — Fuzzy phrase matching

**Purpose in this project:** `rapidfuzz` powers the `Matcher` subsystem. Given the ASR transcript, the matcher calls `rapidfuzz.process.extract(scorer=fuzz.WRatio)` against the full flat list of registered tool phrases to find the top-5 candidates and their scores. If the best score exceeds the configured threshold (default 85.0), the corresponding tool is dispatched.

**Alternatives considered:**
- `thefuzz` (formerly `fuzzywuzzy`) — the classic Python fuzzy matching library. Pure Python; slow on large phrase lists.
- `difflib` (stdlib) — `SequenceMatcher` is available with no extra install, but it is significantly slower and less accurate for short spoken phrases.

**Why `rapidfuzz` won:** `rapidfuzz` is implemented in C++ and is 10–100× faster than `thefuzz` on equivalent inputs. It is API-compatible with `thefuzz`'s `fuzz` module, so the drop-in migration path is trivial. At our scale (dozens of phrases), the speed difference is irrelevant, but C++ correctness and the well-tested scorer implementations make it the right default.

**Pin reason:** `>=3.9.0` for the stable `process.extract()` return-type signature and the `WRatio` scorer improvements in 3.x.

**ADR:** [`decisions/0005-rapidfuzz-matching.md`](decisions/0005-rapidfuzz-matching.md)

---

## `pyautogui` — Key and mouse automation

**Purpose in this project:** `pyautogui` is used inside individual tool functions (in `src/voice_commander/tools/`) to send keystrokes and hotkeys to the active window. For example, the `copy` tool calls `pyautogui.hotkey('ctrl', 'c')`, and `paste` calls `pyautogui.hotkey('ctrl', 'v')`.

**Alternatives considered:** `pynput.keyboard.Controller` — already a dependency for the hotkey listener, so using it for key-sending would avoid an extra package. `win32api`/`SendInput` via `pywin32` — the most accurate Windows simulation, but requires `pywin32` and more boilerplate.

**Why `pyautogui` won:** `pyautogui.hotkey()` sends multi-key combinations with correct inter-key timing in a single call, which is simpler than the `pynput.keyboard.Controller.press()/release()` dance. For the MVP toolset (clipboard, window management, browser navigation), `pyautogui` is expressive enough and well-documented. `pynput.keyboard.Controller` remains usable if a specific tool needs lower-level control.

**Pin reason:** `>=0.9.54` for Python 3.11 compatibility. The `0.9.x` series is the current stable branch.

**ADR:** [`decisions/0002-pynput-over-keyboard.md`](decisions/0002-pynput-over-keyboard.md) (covers keyboard automation broadly)

---

## `windows-toasts` — Native Windows toast notifications

**Purpose in this project:** `windows-toasts` drives the notification half of `WindowsFeedbackSink`. After each recognition cycle, a WinRT toast is fired: `✓ {tool} · "{transcript}" ({score:.0f})` on a match, or `✗ no match · "{transcript}" · top: {candidate} ({score:.0f})` on a miss. Toasts are fire-and-forget — no waiting for dismissal.

**Alternatives considered:**
- `win11toast` — a thin wrapper around WinRT toasts. Simple, but less actively maintained and the API changed between versions without deprecation warnings.
- `plyer` — cross-platform notification library. Uses `win10toast` on Windows, which renders legacy balloon tooltips rather than modern WinRT toasts and cannot be styled.
- Tkinter popup — the classic `tkinter.Tk()` overlay approach. A notorious footgun: Tkinter windows must be driven from the main thread, creating threading complexity and visual lag.

**Why `windows-toasts` won:** WinRT-backed, renders as proper Windows 11 Action Center notifications, supports custom XML templates for styling, and is fire-and-forget from any thread. It solves the threading problem that kills Tkinter for daemon use. Active maintenance and a clean API make it the clear choice.

**Pin reason:** `>=1.1.0` for the `InteractableWindowsToaster` API and async-safe fire-and-forget dispatch.

**ADR:** [`decisions/0007-windows-toasts-over-tkinter.md`](decisions/0007-windows-toasts-over-tkinter.md)

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

**ADR:** [`decisions/0007-windows-toasts-over-tkinter.md`](decisions/0007-windows-toasts-over-tkinter.md)

---

## `Pillow` — Image handling for tray icon (optional, Phase 5)

**Purpose in this project:** `Pillow` (also in the `[tray]` optional group) is a `pystray` dependency: it is used to load and render the PNG icon image into the tray. `pystray` accepts `PIL.Image` objects as icons on all platforms.

**Alternatives considered:** Not meaningfully substitutable for `pystray` icon loading.

**Why `Pillow` won:** It is the required image backend for `pystray` on Windows. No standalone rationale needed.

**Pin reason:** `>=10.2.0` for Python 3.11 compatibility and CVE-clean status. Pillow has had security fixes in the 10.x series that make a lower bound important.

**ADR:** [`decisions/0007-windows-toasts-over-tkinter.md`](decisions/0007-windows-toasts-over-tkinter.md)

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

## Cross-reference index

| Library | ADR |
|---|---|
| `pynput` | [`0002-pynput-over-keyboard.md`](decisions/0002-pynput-over-keyboard.md) |
| `sounddevice`, `soundfile`, `numpy` | [`0003-sounddevice-over-pyaudio.md`](decisions/0003-sounddevice-over-pyaudio.md) |
| `faster-whisper` | [`0004-faster-whisper-cuda.md`](decisions/0004-faster-whisper-cuda.md) |
| `rapidfuzz` | [`0005-rapidfuzz-matching.md`](decisions/0005-rapidfuzz-matching.md) |
| `pyautogui` | [`0002-pynput-over-keyboard.md`](decisions/0002-pynput-over-keyboard.md) |
| `windows-toasts`, `pystray`, `Pillow` | [`0007-windows-toasts-over-tkinter.md`](decisions/0007-windows-toasts-over-tkinter.md) |
| `winsound` | [`0008-winsound-for-chimes.md`](decisions/0008-winsound-for-chimes.md) |
| `tomli` | [`0011-uv-package-manager.md`](decisions/0011-uv-package-manager.md) |
| `uv`, `ruff`, `mypy` | [`0011-uv-package-manager.md`](decisions/0011-uv-package-manager.md) |
| `pytest`, `pytest-cov` | [`0009-phased-delivery-with-hitl-gates.md`](decisions/0009-phased-delivery-with-hitl-gates.md) |
| `@tool` decorator / registry | [`0006-tool-decorator-registry.md`](decisions/0006-tool-decorator-registry.md) |
| Threading model | [`0010-threading-model.md`](decisions/0010-threading-model.md) |
| Scroll Lock hotkey | [`0001-scroll-lock-hotkey.md`](decisions/0001-scroll-lock-hotkey.md) |
