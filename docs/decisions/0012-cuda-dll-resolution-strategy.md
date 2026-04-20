# ADR 0012: CUDA DLL Resolution Strategy

**Status:** Accepted
**Date:** 2026-04-20

## Context

Voice Commander's `Transcriber` loads `faster-whisper` with `device="cuda"`, which causes
`ctranslate2` 4.x to call Windows' native `LoadLibraryExW` to resolve the following DLLs at
runtime:

- `cublas64_12.dll` — cuBLAS (from CUDA Toolkit 12.x)
- `cublasLt64_12.dll` — cuBLAS-Lt (from CUDA Toolkit 12.x)
- `cudnn64_9.dll` — cuDNN dispatcher shim (bundled inside `ctranslate2`'s wheel)
- `cudnn_graph64_9.dll`, `cudnn_ops64_9.dll`, etc. — cuDNN kernels (from cuDNN 9.x)

Windows' `LoadLibraryExW` resolves DLL names by scanning the directories in the **process
environment block's `PATH`** that was inherited when the process started. It does NOT consult
any subsequent mutations of that block — meaning that changes to `PATH` made after the Python
interpreter is already running (via `os.environ['PATH']` or any other mechanism) arrive too
late for native code within that same process to use.

The user's system has the correct DLLs installed:

- CUDA Toolkit 12.8 at `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin`
- cuDNN 9.11 for CUDA 12 at `C:\Program Files\NVIDIA\CUDNN\v9.11\bin\12.9`

Both are registered in the machine-level `PATH` in the Windows registry. However, Windows
Terminal / PowerShell sessions that were opened **before** a CUDA version upgrade carry a
**stale** `PATH` environment block that still references the deleted previous version (e.g.
`CUDA\v12.6\bin`). When `start.ps1` is launched from such a shell, `uv run voice-commander`
inherits the stale block and the daemon crashes at first encode:

```
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded.
```

This is a reproducible failure that affects any long-lived shell session that spans a CUDA
upgrade and cannot be fixed inside the Python process.

## Decision

Detect the installed CUDA Toolkit and cuDNN directories at **PowerShell script level** inside
`start.ps1`, and prepend them to `$env:PATH` before invoking `uv run voice-commander`.
Because this mutation happens in the PowerShell process before the child Python process is
created, the child inherits a corrected `PATH` from the start.

Two new Verb-Noun helper functions are added to `start.ps1`:

- **`Get-VoiceCudaPath`** — scans `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*\bin`
  for the highest-version directory containing `cublas64_12.dll`, and scans
  `C:\Program Files\NVIDIA\CUDNN\v*\bin\12.*` for the highest CUDA-12 sub-directory
  containing `cudnn_graph64_9.dll`. Returns a `[PSCustomObject]` with `CudaBin` and
  `CudnnBin` properties (either may be `$null` if not found, with a `Write-Warning` emitted).

- **`Add-VoiceCudaToPath`** — calls `Get-VoiceCudaPath`, then prepends any non-`$null` result
  to `$env:PATH`. Idempotent: entries already present are not duplicated.

`Add-VoiceCudaToPath` is called in all four launch paths immediately before `Start-VoiceDaemon`:
the `-Device N` path, the `-NoMenu` path, the non-interactive auto-detect path, and the full
interactive TUI path.

### Key assumptions

- CUDA Toolkit is installed under the standard NVIDIA path (`C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\`).
- cuDNN is installed as a standalone package under `C:\Program Files\NVIDIA\CUDNN\` (the layout
  used by the NVIDIA cuDNN installer for Windows since cuDNN 8.x). The older pattern of copying
  cuDNN files into the CUDA Toolkit directory is not assumed.
- The sentinel DLL for CUDA is `cublas64_12.dll` (not `cudart64_12.dll`, which may also be in
  `System32` on some configurations).
- The sentinel DLL for cuDNN is `cudnn_graph64_9.dll` (a cuDNN 9.x kernel library that is NOT
  bundled inside `ctranslate2` and therefore must come from the system installation).
- The auto-detect logic will continue working when Sakib upgrades to CUDA 12.9 / 13.x or
  cuDNN 9.12+, because it picks the **highest** version it finds rather than hard-coding a
  specific version.

### Fallback behaviour

If `Get-VoiceCudaPath` cannot locate a directory (wrong install path, missing DLL, CUDA not
installed), it emits a `Write-Warning` with the expected install path and the relevant download
URL, then returns `$null` for the missing component. `Add-VoiceCudaToPath` silently skips
`$null` components. The daemon is still launched; it will crash with the informative
`cublas64_12.dll is not found` message, which tells the user exactly what to install.

## Consequences

### Positive
- Eliminates the stale-PATH failure class entirely for any shell session launched after the
  PowerShell process starts — including old terminals that predate a CUDA upgrade.
- Zero configuration required: no `config.toml` entry, no environment variable the user must
  remember to set.
- Auto-detects future CUDA / cuDNN upgrades without changing `start.ps1`.
- Idempotent: running `start.ps1` multiple times in the same shell session does not accumulate
  duplicate PATH entries.
- A missing installation produces a clear, actionable warning rather than a cryptic Python
  traceback deep in ctranslate2 native code.

### Negative
- Only covers the standard NVIDIA installer path. Custom install locations (e.g. CUDA installed
  to `D:\CUDA`) are not detected. Users with non-standard locations must add the directories to
  their machine PATH manually (which is the correct permanent fix) or pass `-Verbose` to see
  which paths were/were not found.
- The auto-detect runs on every `start.ps1` invocation, adding a small filesystem scan. On a
  healthy machine with one CUDA version installed this is negligible (< 10 ms).

### Neutral
- `[Environment]::SetEnvironmentVariable(..., 'Machine')` is explicitly NOT used: that would
  permanently modify the user's system PATH, which is outside the scope of a launcher script
  and potentially surprising.

## Alternatives considered

### 1. `uv add nvidia-cublas-cu12 nvidia-cudnn-cu12` (pip CUDA packages)

Installs cuBLAS and cuDNN wheels into `.venv/Lib/site-packages/nvidia/*/bin`. This was
**attempted and rejected** because `ctranslate2` 4.7.1 ships its own `cudnn64_9.dll`
dispatcher shim bundled inside `.venv/Lib/site-packages/ctranslate2/`. When the pip
`nvidia-cudnn-cu12` package's `cudnn64_9.dll` is preloaded in the same process, two cuDNN
versions are resident simultaneously and produce garbage transcriptions — the model emits
repeated nonsense tokens ("cataclysmic cataclysm", etc.) from known-good audio. Pinning older
pip cuDNN versions (9.1.0.70, 9.2.1.18, 9.21.0.82) did not resolve the conflict. The
`pyproject.toml` was reverted and there are no residual `nvidia-*` pip dependencies.

### 2. `os.environ['PATH']` mutation inside Python

Setting `os.environ['PATH'] = '/path/to/cuda/bin:' + os.environ['PATH']` at the top of
`transcriber.py` or `daemon.py` was tried conceptually. **Rejected**: Windows' native
`LoadLibraryExW` uses a snapshot of the process environment block taken at process creation
time. Mutations via `os.environ` after the interpreter is running change the snapshot that
would be passed to *child processes* spawned by Python, but they do not affect the DLL search
path used by native code in the *current* process. The ctranslate2 CUDA load happens during
`import ctranslate2`, which occurs before any application code runs.

### 3. `os.add_dll_directory()` + `ctypes.WinDLL()` preload

`os.add_dll_directory(r'C:\...\CUDA\v12.8\bin')` adds a directory to the set of directories
searched when Python itself resolves DLLs for extension modules loaded via `importlib`.
**Rejected**: this mechanism (the `LOAD_LIBRARY_SEARCH_USER_DIRS` flag path) is only consulted
for Python-level DLL loading. When ctranslate2's native `.pyd` extension internally calls
`LoadLibraryExW` on `cublas64_12.dll`, it does not go through the Python loader and therefore
does not see the `add_dll_directory` additions. Attempting to preload `cublas64_12.dll` via
`ctypes.WinDLL(absolute_path)` before importing ctranslate2 caused version mismatches because
the DLL that was preloaded was not the same build that ctranslate2 was linked against.

### 4. Hardcoded absolute CUDA path in `start.ps1`

Hardcoding `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin` directly into the
script would fix today's problem but break immediately when CUDA is upgraded to 12.9 or 13.x.
**Rejected**: the auto-detect approach has no ongoing maintenance cost for standard upgrades.

### 5. Persistent machine-level PATH update via `[Environment]::SetEnvironmentVariable`

A one-time setup command that adds the CUDA dirs to the machine `PATH` permanently would solve
the stale-session problem by updating the registry source. **Rejected as out of scope for a
launcher script**: modifying machine-level environment variables requires elevation, is
surprising to users who expect `start.ps1` to be non-destructive, and is the user's
responsibility (or the CUDA installer's responsibility) to perform correctly. The NVIDIA
installer already does this — the stale-shell problem occurs specifically because existing
terminal sessions do not re-read the registry.

## Diagnostic commands

Check registry PATH (ground truth):
```powershell
[System.Environment]::GetEnvironmentVariable('PATH', 'Machine')
```

Compare with current shell PATH:
```powershell
$env:PATH
```

Verify CUDA DLLs are reachable from the current shell:
```powershell
Get-Command cublas64_12.dll -ErrorAction SilentlyContinue
```

Test ctranslate2 loads CUDA cleanly (before daemon launch):
```powershell
uv run python -c "import ctranslate2; print(ctranslate2.__version__)"
```

## References

- Spec: [../superpowers/specs/2026-04-19-voice-commander-design.md](../superpowers/specs/2026-04-19-voice-commander-design.md)
- Gotchas §10: [../gotchas.md](../gotchas.md)
- NVIDIA CUDA Toolkit installer: https://developer.nvidia.com/cuda-downloads
- NVIDIA cuDNN installer: https://developer.nvidia.com/cudnn-downloads
- CTranslate2 Windows docs: https://opennmt.net/CTranslate2/installation.html
- Windows DLL search order: https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order
