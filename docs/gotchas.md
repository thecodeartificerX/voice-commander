# Voice Commander — Gotchas

Windows-specific traps, threading pitfalls, and hardware quirks discovered during development. Each section describes the problem, why it happens, and the concrete mitigation.

---

## 1. Scroll Lock LED Toggles on Press

**Problem:** Every time the hotkey fires, the Scroll Lock LED on the keyboard flashes or changes state, which some users find distracting or confusing.

**Explanation:** `pynput` listens for key events at the OS level but does not suppress the default OS handling of the Scroll Lock key. The LED toggle is a firmware-level side-effect of the key event; pynput sees the event but cannot intercept it before the keyboard controller acts on it.

**Mitigation:** This is cosmetic and harmless — the daemon still functions correctly regardless of LED state. If it becomes annoying, the `keyboard` library (a separate package, not currently a dependency) exposes `keyboard.block_key('scroll_lock')` which can suppress the OS toggle. This is deferred to a future config option; do not add `keyboard` as a dependency until that option is explicitly requested.

---

## 2. CUDA DLL Loader Paths on Windows

**Problem:** `faster-whisper` (via CTranslate2) fails to load with a cryptic `OSError` or silent CPU fallback because Windows cannot find `cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`, or `cudnn_*.dll`.

**Explanation:** CTranslate2 dynamically loads CUDA runtime DLLs at import time. Windows DLL search order does not include NVIDIA's install directories unless they are on `PATH`. A missing cuDNN is the single most common failure — the CUDA Toolkit installer does not bundle cuDNN; it must be downloaded separately from the NVIDIA developer portal.

**Mitigation:**
1. Install **NVIDIA CUDA Toolkit 12.x** — this places `cudart64_12.dll`, `cublas64_12.dll`, and `cublasLt64_12.dll` under `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.x\bin\`.
2. Install **cuDNN 9.x** for CUDA 12 — copy the `bin\`, `include\`, and `lib\` contents into the matching CUDA Toolkit directory, or place the DLLs on `PATH` directly.
3. Verify: `python -c "from faster_whisper import WhisperModel; m = WhisperModel('small.en', device='cuda')"` should not print any warnings.
4. If GPU is unavailable at runtime, `config.toml` `device = "cpu"` is the fallback; the daemon will log a warning but continue.

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

## 4. WinRT Toast Permissions on Windows 11 (AUMID)

**Problem:** Toast notifications are silently dropped — no error, no notification — on some Windows 11 systems, even though `windows_toasts` reports success.

**Explanation:** Windows 11 notification dispatch requires the sending process to have a registered **Application User Model ID (AUMID)**. `windows_toasts` auto-registers a temporary AUMID on first use, but Focus Assist, notification grouping policies, or a missing app entry in the registry can prevent delivery without raising a Python exception.

**Mitigation:**
1. `windows_toasts` should handle registration automatically — verify it is imported and a `WindowsToaster` instance is created before the first notification attempt.
2. If toasts silently fail: open **Settings → System → Notifications & actions** and confirm that Python (or "voice-commander") is listed and **not** blocked.
3. Check that Focus Assist / Do Not Disturb is not suppressing all notifications during your test session.
4. As a fallback, the feedback subsystem uses `winsound.MessageBeep` as an audio-only path that requires no permissions, so the user always gets audio confirmation even when toasts are blocked.

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

**Explanation:** There is no OS-level exclusion preventing multiple copies of the same Python script from running simultaneously. The symptom is subtle: commands appear to execute twice, or two competing toast notifications fire.

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

Each 0.5 s the interpreter wakes, checks the event (still clear → loop again), and also services any pending SIGINT. When Ctrl+C arrives, the next 0.5 s wakeup raises `KeyboardInterrupt` in the main thread, which the surrounding `try/except KeyboardInterrupt` catches to call `shutdown()` cleanly. See `src/voice_commander/daemon.py` `Phase1Daemon.run()` for the full implementation.

---

## 10. Windows CUDA DLL Loading Requires PATH Set BEFORE Python Starts

**Problem:** `faster-whisper` (via CTranslate2) crashes at first encode with:

```
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded.
```

This can happen even when the CUDA Toolkit and cuDNN are correctly installed and present in the
machine-level PATH in the Windows registry — specifically when the launching shell session is
**stale**: it was opened before a CUDA version upgrade and still carries the old, deleted
`CUDA\v12.x\bin` path in its environment block.

**Why `os.environ['PATH']` mutation inside Python does NOT work:**
Windows' native `LoadLibraryExW` uses a snapshot of the process environment block that was
inherited when the Python interpreter process was created. Any mutations to `os.environ['PATH']`
after the interpreter is running only affect child processes spawned by Python — they have no
effect on the DLL search path of the current process. The ctranslate2 CUDA DLL load happens
during `import ctranslate2`, before any application code can mutate the environment.

**Why `os.add_dll_directory()` does NOT work:**
`os.add_dll_directory()` adds directories to the set searched when Python resolves DLLs for
its own extension module loading (the `LOAD_LIBRARY_SEARCH_USER_DIRS` flag path). CTranslate2's
internal `LoadLibraryExW` calls for `cublas64_12.dll` and the cuDNN kernels do not go through
the Python loader and therefore do not see these additions.

**Why `uv add nvidia-cublas-cu12 nvidia-cudnn-cu12` does NOT work:**
`ctranslate2` 4.x ships its own `cudnn64_9.dll` dispatcher shim bundled in its wheel. When a
pip-installed `nvidia-cudnn-cu12` package also places a `cudnn64_9.dll` in the process, two
cuDNN versions are resident simultaneously. The conflict produces garbage transcriptions —
the Whisper model emits repeated nonsense ("cataclysmic cataclysm") from known-good audio.
Pinning older pip cuDNN versions does not resolve the conflict. See ADR 0012 for full details.

**The working fix: set PATH at shell level before `uv run` via `start.ps1`'s `Add-VoiceCudaToPath`:**
`start.ps1` contains two helper functions:

- `Get-VoiceCudaPath` — scans the standard NVIDIA install locations for the highest-version
  CUDA 12.x and cuDNN 9.x directories that contain the required sentinel DLLs
  (`cublas64_12.dll` and `cudnn_graph64_9.dll`).
- `Add-VoiceCudaToPath` — prepends those directories to `$env:PATH` in the PowerShell process
  before `uv run voice-commander` is called. The child Python process inherits the corrected
  PATH from the start.

This is called in all four launch paths of `start.ps1`. Always use `start.ps1` to launch the
daemon rather than calling `uv run voice-commander` directly from a shell whose PATH you have
not manually verified.

**Diagnostic: detecting stale PATH:**

Check what the registry actually says (ground truth, requires no elevation to read):
```powershell
[System.Environment]::GetEnvironmentVariable('PATH', 'Machine')
```

Compare with the current shell's PATH:
```powershell
$env:PATH
```

If the machine PATH contains `CUDA\v12.8\bin` but `$env:PATH` still shows `CUDA\v12.6\bin`
(or a path that no longer exists on disk), the shell is stale. Close and reopen the terminal,
or use `start.ps1` which auto-corrects this at launch time.

---

## 8. `pyautogui` Failsafe Corner

**Problem:** While voice-dispatched automation is running, if the mouse cursor passes through the top-left corner of the screen (coordinate `(0, 0)`), `pyautogui` raises `FailSafeException` and the tool execution aborts mid-flight.

**Explanation:** `pyautogui` ships with a failsafe enabled by default: any mouse movement to within a few pixels of `(0, 0)` immediately raises `FailSafeException`. This is an intentional safety valve to let users regain control of a runaway automation script by flicking the mouse to the corner.

**Mitigation:** Keep `pyautogui.FAILSAFE = True` (the default). This is a feature, not a bug — it is your escape hatch if a dispatched tool misbehaves. Document to users that moving the mouse to the top-left corner during a voice-dispatched action will abort that action. If the failsafe triggers during normal use (e.g. tools that deliberately move the mouse near that corner), adjust those tools to avoid the corner region rather than disabling the failsafe globally. If a user explicitly requests `pyautogui.FAILSAFE = False`, add it as an opt-in config option with a prominent warning in `config.toml`.
