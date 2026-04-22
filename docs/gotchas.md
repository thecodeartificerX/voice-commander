# Voice Commander — Gotchas

Windows-specific traps, threading pitfalls, and hardware quirks discovered during development. Each section describes the problem, why it happens, and the concrete mitigation.

---

## 1. Scroll Lock LED Toggles on Press

**Problem:** Every time the hotkey fires, the Scroll Lock LED on the keyboard flashes or changes state, which some users find distracting or confusing.

**Explanation:** `pynput` listens for key events at the OS level but does not suppress the default OS handling of the Scroll Lock key. The LED toggle is a firmware-level side-effect of the key event; pynput sees the event but cannot intercept it before the keyboard controller acts on it.

**Mitigation:** This is cosmetic and harmless — the daemon still functions correctly regardless of LED state. If it becomes annoying, the `keyboard` library (a separate package, not currently a dependency) exposes `keyboard.block_key('scroll_lock')` which can suppress the OS toggle. This is deferred to a future config option; do not add `keyboard` as a dependency until that option is explicitly requested.

---

## 2. CUDA DLL Loading on Windows

**Problem:** `faster-whisper` (via CTranslate2) fails at first `transcribe()` with `RuntimeError: Library cublas64_12.dll is not found or cannot be loaded`, even when CUDA Toolkit is installed and on the system PATH.

**Explanation:** Three Windows-specific quirks stack on top of each other:

1. **Native DLL search uses the process env block snapshotted at process start.** `LoadLibraryExW` called from C++ in CTranslate2 ignores later `os.environ['PATH']` mutations. Terminals opened before a CUDA version upgrade can carry a stale PATH pointing at a deleted `CUDA\v12.6\bin` while the registry-backed machine PATH is fine. Child `uv run` processes inherit that stale env.
2. **`os.add_dll_directory()` only affects Python-level DLL loading.** It does not help third-party native code. PATH from the launching shell is the only thing Windows consults for native `LoadLibrary`.
3. **CTranslate2 4.x bundles its own `cudnn64_9.dll` dispatcher shim** inside the package dir. That shim delegates to full cuDNN kernel libraries (`cudnn_ops64_9.dll`, `cudnn_graph64_9.dll`, etc.) which are NOT bundled. Those kernels must come from somewhere reachable.

**Mitigation (chosen):** Pin the CUDA runtime via pip wheels **and** preload the DLLs by absolute path at import time. See ADR 0012 (amended 2026-04-21) for the full reasoning.

- `pyproject.toml` depends on `nvidia-cublas-cu12` + `nvidia-cudnn-cu12`. These install into `.venv/Lib/site-packages/nvidia/*/bin` and pin the exact ABI CTranslate2 was built against.
- `src/voice_commander/_cuda_setup.py` is imported before `faster_whisper` inside `transcriber.py`. Its `register()` function walks the nvidia packages and calls `ctypes.WinDLL(abs_path)` on every DLL. Once mapped into the process, subsequent short-name `LoadLibrary` calls from CTranslate2 resolve to the preloaded handles regardless of stale shell `PATH`.
- No-op on non-Windows; idempotent.
- **Users still need a system CUDA Toolkit 12.x + cuDNN 9.x MSI install on `PATH`.** The pip wheels + ctypes preload solve the stale-`PATH` problem when a system install exists; they do not substitute for one. See the README's *CUDA setup* section for the user-facing install steps.

**Diagnostic commands:**
```powershell
# Registry truth (machine PATH)
[System.Environment]::GetEnvironmentVariable('PATH', 'Machine') -split ';' | sls 'cuda|cudnn'

# Current shell PATH (may differ if shell was launched before a CUDA upgrade)
$env:PATH -split ';' | sls 'cuda|cudnn'
```

**Rejected alternatives:**
- Requiring users to install system CUDA Toolkit + cuDNN and manage PATH manually — brittle; any stale terminal breaks the daemon.
- Prepending PATH inside `start.ps1` before `uv run` — works but couples the launcher to an unrelated concern, and fails for any other entry point (raw `uv run voice-commander`, pytest, an IDE run config).
- Python-level `os.environ['PATH']` prepend or `os.add_dll_directory()` — does not affect CTranslate2's native-code DLL search.

**If GPU is unavailable**, `config.toml` `transcription.device = "cpu"` is the fallback; the daemon logs a warning but continues.

---

## 3. PortAudio Device Indices Drift Across Reboots

**Problem:** A hardcoded `sounddevice` device index (e.g. `device=2`) silently records from the wrong microphone — or raises `ValueError: No such device` — after a USB reconnect, driver update, or reboot.

**Explanation:** PortAudio assigns integer indices based on the order devices are enumerated at driver init time. This order is not guaranteed to be stable. Plugging or unplugging any audio device, or installing a new audio driver, can shift every subsequent index.

**Mitigation:** Never hardcode a bare integer index in `config.toml` without also recording the human-readable device name alongside it. The preferred approach is to look devices up by substring of their name at startup:

```python
import sounddevice as sd
def find_device(name_fragment: str) -> int:
    for i, dev in enumerate(sd.query_devices()):
        if name_fragment.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
            return i
    return -1  # system default
```

Use `device = -1` (system default) as the fallback when no matching device is found, rather than raising at startup.

---

## 4. WinRT Toast Permissions on Windows 11 (AUMID) — HISTORICAL

**Status:** Not a live issue — `windows_toasts` was removed in ADR 0013 (2026-04-20). The feedback subsystem is audio-chime only. Kept here for historical reference in case a future dev reintroduces visual notifications.

**Problem:** Toast notifications were silently dropped — no error, no notification — on some Windows 11 systems, even though `windows_toasts` reported success.

**Explanation:** Windows 11 notification dispatch requires the sending process to have a registered **Application User Model ID (AUMID)**. `windows_toasts` auto-registers a temporary AUMID on first use, but Focus Assist, notification grouping policies, or a missing app entry in the registry could prevent delivery without raising a Python exception.

**Mitigation (if toasts are ever reintroduced):**
1. `windows_toasts` should handle registration automatically — verify a `WindowsToaster` instance is created before the first notification attempt.
2. If toasts silently fail: open **Settings → System → Notifications & actions** and confirm the hosting process is listed and **not** blocked.
3. Check that Focus Assist / Do Not Disturb is not suppressing all notifications during your test session.
4. Always keep audio chimes as the primary feedback path so the user gets confirmation even when toasts are blocked.

---

## 5. `pynput` Callback Threading

**Problem:** Placing any blocking work — file I/O, network calls, model inference, `time.sleep` — inside a `pynput` key listener callback freezes all global keyboard dispatch until the work completes. The entire system appears locked.

**Explanation:** `pynput` runs listener callbacks on the **listener thread**, a single internal thread that processes all OS-level keyboard events. Blocking that thread prevents any subsequent key event from being processed, including the Scroll Lock release event. The hotkey appears to "stick."

**Mitigation:** The listener callback must do exactly one thing: put a message on the shared `queue.Queue` and return immediately. All transcription, matching, and tool dispatch happens on the dedicated worker thread that drains that queue. This is not optional — it is a hard architectural constraint enforced by the threading model documented in `architecture.md`.

```python
def on_press(key):
    if key == Key.scroll_lock:
        event_queue.put(HotkeyEvent(pressed=True))  # non-blocking; return immediately
```

---

## 6. Single-Instance Enforcement

**Problem:** Launching the daemon twice (e.g. from a startup script while a session is already running) results in two processes both listening for Scroll Lock. Both receive the hotkey, both start recording, both transcribe, and both attempt tool dispatch — producing duplicate or conflicting actions.

**Explanation:** There is no OS-level exclusion preventing multiple copies of the same Python script from running simultaneously. The symptom is subtle: commands appear to execute twice, or two daemons compete for the same hotkey and audio device.

**Mitigation:** Lock-file-based single-instance enforcement with OS-level locking is implemented in `src/voice_commander/single_instance.py`. On startup the daemon acquires an exclusive advisory lock on `outputs/.daemon.lock` using `msvcrt.locking` (Windows) or `fcntl.flock` (POSIX), and writes the current PID + GUID to the file. If the lock is already held by a live process the daemon logs the conflict and exits cleanly. The OS automatically releases the lock on process death, so stale locks from crashes are reclaimed without PID-alive polling. See ADR 0039.

---

## 7. Whisper Hallucinates on Silence

**Problem:** `faster-whisper small.en` produces text output — commonly `"Thank you."`, `"Thanks for watching."`, or `"."` — when fed audio that is pure silence or very low-level background noise. The matcher then attempts to run a tool that was never spoken.

**Explanation:** Whisper was trained on real-world audio with speech; it has learned to predict plausible utterance completions. When given near-silence, the decoder can still produce high-probability tokens from its language model priors. This is a known upstream limitation of all Whisper variants.

**Mitigation:**
1. Pass `vad_filter=True` to `faster-whisper`'s `transcribe()` call. This runs a lightweight Voice Activity Detector over the audio before transcription and skips segments with no detected speech.
2. Treat any transcript with a mean log-probability below a configurable threshold (e.g. `no_speech_prob > 0.6`) as a miss, log it at DEBUG level, and play the "no match" chime instead of attempting dispatch.
3. Ensure the recorder only captures audio between Scroll Lock press and release — do not pad silence at the recording boundaries beyond the minimum required by the VAD.

---

## 9. Windows: Ctrl+C and `threading.Event.wait()`

**Problem:** Pressing Ctrl+C while the daemon is running in a console has no effect — the process only dies when the terminal is force-killed.

**Explanation:** On Windows, `threading.Event.wait()` with no timeout (or a very large timeout) blocks the calling thread inside a kernel `WaitForSingleObject` call. The Python interpreter services SIGINT handlers only between bytecodes on the main thread, but that thread is permanently parked inside the kernel wait and never returns to the interpreter loop. The registered `signal.signal(SIGINT, ...)` handler therefore never fires.

**Mitigation:** Replace the bare `self._shutdown.wait()` with a polled loop:

```python
while not self._shutdown.wait(0.5):
    pass
```

Each 0.5 s the interpreter wakes, checks the event (still clear → loop again), and also services any pending SIGINT. When Ctrl+C arrives, the next 0.5 s wakeup raises `KeyboardInterrupt` in the main thread, which the surrounding `try/except KeyboardInterrupt` catches to call `shutdown()` cleanly. See `src/voice_commander/daemon.py` `StreamingDaemon.run()` for the full implementation.

---

## 8. `pyautogui` Failsafe Corner

**Problem:** While voice-dispatched automation is running, if the mouse cursor passes through the top-left corner of the screen (coordinate `(0, 0)`), `pyautogui` raises `FailSafeException` and the tool execution aborts mid-flight.

**Explanation:** `pyautogui` ships with a failsafe enabled by default: any mouse movement to within a few pixels of `(0, 0)` immediately raises `FailSafeException`. This is an intentional safety valve to let users regain control of a runaway automation script by flicking the mouse to the corner.

**Mitigation:** Keep `pyautogui.FAILSAFE = True` (the default). This is a feature, not a bug — it is your escape hatch if a dispatched tool misbehaves. Document to users that moving the mouse to the top-left corner during a voice-dispatched action will abort that action. If the failsafe triggers during normal use (e.g. tools that deliberately move the mouse near that corner), adjust those tools to avoid the corner region rather than disabling the failsafe globally. If a user explicitly requests `pyautogui.FAILSAFE = False`, add it as an opt-in config option with a prominent warning in `config.toml`.

---

## 10. `windows_toasts` Eager Import Corrupts CUDA Initialization — HISTORICAL

**Status:** Not a live issue — `windows_toasts` was removed as a dependency in ADR 0013 (2026-04-20). Kept here as a cautionary case study for any future dev tempted to add a package that initializes WinRT / COM at import time.

**Problem:** `uv run voice-commander` crashes with native access violation (`exit code -1073741819` / `STATUS_ACCESS_VIOLATION / 0xC0000005`) inside `faster_whisper/transcribe.py:689` during `WhisperModel.__init__()` on CUDA. No Python exception; Python dies silently unless `faulthandler.enable()` is on. The same model construction succeeds in the pytest suite.

**Explanation:** Importing `windows_toasts` at module scope pulls the WinRT runtime (`winsdk` / `Windows.Foundation` bindings) into the process. WinRT initialization permanently modifies some process-wide state — likely the DLL search path, COM apartment, or module-resolution cache — that CTranslate2's native code relies on when it lazy-loads `cudnn_graph64_9.dll`, `cudnn_ops64_9.dll`, and the rest of the cuDNN dispatcher chain. The resulting DLL resolution is broken in a way that does not surface as a load error; it surfaces later as a null pointer dereference during the first CUDA context setup.

The failure is order-dependent:
- Test path: `pytest tests/unit/test_transcriber.py -m hardware` — imports only `transcriber.py` → `_cuda_setup.register()` → `faster_whisper` → succeeds.
- Daemon path: `uv run voice-commander` — imports `__main__` → `daemon` → `feedback` (which eagerly imports `windows_toasts`) → `transcriber` → `_cuda_setup.register()` → `faster_whisper` → crashes at `WhisperModel.__init__()`.

**Original mitigation (commit `e3a8fe7`, later superseded):** `windows_toasts` was lazy-imported inside `WindowsFeedbackSink._toast()` so the first toast dispatch (after the user's first match/miss, long after model load) triggered the import. That kept the package out of the process during CUDA init. Rejected as the durable fix — one careless future refactor that promotes the import back to module scope silently reintroduces the crash.

**Final resolution (ADR 0013, commit removing the toast system):** `windows_toasts` dropped as a dependency entirely. Feedback is audio-chimes-only. No WinRT in the process, no way to regress.

**Diagnostic value retained:** This failure mode is the reason `__main__.py` calls `faulthandler.enable(file=<crash.log>, all_threads=True)` before importing anything heavy, and why `_cuda_setup.register()` logs DLL preload counts at INFO. Without the fault handler, the daemon would have appeared to simply exit with an opaque numeric code and no traceback. That infrastructure is kept — it's general-purpose observability, not toast-specific.

**Related pitfalls to watch for:** Any package that loads COM/WinRT at import time (`winsdk`, `winrt-*`, `pywin32` with early `pythoncom.CoInitialize`, `pythonnet`) may reproduce this class of bug. Keep CUDA-adjacent imports first; lazy-load Windows-specific UI/COM helpers, or — if the feature turns out to be optional — skip the integration entirely. Audio chimes do a lot of the same job with none of the process-level side effects.

---

## 11. VAD RT-Callback Constraint

**Problem:** Performing any blocking work, memory allocation, or Python GIL-contended operation inside the PortAudio stream callback causes audio glitches, dropped frames, or hard RT-deadline violations.

**Explanation:** The PortAudio callback runs on a real-time OS audio thread. Its deadline is the audio buffer period (typically 10–20 ms). Any work that takes longer than that deadline — or that blocks waiting for Python's GIL — causes the callback to overrun, which produces audible artifacts and can destabilize the audio stack.

**Mitigation:** The callback does exactly two things: `indata.copy()` (a single numpy allocation, fast) and `raw_q.put_nowait()` (a non-blocking enqueue). All VAD processing, resampling, and inference happen on the VAD worker thread that drains `raw_q`. This division is a hard architectural constraint — never add logic to the callback.

---

## 12. onnxruntime Thread-Safety

**Problem:** Sharing a silero-vad `VADIterator` (or its underlying ONNX session) across multiple threads causes race conditions, corrupted internal state, and unpredictable detection results.

**Explanation:** silero-vad's ONNX model maintains mutable internal state (hidden states for the RNN layers). `onnxruntime` `InferenceSession` objects are not thread-safe for concurrent inference calls — the library documents this explicitly. Even if calls appear to work initially, the internal hidden-state tensor is corrupted by concurrent writes, producing garbage probability outputs.

**Mitigation:** The `VADGate` and its underlying ONNX session are isolated to a single VAD worker thread. One `VADGate` is created per audio stream and never shared. Do not pass a `VADIterator` instance across thread boundaries.

---

## 13. Resampler State Lifetime

**Problem:** Reusing a `soxr.ResampleStream` instance across recording sessions causes audio from one session to bleed into the next — the filter's internal delay line carries samples from the previous session into the start of the new one.

**Explanation:** `soxr.ResampleStream` maintains internal filter state (a polyphase FIR delay line) across `resample_chunk()` calls. This is the correct behaviour within a session, as it avoids discontinuities at chunk boundaries. Across sessions, however, the tail of the previous session's audio remains in the filter's internal buffer and is output at the start of the next session, contaminating the VAD's first few frames.

**Mitigation:** `StreamingRecorder` creates a fresh `Resampler` instance at the start of every session (every Scroll Lock open). A `reset()` call would theoretically suffice, but creating a new instance is simpler and eliminates any risk of residual state. Do not reuse a `ResampleStream` across session boundaries.

---

## 14. Uvicorn on a daemon thread

Running `uvicorn.Server.run()` on a `threading.Thread(daemon=True)` works but has a subtle requirement: the thread gets its own asyncio event loop created by uvicorn internally. Do NOT share the uvicorn event loop with other code. Access the FastAPI app synchronously through the registry/store — all shared state is guarded by `threading.Lock`, not asyncio primitives.

Graceful shutdown: set `server.should_exit = True` and join the thread. Uvicorn polls this flag in its main loop.

---

## 15. Portalocker on Windows

`portalocker` uses `msvcrt.locking()` on Windows, which requires the file to be opened in a compatible mode. Always use `mode="a"` (append) for lock files — this creates the file if missing and doesn't truncate existing content. Lock files live in `tools/.locks/` and should be gitignored.

---

## 16. TOML atomic write on Windows

`os.replace()` is atomic on POSIX but NOT guaranteed atomic on Windows (NTFS is close but not specified). The sequence `write .tmp` → `os.replace .tmp → .toml` is the best available. The per-tool file lock (portalocker) is the actual concurrency guard; atomic replace is defense-in-depth.

---

## 17. Tailwind CSS vendoring

The dashboard uses Tailwind CSS Play CDN (`<script src="https://cdn.tailwindcss.com">`) for development. For fully offline use, vendor the CDN script to `web/static/tailwind.min.js`. The Play CDN generates CSS client-side from class names — it's ~300KB but zero-config.

---

## 18. Windows 11 Foreground Lockout and `SetForegroundWindow` Silent Failure

**Problem:** `SetForegroundWindow(hwnd)` appears to succeed (no Python exception) but the target window does not come to the foreground. Subsequent keystrokes land in the wrong window. The daemon log may show:

```
pywintypes.error: (0, 'SetForegroundWindow', 'No error message is available')
```

or the call may return `False` with no exception at all.

**Explanation:** Windows 11 has tighter foreground-lockout rules than Windows 10. A background process (such as the Voice Commander daemon) is denied the ability to call `SetForegroundWindow` unless it holds the foreground permission token — which it does not, because it received no recent input event. The call fails silently: the Win32 return value is `False` and the error code is 0, neither of which raises a Python exception by default. If the function returns without raising, the caller assumes focus succeeded and continues — leading to keystrokes being sent to the wrong window.

**Mitigation (ADR 0037):** Use the `AttachThreadInput` + `AllowSetForegroundWindow(ASFW_ANY)` + post-call `GetForegroundWindow` verification pattern:

1. Call `AllowSetForegroundWindow(ASFW_ANY)` to acquire the foreground permission token.
2. Retrieve the current foreground thread ID and the target window's thread ID.
3. Call `AttachThreadInput(foreground_tid, target_tid, True)` to temporarily link the input queues.
4. Call `SetForegroundWindow(hwnd)`.
5. Unconditionally call `AttachThreadInput(foreground_tid, target_tid, False)` in a `finally` block.
6. Call `GetForegroundWindow()` to verify focus actually changed. If `GetForegroundWindow() != hwnd`, raise `FocusWindowError`.

Grep anchor for future agents: `pywintypes.error: (0, 'SetForegroundWindow', 'No error message is available')` — if you see this string in a log, the focus call failed and the daemon silently continued. Apply the pattern above.

**Elevated windows:** a non-elevated daemon cannot focus an admin-elevated window regardless of `AttachThreadInput`. This failure is expected and `FocusWindowError` is raised.

See ADR 0037 for the full rationale and alternatives considered.

---

## 19. LM Studio Prefix KV Cache: `GET /v1/models` Warmup Is Useless

**Problem:** The daemon's startup warmup logs `warmup complete` after a successful `GET /v1/models` response, but the first real voice command still times out (observed: ~21 s gap between warmup success and first-call timeout in production log — see ADR 0038 for the exact excerpt).

**Explanation:** `GET /v1/models` is a pure metadata query. It does not touch the LM Studio inference engine, does not allocate KV cache buffers, and does not execute any model computation. The model is loaded in VRAM, but LM Studio's prefix KV cache (which stores the prefilled key-value activations for the stable system prompt + tools array prefix) is empty. The first `/v1/chat/completions` call pays the full cold-prefill cost: 4–8 s on typical hardware for Gemma 4 E4B with the full tools array. The per-call `timeout_ms = 600` budget is calibrated for warm calls and is correctly too tight for this cold first call.

R1 §5 documents this: prefix KV cache reuse applies only to the unchanging prefix that has already been processed by a real inference call. A GET request contributes nothing.

**Mitigation (ADR 0038):** Replace the `GET /v1/models` warmup with a `POST /v1/chat/completions` call that matches the exact runtime request shape:

- Use the real system prompt and real tools array (same as every routing call).
- Set `tool_choice = "none"` (or `"auto"` as fallback) and `max_tokens = 1`.
- Use a dedicated `warmup_timeout_ms = 5000` config field (separate from per-call `timeout_ms = 600`).

This forces LM Studio to execute a full prompt prefill, populate the KV cache, and compile any CUDA kernels — so the first real call hits a warm cache and completes within the 600 ms budget.

**Rule for future agents:** a warmup that does not POST the same request shape as the real calls will not seed the prefix KV cache. `GET /v1/models` only confirms the HTTP server is alive; it does not confirm inference readiness. Always POST a minimal chat-completion request to genuinely warm the cache.

See ADR 0038 for the full rationale and alternatives considered.

---

## 20. ctypes HANDLE Truncation on 64-bit Windows

**Problem:** `_pid_alive()` in `single_instance.py` always returned `True` for dead PIDs on 64-bit Windows, preventing stale-lock reclamation after a daemon crash.

**Explanation:** `ctypes.windll.kernel32.OpenProcess` has a default `restype` of `c_int` (32-bit signed integer). On 64-bit Windows, `HANDLE` is a pointer-sized value (64-bit). When `OpenProcess` returns a valid handle, the 64-bit value is truncated to 32 bits. Even for dead PIDs where `OpenProcess` should return `NULL` (0), the truncated value could be non-zero — producing a bogus truthy result. The subsequent `if handle:` check then concludes the process is alive when it is not.

This is a general ctypes pitfall: any Windows API function that returns `HANDLE`, `HMODULE`, `HWND`, or any pointer-sized type **must** have its `restype` explicitly set to `ctypes.c_void_p`. The default `c_int` is only safe for functions returning 32-bit integers (e.g. `BOOL`, `DWORD`).

**Mitigation:** All ctypes bindings in `single_instance.py` now declare explicit `restype` and `argtypes`:

```python
OpenProcess = ctypes.windll.kernel32.OpenProcess
OpenProcess.restype = ctypes.c_void_p        # HANDLE is pointer-sized
OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
```

Additionally, `GetExitCodeProcess` is called after a successful `OpenProcess` — a handle to a zombie process is truthy but its exit code ≠ `STILL_ACTIVE` (259). See ADR 0039.

---

## 21. LM Studio Must Be Running for Any Routing to Work

**Problem:** Every voice utterance that passes the confidence/word-count gates is now routed through `LLMRouter.route()` unconditionally (ADR 0040). If LM Studio is not running, every utterance produces a miss chime — the daemon appears to be broken even though ASR is working correctly.

**Explanation:** There is no rapidfuzz hot path or offline fallback. `LLMRouter.route()` makes an HTTP call to `http://localhost:1234/v1/chat/completions`. A connection refused, timeout, or HTTP error all return `None`, which `Resolver` converts to a miss. This is by design — `None` is the safe, observable failure mode — but it means the daemon is effectively non-functional without LM Studio.

**Mitigation:**

1. Start LM Studio before starting the Voice Commander daemon.
2. Load the configured model in LM Studio (`[llm].model_id` in `config.toml`; default is `google/gemma-4-e4b`).
3. Verify with the daemon's startup log: it calls `LLMRouter.warmup()` at startup and logs either `LLM router warm` or `LLM router warmup failed` with the error. If you see the failure message, check LM Studio is running and the model is loaded.
4. `LLMRouter.warmup()` sends a real chat-completion POST (not just a `GET /v1/models` ping — see gotcha §19). A successful warmup log means the first real utterance will hit a warm KV cache.

**If LM Studio is not available by design** (e.g. running on a machine without an LLM), the daemon will still capture, transcribe, and gate audio correctly — every routable utterance will miss-chime. There is no silent failure; the miss chime is the observable signal.

**Diagnostic commands:**

```powershell
# Check whether LM Studio server is listening
Test-NetConnection -ComputerName localhost -Port 1234

# Check daemon startup log for warmup result
Select-String -Path outputs\voice_commander_*.log -Pattern "LLM router"
```

See ADR 0040 for the full rationale for removing the rapidfuzz fallback path.

---

## 22. `OpenProcess` Permission Denial When Enumerating Windows

**Problem:** `resolver.resolve_window(target)` scores every visible window by `max(WRatio(target, proc_name), WRatio(target, title))`. Reading the process name requires `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetModuleBaseName`. On Windows 11, that call raises `pywintypes.error: (5, 'OpenProcess', 'Access is denied')` for processes owned by a different session or running at higher integrity (e.g. Task Manager when the daemon runs unelevated, or most shell surfaces owned by `dwm.exe` / `ShellExperienceHost.exe`).

**Explanation:** Per-process access control is enforced by the kernel. `PROCESS_QUERY_LIMITED_INFORMATION` is the weakest handle that still allows `GetModuleBaseName`; anything lower cannot read the image name. Processes running as SYSTEM or in a different session deny even this level to a normal-integrity caller.

**Mitigation:** `_get_process_name()` in `resolver.py` catches every exception from `OpenProcess` and returns `""`. The caller then computes the candidate's score purely from `WRatio(target, title)` — i.e. the process-name axis is zero, and the title axis carries the decision. This is the correct behaviour:

- The user sees window titles, not process names. Scoring by title alone is usually fine.
- A window title almost always contains enough signal ("Chrome" in "Inbox — Gmail — Google Chrome", "Spotify Premium" in the Spotify window) to clear the 70 threshold.
- If the title axis also fails, the top-3 diagnostic in `FocusWindowError` includes `proc=''` so the developer can see the permission denial and decide whether to elevate the daemon.

**Do not:**

- Elevate the daemon to get process-name read access. That expands the attack surface for every other pywin32 call and is not justified by a marginal accuracy gain on fuzzy scoring.
- Ignore the exception and crash. The resolver must be robust — a single unreadable `dwm.exe` handle cannot break `focus("chrome")`.

**Diagnostic:** Turn on `DEBUG` logging for `voice_commander.resolver` and you will see per-call `resolve_window target=… picked=… top3=…` lines. Entries with `proc=''` are the permission-denied cases.

---

## 23. Pyglet Transparent Overlay on Windows — Black Background

**Problem:** Sprite companion window showed an opaque black square around the cat on Windows 11 despite using `WINDOW_STYLE_OVERLAY`, `alpha_size=8`, `glClearColor(0,0,0,0)`, and `GL_BLEND`. The cat drew correctly but the rest of the window never composited against the desktop.

**Explanation:** Several independent Windows/pyglet traps stack. Any one of them produces the exact same symptom (opaque black where alpha should be zero), so they have to be audited together:

1. **`glClearColor` reset between frames.** `window.clear()` clears to the *current* clear color, and pyglet's internal framebuffer bookkeeping can reset it between draws. Setting `glClearColor(0,0,0,0)` once in `__init__` is insufficient — the second frame renders against whatever default pyglet reinstalled. Must be called **inside `on_draw` before `window.clear()`**, every frame. (Pyglet issue [#1271](https://github.com/pyglet/pyglet/issues/1271).)
2. **Driver silently drops alpha under MSAA.** `pyglet.gl.Config(alpha_size=8)` does not promise an alpha channel — some Windows drivers auto-pick a multisampled config when `sample_buffers` is unspecified, and the multisampled framebuffer has no alpha. Always set `sample_buffers=0, samples=0` explicitly, and log `window.context.config.alpha_size` at startup to verify the driver granted 8 bits.
3. **`glBlendFunc` collapses framebuffer alpha.** `glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)` uses the same factors for the alpha channel, so output alpha = `src_α*src_α + dst_α*(1-src_α)`. With `dst_α=0` after clear, opaque sprite pixels get `1*1+0=1` (fine), but semi-transparent edges get squared-down values and the DWM composite looks black-fringed. Use `glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA)` so output alpha = `src_α + dst_α*(1-src_α)` accumulates correctly.
4. **`pyglet.sprite.Sprite` ignores the window-level blend state.** In pyglet 2.x every sprite has its own `blend_src` / `blend_dest` attributes used by its shader, and they default to values that don't assume a transparent framebuffer. Set them explicitly per sprite (`sprite.blend_src = GL_SRC_ALPHA; sprite.blend_dest = GL_ONE_MINUS_SRC_ALPHA`) so the texture composites correctly against our zero-alpha clear.
5. **`SetLayeredWindowAttributes` switches the window to constant-alpha mode.** This call **disables** per-pixel alpha. Pyglet's `WINDOW_STYLE_OVERLAY` already calls it internally with alpha=255 (a deliberate DWM pass-through dance alongside `DwmEnableBlurBehindWindow`), which works. Do **not** call `SetLayeredWindowAttributes` a second time from user code — the redundant call re-arms constant-alpha mode and the framebuffer alpha stops reaching DWM.
6. **`DwmExtendFrameIntoClientArea` is useless for `WS_POPUP` overlays.** It requires a window with non-client area (title bar, borders) to extend. Our overlay has none, so the call returns `E_INVALIDARG`. Skip it entirely; `DwmEnableBlurBehindWindow` (already called by pyglet) is the relevant DWM hint for borderless overlays.

**Mitigation:** All six are fixed in `src/voice_sprite/window.py` and `src/voice_sprite/win32_flags.py`:

```python
# window.py
gl_config = pyglet.gl.Config(
    alpha_size=8,
    double_buffer=True,
    sample_buffers=0,   # (2) no MSAA auto-pick
    samples=0,
)
super().__init__(..., style=pyglet.window.Window.WINDOW_STYLE_OVERLAY, config=gl_config)

gl.glEnable(gl.GL_BLEND)
gl.glBlendFuncSeparate(                             # (3) separate alpha channel
    gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA,
    gl.GL_ONE,       gl.GL_ONE_MINUS_SRC_ALPHA,
)

granted = self.context.config.alpha_size            # (2) verify
logger.info("GL config granted alpha_size=%s", granted)

def on_draw(self):
    pyglet.gl.glClearColor(0, 0, 0, 0)              # (1) every frame
    self.clear()
    ...
    if self._sprite is None:
        self._sprite = pyglet.sprite.Sprite(region, ...)
        self._sprite.blend_src  = gl.GL_SRC_ALPHA   # (4) per-sprite
        self._sprite.blend_dest = gl.GL_ONE_MINUS_SRC_ALPHA
```

```python
# win32_flags.py — add ONLY toolwindow + noactivate + topmost.
# Do NOT re-call SetLayeredWindowAttributes (pyglet already did it).
# Do NOT call DwmExtendFrameIntoClientArea (WS_POPUP has no frame to extend).
style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
```

**Diagnostic (paste the line into the log you want to see at startup):**

```
GL config granted alpha_size=8 (need 8 for transparency)
```

If that logs `0`, your driver (or RDP session, or iGPU) silently refused alpha and no Python-side fix will help — swap GPU or fall back to `UpdateLayeredWindow` with a CPU blit. If it logs `8`, the bug is in the six-item list above.

See ADR 0050.

---

## 24. Charsheet Cell Size Cannot Be Assumed From Pack Documentation

**Problem:** `CatPackPaid/Sprites/Halloween/witch.png` is advertised as a "32×32" pack, but slicing at 32×32 produces headless cats — the witch-hat adds vertical content that pushes the real pose-to-pose pitch to 32×48 for most rows. Other rows (sleeping, lying-down poses with long tail) use 32×48-wide cells. The pack's documented cell size is only the *body* — not the pitch.

**Explanation:** Free / paid sprite packs authored by hand commonly mix cell sizes per row, with padding added around tall or wide poses to avoid overlap. A uniform grid is the exception, not the rule. Assuming the docs' cell size is the right pitch produces the classic "every other frame is chopped" artefact visible when the pitch you're slicing at is a factor-of-2 off from the real pitch.

**Mitigation:** Never trust the pack's documented cell size for a new charsheet. Detect the pitch mathematically:

1. Load the PNG alpha channel into numpy.
2. For each candidate `(cell_w, cell_h)` that evenly divides the sheet, compute the mean alpha of pixels landing exactly on the grid lines. The correct pitch has the lowest grid-line alpha because gaps between cells should be (mostly) transparent.
3. Overlay the winning grid on the sheet and eyeball — red lines should land in the alpha gaps, not through hats/tails.
4. If per-row horizontal pitch varies (e.g. row 0 has 10 sitting cats at 32-wide, row 6 has 10 lying cats at 48-wide), support per-state overrides in the charsheet TOML:

```toml
frame_width = 32      # global default
frame_height = 48

[states.crashed]      # row 6 uses wider pose pitch
row = 6
frames = 10
frame_width = 48
```

To verify a chosen mapping, run `uv run python scripts/dump_sprite_slices.py` — it
slices every declared state out of the sheet and writes per-state strips to
`outputs/sprite_debug/` so misalignment is obvious. `scripts/build_state_reference.py`
produces a single labeled reference PNG of every state for human review.

See ADR 0047 (updated) for the per-state pitch override.
