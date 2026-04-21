# R8: Windows App/URI Launch Primitives

**Date:** 2026-04-21  
**Context:** LLM-router tool needs `launch(app: str)` where `app` is freeform (executable names, paths, shortcuts, URIs, AppUserModelID).

## Research Summary

### 1. os.startfile(path) — Shell Launch

**What it accepts:**
- File paths (`.exe`, `.txt`, `.pdf`)
- URI schemes (`https://`, `mailto:`, `ms-settings:`)
- Registered file extensions (delegates to associated program)
- **NOT** AppUserModelID or Start-menu shortcut names directly

**Returns:** None (async, non-blocking); fires handler and returns immediately.

**Exception on failure:** `FileNotFoundError: [WinError 2]` if file/URI handler not found.

**Limitation:** Does not support UWP/Store apps by AUMID; cannot launch shortcut by name without full path.

---

### 2. subprocess.Popen([...], shell=True) vs shell=False

#### When shell=True is needed:
- PATH environment variable resolution (e.g., `["notepad"]` → searches %PATH%)
- Start-menu shortcut names via `start` command (Windows shell built-in)
- Environment variable expansion (`%APPDATA%`, `%PROGRAMFILES%`)
- Shell redirection/piping (not needed for launch)

#### When shell=False (preferred):
- Full paths (`"C:\\Program Files\\...\\app.exe"`)
- URI schemes (bypass shell, use `os.startfile`)
- Inherently safer; no shell parsing of metacharacters

#### Security risk of shell=True:
- Shell metacharacters (`;`, `|`, `&`, `$(...)`, `` ` ``) in the command string are interpreted by cmd.exe
- **Example injection:** `launch("notepad; del C:\\*")` → both commands execute
- **Mitigation:** Use `shlex.quote(app)` to escape user input before passing to shell; or pass as list and avoid shell entirely

#### Python 3.12+ security hardening:
- Changed Windows shell search order; current directory no longer checked before system PATH
- Reduces (but doesn't eliminate) risk of CWD-planted malicious `cmd.exe`

---

### 3. UWP / Microsoft Store App Launch

#### Method 1: AppUserModelID via explorer.exe
```
explorer.exe shell:AppsFolder\{AppUserModelID}
```
**Format:** `explorer.exe shell:AppsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App`  
**How to find AUMID:**
- Manual: Start → Run → `shell:Appsfolder` → Alt+V > Choose Details → enable "AppUserModelId"
- PowerShell: `Get-AppxPackage | Select-Object Name, PackageFamilyName` (gives Package Name only; AUMID is `PackageFamilyName!AppID`)
- Caveat: AUMID is install-specific; varies per user/machine

#### Method 2: winrt library (Python)
- Requires `pip install winrt`
- Async API: `winrt.windows.system.Launcher.launch_uri_async()` (for URIs) or `launch_file_async()` (for files)
- Advantage: Cleaner Python integration; can specify target app via `LauncherOptions`
- **Not in current pyproject.toml; would add dependency**

#### Method 3: Shortcut resolution (file system scan)
- Scan `%APPDATA%\Microsoft\Windows\Start Menu\Programs\` and `%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\`
- Read `.lnk` (shortcut) properties using `win32com.client` (PyWin32, already in pyproject.toml)
- Extract target path, args, working directory
- Launch resolved path with `os.startfile` or `subprocess.Popen`
- **Pros:** No new dependencies; works for all shortcuts. **Cons:** Slow (filesystem I/O); fragile if shortcut moved/deleted.

---

### 4. cmd /c start vs os.startfile

**Use `cmd /c start`:**
- Launching by shortcut name: `cmd /c start "" "notepad"` (empty title arg required for quoted name)
- Implies PATH resolution and shell features
- Example: `cmd /c start "" "Visual Studio Code"` if "Visual Studio Code" is a Start-menu shortcut

**Use `os.startfile`:**
- URIs, registered file extensions, full paths
- Simpler, no shell overhead
- Preferred when possible

---

### 5. Exception Handling & "Not Found" Detection

**FileNotFoundError [WinError 2]:**
- Raised by `os.startfile()` when file/URI handler not found
- Raised by `subprocess.Popen()` when executable not in PATH (if shell=False)

**No built-in "app not found" on shell=True:**
- `cmd /c start` does NOT raise exception; silently fails if app not found
- Must parse stderr or check process exit code if needed for diagnostics
- Recommend: Try `os.startfile` first (throws on missing), fall back to `cmd /c start` only if explicit PATH resolution needed

---

### 6. Start Menu Shortcut Resolution Trade-offs

**Scan filesystem approach:**
- **Pros:** 
  - Universal; works for any shortcut
  - No dependency on PATH or registry
- **Cons:**
  - Filesystem I/O overhead per launch
  - Shortcut may not exist or be deleted post-scan
  - Requires parsing `.lnk` binary (PyWin32 adds complexity)
  - User-created shortcuts may be anywhere in Start Menu

**Rely on PATH + cmd.exe approach:**
- **Pros:**
  - Windows handles resolution natively
  - Faster (no filesystem scan)
- **Cons:**
  - Only works if shortcut is in a standard Start-menu location indexed by PATH
  - Not all shortcuts are in PATH

**Recommendation:** Hybrid — try `os.startfile` first (covers URIs and full paths), then fall back to `cmd /c start ""` with the name (lets Windows shell resolve shortcuts via PATH). Only scan filesystem if both fail and we're desperate.

---

### 7. Security: LLM Input Sanitization

**Threat model:**
- LLM supplies `app` string (user-controlled indirectly via intent router)
- We assume user is benign (running own commands) but want defense-in-depth

**Safeguards:**
1. **Never use shell=True without escaping:**
   - If shell=True is unavoidable, wrap user input with `shlex.quote(app)` before embedding in command string
   - Example: `subprocess.Popen(f'start "" {shlex.quote(app)}', shell=True, ...)` NOT `subprocess.Popen(f'start "" {app}', ...)`

2. **Prefer list-based subprocess (shell=False):**
   - `subprocess.Popen([full_path_exe, arg1, arg2], shell=False)` — metacharacters treated literally
   - Safe even without escaping; shell never invoked

3. **Validate app string shape before launching:**
   - Reject if contains shell metacharacters and we're using shell=True: `if any(c in app for c in ';|&$`\\'"`); raise ValueError`
   - Or use `shlex.quote()` unconditionally

4. **Log what we launch:**
   - For debugging and security audit: log exact command before executing

---

## Recommended Resolution Strategy (Pseudocode)

```
launch(app: str) -> None:
    """Launch an app/URI/file. May raise CallerError if not found."""
    
    # Step 1: If it looks like a URI (contains ://), use os.startfile directly
    if '://' in app:
        try:
            os.startfile(app)
            return
        except FileNotFoundError:
            raise CallerError(f"URI handler not found: {app}")
    
    # Step 2: If it looks like a full path (/ or \\ present, especially with drive:)
    if '\\' in app or ':' in app:
        try:
            os.startfile(app)
            return
        except FileNotFoundError:
            raise CallerError(f"App not found at path: {app}")
    
    # Step 3: Try AppUserModelID (if it looks like one: contains dot)
    if '.' in app and '_' in app:  # heuristic: "Microsoft.App_hash!App" pattern
        try:
            # Use explorer.exe shell:AppsFolder trick
            subprocess.Popen(
                ['explorer.exe', f'shell:AppsFolder\\{app}'],
                shell=False
            )
            return
        except Exception:
            pass  # Fall through
    
    # Step 4: Try simple name via PATH + cmd.exe start (resolves shortcuts & executables)
    try:
        # Use shlex.quote to defend against injection if app is untrusted
        escaped_app = shlex.quote(app)
        subprocess.Popen(
            f'cmd /c start "" {escaped_app}',
            shell=True
        )
        return
    except Exception as e:
        raise CallerError(f"Failed to launch {app}: {e}")
```

---

## Library Status in pyproject.toml

**Already present:**
- `pywin32 >= 306` — supports `.lnk` shortcut reading if needed (optional fallback)

**Not present:**
- `winrt` — UWP native launch; adds 50+ MB; only needed if AUMID launching is common
- `shlex` — built-in stdlib; no addition needed

**Recommendation:** Avoid `winrt` for MVP. Use `explorer.exe shell:AppsFolder` via subprocess for AUMID (lower overhead). Add `winrt` only if profiling shows AUMID launches are slow or fail.

---

## Sources

- [os.startfile documentation](https://docs.python.org/3/library/os.html#os.startfile)
- [Python subprocess security](https://docs.python.org/3/library/subprocess.html)
- [Launch default app for URI](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-default-app)
- [shlex.quote for injection prevention](https://docs.python.org/3/library/shlex.html#shlex.quote)
- [Find AUMID (Application User Model ID)](https://learn.microsoft.com/en-us/windows/configuration/store/find-aumid)
- [Subprocess shell injection prevention guide](https://semgrep.dev/docs/cheat-sheets/python-command-injection)
- [Bandit subprocess security plugin](https://bandit.readthedocs.io/en/latest/plugins/b602_subprocess_popen_with_shell_equals_true.html)
- [How to open UWP apps from command line](https://www.addictivetips.com/windows-tips/open-uwp-apps-from-command-line-windows-10/)
