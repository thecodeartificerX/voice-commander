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

**Mitigation (chosen):** Bundle the CUDA runtime directly in the venv via pip packages, then preload the DLLs by absolute path at import time. See ADR 0012 for the full reasoning.

- `pyproject.toml` depends on `nvidia-cublas-cu12` + `nvidia-cudnn-cu12`. These install into `.venv/Lib/site-packages/nvidia/*/bin` and travel with the venv — no system CUDA install required.
- `src/voice_commander/_cuda_setup.py` is imported before `faster_whisper` inside `transcriber.py`. Its `register()` function walks the nvidia packages and calls `ctypes.WinDLL(abs_path)` on every DLL. Once mapped into the process, subsequent short-name `LoadLibrary` calls from CTranslate2 resolve to the preloaded handles.
- No-op on non-Windows; idempotent.

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

**Mitigation (Phase 5):** Implement a named OS mutex at startup using `win32event.CreateMutex(None, True, "VoiceCommanderDaemon")`. If `GetLastError()` returns `ERROR_ALREADY_EXISTS`, log the conflict and exit cleanly. As a simpler fallback, write a lock file to `outputs/.daemon.lock` containing the current PID, and check for its existence at launch (with stale-PID detection). This is explicitly deferred to Phase 5 alongside the system-tray icon.

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
