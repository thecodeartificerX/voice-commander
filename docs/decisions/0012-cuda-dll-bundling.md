# ADR 0012: Bundle CUDA runtime via pip packages, preload DLLs from Python

**Status:** Amended 2026-04-21 — the "self-contained venv" claim was wrong. See *Amendment* at the bottom.
**Date:** 2026-04-20

## Context

During Phase 2 validation, launching the daemon from certain Windows terminal sessions crashed at first `WhisperModel.transcribe()` with `RuntimeError: Library cublas64_12.dll is not found or cannot be loaded` — even though CUDA Toolkit 12.8 and cuDNN 9.11 were correctly installed system-wide, present on the registry-backed machine PATH, and visible to `nvidia-smi`.

Investigation surfaced three overlapping Windows quirks:

1. **Native DLL search uses the process environment block snapshotted at process start.** `LoadLibraryExW` called from C++ in CTranslate2 does not observe `os.environ['PATH']` mutations made after the Python interpreter is running. A terminal session carrying a stale PATH entry (e.g. a deleted `CUDA\v12.6\bin` from before an in-place upgrade to v12.8) produces a misleading "DLL not found" error even though the correct DLL exists on disk and is listed in the machine PATH.
2. **`os.add_dll_directory()` is Python-only.** It affects DLLs loaded by the Python interpreter itself and extension modules linked via Python's DLL-search hooks — but not third-party native code using the Windows default DLL search.
3. **CTranslate2 4.x ships a `cudnn64_9.dll` dispatcher shim** inside its own package dir. The shim delegates cuDNN calls to full kernel libraries (`cudnn_ops64_9.dll`, `cudnn_graph64_9.dll`, `cudnn_cnn64_9.dll`, etc.) that are NOT bundled. Those kernels must be loadable from somewhere at runtime or the model crashes at encode time.

We need a solution that works regardless of shell state, without requiring users to manage system CUDA installs or PATH by hand.

## Decision

1. Add `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` as project dependencies in `pyproject.toml`. These pip packages install the CUDA 12.x cuBLAS runtime and cuDNN 9.x kernel libraries into `.venv/Lib/site-packages/nvidia/cublas/bin` and `.venv/Lib/site-packages/nvidia/cudnn/bin`. The venv becomes self-contained; users do not need a system CUDA install.
2. Add `src/voice_commander/_cuda_setup.py`. Its `register()` function enumerates `nvidia.cublas`, `nvidia.cudnn`, and `nvidia.cuda_nvrtc`, resolves each package's `bin` dir via `importlib.resources.files()`, calls `os.add_dll_directory()` (for Python-level loads), and then calls `ctypes.WinDLL(abs_path)` on every `.dll` in that dir. Preloading by absolute path puts the DLLs in the process's module table; CTranslate2's subsequent short-name `LoadLibrary` calls resolve from that table regardless of PATH.
3. `src/voice_commander/transcriber.py` imports `_cuda_setup` and calls `_cuda_setup.register()` **before** `from faster_whisper import WhisperModel`. Order matters: the DLLs must be mapped into the process before CTranslate2's native code runs.
4. `register()` is Windows-only (no-op on other platforms) and idempotent.

## Consequences

### Positive
- Daemon launches successfully from any shell — stale PATH entries, fresh terminals, IDE run configurations, CI containers all work identically.
- Users do not need to install CUDA Toolkit or cuDNN manually. `uv sync` is enough.
- Venv is fully self-contained and portable: zip the project + venv and it runs on any Windows machine with a recent NVIDIA driver.
- No coupling to the launcher. `start.ps1`, `uv run voice-commander`, `pytest`, and an IDE green-arrow all behave the same.

### Negative
- Venv size grows by ~800 MB (cuBLAS + cuDNN kernels are large). Acceptable cost for dev/user machines; we would revisit for a shipped installer.
- The DLL preload call happens at `transcriber.py` import time, adding a small delay (< 100 ms on a warm disk). Acceptable — the daemon only imports transcriber once at startup.
- Users of non-NVIDIA GPUs (or no GPU) still pay the pip download cost even though they would set `config.toml` `transcription.device = "cpu"`. Revisit if we ever offer a `cpu-only` extras flavour.

### Neutral
- Pip-package cuDNN ships both the dispatcher shim (`cudnn64_9.dll`) and the full kernel set. CTranslate2 also ships its own shim. Both get loaded into the process; they do not conflict because the shim is a thin routing layer — multiple shim copies at different module base addresses resolve to the same underlying kernels through the dynamic dispatch.
- `_cuda_setup.register()` catches `OSError` during `WinDLL` calls (some DLLs may have dependencies the loader rejects in isolation) and logs at DEBUG. Observed in practice: all DLLs load cleanly.

## Alternatives considered

### System CUDA install + user manages PATH
The original MVP assumption. Rejected because stale terminal env blocks break it silently and the error message points at a missing DLL that is in fact present on the registry PATH. Shifts a hard troubleshooting burden onto every user. Also requires the user to separately obtain cuDNN, which is gated behind an NVIDIA developer login.

### Prepend PATH inside `start.ps1` before `uv run voice-commander`
Works for the launcher happy path but fails for any other entry point: raw `uv run voice-commander`, `pytest`, an IDE run configuration, CI, a packaged `.exe` built with PyInstaller. Couples the launcher to an unrelated concern. Implemented briefly as commit `5092028` and reverted — the fix belonged in the Python import path, not the shell layer.

### Python-level `os.environ['PATH']` prepend or `os.add_dll_directory()` only
Rejected. `os.environ` is not read by native `LoadLibraryExW` after process start (Windows snapshots the process env block). `os.add_dll_directory` only affects DLL loads made through Python's own DLL-search hooks, not arbitrary native code.

### Build CTranslate2 from source with statically linked cuBLAS / cuDNN
Maximally robust but requires a C++ toolchain and adds minutes to every build. CTranslate2's pre-built wheels are a hard requirement we do not want to relax.

## References
- ADR 0004: faster-whisper on CUDA with small.en model
- `docs/gotchas.md` §2 — operational diagnostics for the underlying DLL-loader quirk
- `src/voice_commander/_cuda_setup.py` — implementation
- CTranslate2 Windows wheel: https://pypi.org/project/ctranslate2/
- Windows DLL search order: https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order
- Python `os.add_dll_directory` semantics: https://docs.python.org/3/library/os.html#os.add_dll_directory

---

## Amendment — 2026-04-21

The original *Decision* bullet 1 claimed "users do not need a system CUDA install." **This is incorrect in practice and should not be relied upon.**

### What changed

- faster-whisper's own documentation lists **cuBLAS for CUDA 12** and **cuDNN 9 for CUDA 12** as prerequisites that must be installed on the system (<https://github.com/SYSTRAN/faster-whisper#gpu>).
- Real-world installs on clean Windows machines fail without the CUDA Toolkit 12.x and cuDNN 9.x MSI packages registered system-wide — the pip wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) ship the headline DLLs but not the full set of transitive dependencies CTranslate2 ends up resolving (NVIDIA driver runtime shim, `cudart`, `nvrtc`, etc.).
- The NVIDIA driver itself is obviously not pip-installable; that was always implicit but was not surfaced in the original ADR.

### Revised decision

1. **Users must install CUDA Toolkit 12.x and cuDNN 9.x via NVIDIA's official MSI installers, and both must be on `PATH`.** The README's *CUDA setup* section walks through this.
2. The `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` pip deps **stay** — they pin the exact ABI CTranslate2 was built against and make the venv reproducible — but they supplement the system install, they do not replace it.
3. The `_cuda_setup.register()` ctypes preloading shim **stays** — it still solves the original stale-`PATH` / process-env-snapshot problem that motivated this ADR. When a system install is present but a shell session carries a stale `PATH`, the shim makes `uv run voice-commander`, `pytest`, and IDE run configurations all load the correct DLLs from the venv.
4. CPU-only users can skip the system install entirely by setting `[transcription] device = "cpu"` in `config.toml`. No code change required.

### Lesson learned

The "self-contained venv" framing survived because we tested exclusively on machines that already had CUDA installed from a prior project. A clean-machine validation was never run during Phase 2. Future ADRs making portability claims must include a clean-machine smoke test as part of the acceptance criteria.
