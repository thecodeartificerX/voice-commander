# ADR 0042: Resolver Module — Pure Functions for Parameter Grounding

**Status:** Accepted
**Date:** 2026-04-21

## Context

`focus(target)` and `open(target)` receive a free-form string from the LLM and must map it onto a concrete operating-system object (an `hwnd` to focus, a launch token to hand to `os.startfile`). The grounding logic is non-trivial:

- `focus` needs to enumerate visible windows, score each by process-name and window-title similarity, and pick the best match.
- `open` needs to branch on whether the string is a URI, a filesystem path, or an app-display-name to be resolved against the Start Menu and `shell:AppsFolder`.

An earlier draft of this ADR proposed a `Resolver` *class* that also wrapped routing (calling `LLMRouter.route()` and firing `on_miss()`). That design conflated two unrelated responsibilities and got discarded during implementation. Routing stayed in `StreamingDaemon._process_utterance()` (a direct `LLMRouter.route()` call — no wrapper). Parameter grounding moved into a dedicated module of pure functions.

## Decision

`src/voice_commander/resolver.py` exports two top-level functions and one configuration hook:

```python
def resolve_window(target: str) -> int: ...
def resolve_app(target: str) -> str: ...

def _set_config(config: LLMConfig) -> None: ...
```

### `resolve_window(target: str) -> int`

1. Import `win32gui` / `win32process` / `win32api`. If `pywin32` is unavailable, raise `FocusWindowError`.
2. `EnumWindows` over visible windows with non-empty titles; skip every other handle.
3. For each candidate, read `proc_name` via `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetModuleBaseName`. A permission-denied on `OpenProcess` is swallowed and the candidate is scored with `proc_name = ""`.
4. Score each candidate as `max(WRatio(target, proc_name), WRatio(target, title))`.
5. Sort descending, take top-3 for diagnostic reporting.
6. If the best score is below `focus_fuzzy_threshold` (default 70), raise `FocusWindowError(f"… top3={…}")`. Otherwise return the best-matching `hwnd`.

### `resolve_app(target: str) -> str`

1. If `target` matches `^[a-z][a-z0-9+\-.]*://` (URI scheme), return it verbatim.
2. Try `Path(target).exists()`; on success, return `str(Path(target).resolve())`.
3. Otherwise consult the module-level `_cache["apps"]` list of `(display_name, launch_token)` pairs. If empty, populate it by:
   - Recursively `rglob("*.lnk")` under `%ProgramData%\Microsoft\Windows\Start Menu\Programs` and `%APPDATA%\Microsoft\Windows\Start Menu\Programs`; each entry is `(lnk.stem, str(lnk))`.
   - Enumerating `shell:AppsFolder` via `win32com.client.Dispatch("Shell.Application").NameSpace("shell:AppsFolder").Items()`; each entry is `(item.Name, f"shell:AppsFolder\\{item.Path}")`.
4. Score each cached display name with `WRatio(target, display)`; take argmax.
5. Below `open_fuzzy_threshold` (default 70) → `OpenResolveError` with top-3. Otherwise return the winning launch token.

The cache is populated on first use, not at import time, so the `pythoncom` / `win32com` cost (hundreds of milliseconds) is paid once on the first `open` call rather than at daemon startup. The cache is never invalidated during the daemon's lifetime — adding a new app to the Start Menu requires a restart. A `_invalidate_app_cache()` hook exists for tests.

### Configuration injection

Thresholds are read via small private accessors:

```python
def _focus_threshold() -> int:
    cfg = _config_ref
    if cfg is not None and hasattr(cfg, "focus_fuzzy_threshold"):
        return int(cfg.focus_fuzzy_threshold)
    return _DEFAULT_FOCUS_THRESHOLD  # 70
```

`_config_ref` is a module-level `Any = None` slot. `daemon.build_streaming_daemon()` calls `resolver._set_config(cfg.llm)` exactly once after `Config.load()`. This is a pragmatic compromise:

- **Pure-per-call contract preserved.** `resolve_window` / `resolve_app` remain callable with no explicit `config` argument. Tests import them and call them directly without staging an `LLMConfig`.
- **No global mutable state beyond a single pointer.** `_config_ref` is only ever assigned in `_set_config`. Thresholds are read, never written.
- **Fallback on the default.** If `_set_config` is never called (e.g. in an isolated unit test), both functions still work with the hardcoded default 70.

## Consequences

### Positive

- `focus` and `open` stay small: one `resolver.resolve_*` call followed by Win32 activation / `os.startfile`. No grounding logic leaks into the primitive bodies.
- Pure-function API is trivially unit-testable: monkeypatch `win32gui.EnumWindows` / the AppsFolder enum, call the function, assert on return value or raised error.
- Threshold tuning is a config-file edit, not a code change. Validated defaults (70) work for the author's desktop; power users override in `config.local.toml`.
- The AppsFolder cache pays COM enumeration once; subsequent `open` calls are a single `WRatio` sweep over ~200 entries (<1 ms).

### Negative

- Module-level `_config_ref` is technically mutable global state. It is set exactly once at daemon startup and never mutated afterward, which is the least-bad way to avoid threading the `LLMConfig` through every primitive. Documented here so future maintainers do not try to "fix" it by passing config explicitly — that would bloat every primitive signature.
- The app cache does not refresh during daemon uptime. If a user installs a new app while the daemon is running, `open` cannot find it until a restart. Acceptable for voice-command ergonomics; listed as a known limitation.
- `resolve_window` takes O(n) on every call where n is the visible-window count. On a typical desktop that is <50 and sub-millisecond. If it ever becomes a hotspot, add an LRU cache keyed on `target`.

### Neutral

- `FocusWindowError` is defined in `tools/_win32.py`, not in `resolver.py` — existing callers in `primitives.focus` already import it from that module, and re-exporting from a second location would invite drift.
- `OpenResolveError` is defined in `resolver.py` directly. It is caught by `primitives.open_target` only for logging; there is no retry path.

## Alternatives considered

### Resolver as a class with injected config
Rejected. The only state it would carry is the `LLMConfig` pointer, which is cheaper as a module-level slot. A class with a single field and two methods is just a namespace with extra steps.

### Threshold as a function parameter
Rejected. Every `focus` / `open` call site would have to thread the threshold through, and the LLM has no way to know what the configured value is. Config-driven thresholds are a daemon-global knob.

### Eager AppsFolder enumeration at daemon startup
Rejected. Enumerating `shell:AppsFolder` via COM takes 200–500 ms. Paying that at startup is observable on daemon boot. Lazy population on first `open` amortises it into the user's first app-open command where the warm-up overhead is masked by the command's own work.

### Build a proper `Resolver` router class wrapping `LLMRouter`
Rejected during implementation. Routing is a single call in `StreamingDaemon._process_utterance()` — wrapping it in a class adds a file for zero behavioural gain. The daemon's three-line routing block is more readable than a one-method class.
