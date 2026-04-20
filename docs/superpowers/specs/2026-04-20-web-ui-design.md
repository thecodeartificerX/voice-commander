# Command Management Web UI — Design Spec

**Date:** 2026-04-20
**Status:** Approved (brainstorming complete, ready for Archon dispatch)
**Phase:** 7 — Command management web UI
**Depends on:** Phase 6 (VAD streaming) — branches off `feat/vad-streaming` OR `main` after VAD merges

## Problem

Voice Commander's 14 tool phrases currently live inline in Python decorator args: `@tool(phrases=["copy", "copy that"])`. Adding/editing/categorising commands requires editing `.py` files and restarting the daemon. No way to see the whole command vocabulary at a glance. No enable/disable. No categories beyond module name.

## Goal

Local-only dark-themed web UI at `http://127.0.0.1:8765`, auto-opened by `start.ps1`. Lists all registered voice commands grouped by category. Each tool card shows phrases, description, category, enabled-toggle. "Edit" swaps card to inline form → Save commits → sidecar TOML writes → live registry reload → next utterance uses new phrases. No daemon restart.

## Non-goals

- Authentication — local-only, bound to 127.0.0.1.
- Adding/deleting tools (requires code gen) — future Phase.
- Rich tool authoring (parameters, chained actions) — future Phase.
- Remote/networked access.

## Locked decisions

| Q | Decision | Why |
|---|---|---|
| Metadata source of truth | **Sidecar TOML per tool module** (`tools/clipboard.toml` next to `tools/clipboard.py`) | TOML safe to edit programmatically; decorator becomes metadata-free; UI never touches Python |
| Stack | **FastAPI + HTMX + Jinja + vendored Tailwind** | Zero JS build, server-rendered, ~200 LOC server, HTMX-native save cycle |
| Topology | **Embedded** — uvicorn thread inside daemon process | Shared `ToolRegistry` singleton, zero IPC, one process, one venv |
| Hot reload | **Metadata-only reload today, full-module reimport-ready later** | TOML-only mutation needed now; API stable for future rescan |
| Scope | **View + edit phrases/description/category + enable/disable**, D-ready (add/delete later) | Biggest UX win without code gen; enable flag one TOML field |
| `start.ps1` integration | **Auto-open browser on daemon launch**, `-NoUI` / `-UIPort N` / `-NoOpenBrowser` flags | User asked for bundled start; dark UI pops automatically |
| Migration | **One-shot script** `scripts/migrate-to-sidecar-toml.py` | Mechanical; commit TOMLs; delete phrases kwarg from decorators; zero ambiguity post-migration |
| Save UX | **Edit mode + Save/Cancel buttons** | Safer than blur-save; HTMX-native one round-trip |
| Threading | **uvicorn on daemon-owned thread** | Single process; `threading.Lock` guards registry mutation |

## Architecture

### Thread topology

```
[Main thread]           [HotkeyCtrl thread]    [VAD/Pipeline threads*]    [uvicorn thread]
Daemon orchestrator     pynput listener        (existing)                 FastAPI app
owns registry,                                 shares ToolRegistry        handles HTTP
reload_lock                                    singleton under            uses reload_lock
                                               reload_lock                for mutations
```

* VAD/Pipeline threads exist after Phase 6 (VAD streaming) lands. If UI branches from `main` before VAD merges, the existing Phase3 recorder thread is what shares the registry.

### Data flow

1. Daemon startup — `discover()` imports all `tools/*.py`, decorator registers bare `@tool()` functions, then `ToolMetadataStore.load_all()` reads all `tools/*.toml`, `registry.bind_metadata()` pairs each `ToolEntry` with its TOML metadata. Pairing check: every `@tool()` function must have a matching TOML entry; every TOML entry must have a matching function. Mismatch → `ToolMetadataError`, abort.
2. `WebServer.start()` spawns uvicorn on 127.0.0.1:8765 (configurable, with port-bump fallback).
3. `start.ps1` `Start-Job` → `Start-Process http://127.0.0.1:8765` after 1.5s delay (browser opens).
4. User clicks Edit on a tool card → `GET /tool/{name}/edit` → HTMX swaps card to `_tool_edit.html` form fragment.
5. User edits → clicks Save → `POST /tool/{name}` with form data.
6. Server validates (non-empty phrases, no duplicate phrases across tools). On fail → 400 + error banner fragment.
7. Server acquires per-tool file lock → `ToolMetadataStore.save()` atomic tmp + rename → release file lock.
8. Server acquires `reload_lock` → `registry.reload_metadata(store)` re-reads TOMLs, mutates `ToolEntry.phrases/description/category/enabled` in place → release `reload_lock`.
9. Server renders updated `_tool_card.html` fragment and returns.
10. HTMX swaps form → updated card. No page reload.
11. Matcher's next `match()` call sees updated phrases (reads `registry.flat_phrases_enabled()` under `reload_lock`).

### State machine

No explicit state machine. Registry is mutable shared state guarded by `reload_lock`. File writes are atomic and per-file-locked. UI is stateless per request (HTMX swaps).

## Subsystem contracts

### New

**`ToolMetadata(name, phrases, description, category, enabled)`** — dataclass — `src/voice_commander/tool_metadata.py`

**`ToolMetadataStore(tools_dir: Path)`** — `src/voice_commander/tool_metadata.py`
- `load_all() -> dict[str, ToolMetadata]` — glob `**/*.toml` next to `.py`, parse, flatten module-level `category` default with per-tool override.
- `load_one(name: str) -> ToolMetadata`
- `save(name: str, md: ToolMetadata) -> None` — atomic write (write to `.tmp`, `os.replace`), per-tool file lock via `portalocker`.
- `path_for(name: str) -> Path`
- `file_lock(name: str) -> ContextManager` — per-tool lockfile under `tools/.locks/`.

**`WebApp.create_app(registry, store, reload_lock) -> FastAPI`** — `src/voice_commander/web/app.py`
- Routes: `GET /`, `GET /healthz`, `GET /tool/{name}/edit`, `GET /tool/{name}/cancel`, `POST /tool/{name}`, `POST /tool/{name}/toggle`.
- Jinja templates: `index.html`, `_tool_card.html`, `_tool_edit.html`, `_error_banner.html`.
- Static: `tailwind.min.css`, `htmx.min.js`, `app.css`.

**`WebServer(app, host, port)`** — `src/voice_commander/web/server.py`
- `start() -> None` — spawns `threading.Thread(target=_run_uvicorn, daemon=True)` with port-bump fallback (8765 → 8766 → ... up to +10 tries).
- `stop(timeout=5.0) -> None` — sets `uvicorn.Server.should_exit = True`, joins thread.
- `bound_port: int | None` — actual port after start (may differ if bumped).

**`scripts/migrate-to-sidecar-toml.py`** — one-shot: imports all tool modules, introspects decorator args, writes `<module>.toml` per module, prints instructions to remove `phrases=[...]` kwarg from decorators.

### Modified

**`ToolRegistry`** — `src/voice_commander/registry.py`
- Extend `ToolEntry` with `description: str`, `category: str`, `enabled: bool`.
- Change `@tool()` decorator — bare, no args. Stores func + name + module only. Metadata bound post-import.
- New: `bind_metadata(store: ToolMetadataStore) -> None` — at `discover()` end, for each entry pair with TOML.
- New: `reload_metadata(store: ToolMetadataStore) -> None` — under external lock, refresh metadata from disk.
- New: `all_enabled() -> list[ToolEntry]`
- New: `flat_phrases_enabled() -> list[tuple[str, str]]`
- New exception: `ToolMetadataError`.

**`@tool`** decorator — becomes argument-less `@tool()`. Migration script + codemod removes old `phrases=[...]`.

**Matcher** — `src/voice_commander/matcher.py`
- Uses `registry.flat_phrases_enabled()` instead of `flat_phrases()`.
- Holds `reload_lock` during choice snapshot (μs-scale).

**StreamingDaemon** (or `Phase3Daemon` if pre-merge) — `src/voice_commander/daemon.py`
- Constructor gains `web_server: WebServer | None` parameter.
- `run()` calls `web_server.start()` after transcriber load, before entering main loop.
- `shutdown()` calls `web_server.stop()` before stopping other subsystems.
- `build_*` factory reads `[web]` config, constructs `WebServer` with `create_app(registry, store, reload_lock)`.

**`Config`** — `src/voice_commander/config.py`
- Add `WebConfig(enabled: bool, host: str, port: int, auto_open_browser: bool)`.
- Parse `[web]` section from `config.toml`.

**Tool modules** (`tools/{clipboard,browser,window,system}.py`)
- Strip `phrases=[...]` kwargs. Decorator becomes bare `@tool()`.

**`start.ps1`**
- Add parameters: `-NoUI`, `-UIPort N`, `-NoOpenBrowser`.
- Read `[web]` from `config.toml` via existing `uv run python -c "..."` pattern.
- If `web.enabled` and `web.auto_open_browser` and not `-NoOpenBrowser`, `Start-Job` → `Start-Process $url` after 1.5s delay.
- Pass `-NoUI` as env var `VOICE_COMMANDER_WEB_DISABLED=1` so daemon skips server thread.

### Unchanged
`HotkeyController`, `Transcriber`, `Dispatcher`, `FeedbackSink`, existing recorder / VAD subsystems, existing hotkey flow.

## TOML schema

One sidecar per Python module in `src/voice_commander/tools/`:

```toml
# tools/clipboard.toml
category = "clipboard"   # module-level default

[tools.copy]
phrases = ["copy", "copy that", "copy selection"]
description = "Sends Ctrl+C to copy the current selection."
enabled = true

[tools.paste]
phrases = ["paste", "paste it", "paste here"]
description = "Ctrl+V."
enabled = true

[tools.cut]
phrases = ["cut", "cut selection", "cut that"]
description = "Ctrl+X."
enabled = true

[tools.select_all]
phrases = ["select all", "select everything"]
description = "Ctrl+A."
enabled = true
# Optional per-tool category override:
# category = "editing"
```

**Pairing:** `tools.<key>` names match decorated Python function names within the module. Unpaired entries on either side raise `ToolMetadataError` at daemon startup.

## Config schema

```toml
# config.toml (new section)
[web]
enabled = true
host = "127.0.0.1"
port = 8765
auto_open_browser = true
```

## URL + routes

| Method | Path | Returns | Purpose |
|---|---|---|---|
| GET | `/` | full HTML | dashboard, tools grouped by category |
| GET | `/healthz` | JSON `{"status":"ok"}` | liveness |
| GET | `/tool/{name}/edit` | HTML fragment (form) | swap card to edit form |
| GET | `/tool/{name}/cancel` | HTML fragment (card) | swap form back to read-only |
| POST | `/tool/{name}` | HTML fragment (card) OR 400 + error banner | save edits, return updated card |
| POST | `/tool/{name}/toggle` | HTML fragment (card) | flip enabled, return card |

All non-`/` routes return **HTML fragments** (not full pages) — HTMX `hx-swap="outerHTML"`.

## UI layout

Dark theme via Tailwind. Palette:
- bg: `bg-neutral-950` (near-black)
- card: `bg-neutral-900` + `border-neutral-800`
- primary accent: `text-emerald-400` (category badges, enabled toggle)
- phrase chips: `bg-neutral-800 text-neutral-300`
- disabled tool: dimmed + strikethrough phrases

Tools grouped by category section (Clipboard / Browser / Window / System). Each card: name, category badge, description, phrase chips, Edit button, Enable/Disable button. Edit mode: same card swapped to form with description textarea, category input, phrases textarea (one per line), enabled checkbox, Cancel/Save buttons.

## Reload mechanism + concurrency

```python
# Save path (POST /tool/{name})
with store.file_lock(name):
    store.save(name, new_metadata)
with reload_lock:
    registry.reload_metadata(store)
# no matcher cache to invalidate — flat_phrases_enabled() is recomputed per match()

# Read path (Matcher.match())
with reload_lock:
    choices = registry.flat_phrases_enabled()
return rapidfuzz.process.extractOne(utterance, choices, ...)
```

- `reload_lock: threading.Lock` — global registry mutation lock. Held μs for reads, ms for saves.
- Per-tool `portalocker` file lock — prevents two browser tabs corrupting the same TOML.
- Rebuild of rapidfuzz choices per `match()` is cheap (14 × 3 = 42 strings). No choice-cache needed.

**Future A-ready:** `registry.rescan_tools()` grows later to do `importlib.invalidate_caches()` + `importlib.reload()` per tool module + re-`discover()`. API surface `reload_metadata()` stable today.

## Error handling

| Failure | Detection | Response |
|---|---|---|
| Port 8765 in use | `OSError` on `uvicorn.Server.startup()` | Log, try `port+1` up to `+10`, give up → daemon continues without UI, chime error |
| Sidecar TOML missing for registered tool | `discover()` pairing check | `ToolMetadataError` at startup, abort; instruct `scripts/migrate-to-sidecar-toml.py` |
| TOML entry for non-existent function | pairing check | `ToolMetadataError`, fail fast |
| Malformed TOML on save | `tomllib.TOMLDecodeError` | 400 + `_error_banner.html` fragment, TOML untouched |
| Duplicate phrase across tools after save | `Matcher` / registry collision check | 400 + "phrase 'copy' already used by 'clipboard.copy'", rollback (restore backup) |
| Empty phrases list on save | server-side validation | 400 + "at least one phrase required" |
| TOML write fail (disk full / perms) | `OSError` during atomic rename | 500 + error banner, registry untouched |
| Registry reload raises after TOML saved | exception in `reload_metadata()` | Log, feedback on_error chime, disk and registry briefly inconsistent; next daemon restart re-reads disk. Rare. Acceptable. |
| Browser auto-open fails | PowerShell `Start-Process` error | Non-fatal, print URL to console, daemon continues |
| FastAPI handler raises | FastAPI default exception handler + HTMX `hx-target-500` | 500 + error banner swap |
| WebServer thread crashes | `threading.excepthook` | Log, daemon continues degraded (hotkey still works) |
| Concurrent save from two tabs | per-tool `portalocker` file lock | Second save blocks briefly, proceeds on fresh state, last-write-wins |
| Server bound on daemon crash | OS cleans socket on exit | Next daemon run binds fresh |

## Testing strategy

### Unit tests (`tests/unit/`)

- `test_tool_metadata_store.py` — `load_all` round-trip, atomic save, file lock contention, malformed TOML raises, missing-file raises, per-tool category override, module-level category default.
- `test_registry_reload.py` — bootstrap registry with stub TOMLs + stub `@tool()` functions → mutate TOML on disk → `reload_metadata()` → assert phrases/category/description/enabled reflected in `ToolEntry`. Ensure func identity unchanged (no module reload today).
- `test_web_routes.py` — FastAPI `TestClient`:
  - `GET /` returns 200 full page with all tool names.
  - `GET /tool/copy/edit` returns form fragment (no `<html>` wrapper).
  - `POST /tool/copy` valid form → saves TOML + `reload_metadata` called + returns card fragment.
  - `POST /tool/copy` empty phrases → 400 + error banner fragment.
  - `POST /tool/copy` duplicate phrase → 400 + rollback (TOML unchanged).
  - `POST /tool/copy/toggle` flips `enabled`, returns card fragment with new state.
- `test_discover_pairing.py` — decorator without matching TOML → `ToolMetadataError`; TOML key without decorator → `ToolMetadataError`.
- `test_matcher_disabled_filter.py` — disabled tool's phrases absent from `flat_phrases_enabled()`; re-enable restores them.
- `test_web_server_thread.py` — `WebServer.start()` binds + `stop()` shuts down cleanly within timeout; port-bump fallback on EADDRINUSE.

### Integration tests (`tests/integration/`)

- `test_ui_end_to_end.py` — real uvicorn on random port + real registry + real tool modules. `httpx.Client` → GET dashboard → POST edit → verify TOML on disk + registry state + matcher picks new phrase.
- `test_save_triggers_match.py` — save new phrase "xerox" to tool `copy` → call `matcher.match("xerox")` → assert resolves to `copy`.

### Manual validation gate (human)

1. Run `start.ps1` → browser auto-opens → see all 14 tools grouped by category in dark theme.
2. Edit `copy` — add phrase "clone it", Save → card updates → press Scroll Lock → say "clone it" → clipboard updates (no daemon restart).
3. Disable `reload` → say "reload" → miss-chime (not dispatched).
4. Re-enable `reload` → say "reload" → fires.
5. Open two browser tabs, edit same tool concurrently → last save wins, no corruption.
6. Malformed save (empty phrases) → banner error, no file write.
7. Ctrl+C daemon → browser gets connection refused, daemon exits clean, no port stuck.
8. Port 8765 occupied → daemon retries 8766-8775, logs + UI on next free port.

### Fixtures

- `tests/fixtures/tools_sidecar/` — sample TOMLs for pairing tests.
- Stub `@tool()` functions registered dynamically in test setup.

## File changes

```
src/voice_commander/
  registry.py              [EDIT]  bare @tool() decorator; extend ToolEntry; reload_metadata(); all_enabled(); flat_phrases_enabled()
  tool_metadata.py         [NEW]   ToolMetadata + ToolMetadataStore
  matcher.py               [EDIT]  use flat_phrases_enabled()
  daemon.py                [EDIT]  daemon constructor gains web_server; run()/shutdown() wire it up
  config.py                [EDIT]  WebConfig dataclass + [web] parse
  tools/clipboard.py       [EDIT]  bare @tool()
  tools/clipboard.toml     [NEW]
  tools/browser.py         [EDIT]
  tools/browser.toml       [NEW]
  tools/window.py          [EDIT]
  tools/window.toml        [NEW]
  tools/system.py          [EDIT]
  tools/system.toml        [NEW]
  web/
    __init__.py            [NEW]
    app.py                 [NEW]   FastAPI app factory
    server.py              [NEW]   WebServer (uvicorn thread wrapper)
    templates/
      index.html           [NEW]
      _tool_card.html      [NEW]
      _tool_edit.html      [NEW]
      _error_banner.html   [NEW]
    static/
      tailwind.min.css     [NEW]   vendored
      htmx.min.js          [NEW]   vendored
      app.css              [NEW]   custom overrides

scripts/
  migrate-to-sidecar-toml.py  [NEW]

config.toml                [EDIT]  add [web] section
start.ps1                  [EDIT]  -NoUI / -UIPort / -NoOpenBrowser; auto-open browser

tests/unit/
  test_tool_metadata_store.py       [NEW]
  test_registry_reload.py           [NEW]
  test_web_routes.py                [NEW]
  test_discover_pairing.py          [NEW]
  test_matcher_disabled_filter.py   [NEW]
  test_web_server_thread.py         [NEW]

tests/integration/
  test_ui_end_to_end.py             [NEW]
  test_save_triggers_match.py       [NEW]

tests/fixtures/
  tools_sidecar/                    [NEW]

docs/decisions/
  0020-web-ui-embedded-fastapi.md        [NEW]
  0021-sidecar-toml-per-tool.md          [NEW]
  0022-htmx-over-spa.md                  [NEW]
  0023-metadata-only-hot-reload.md       [NEW]
  0024-bare-tool-decorator-migration.md  [NEW]
docs/architecture.md       [EDIT]  add web subsystem section
docs/libraries.md          [EDIT]  add fastapi, uvicorn, jinja2, portalocker
docs/gotchas.md            [EDIT]  uvicorn-on-thread asyncio pattern; portalocker on Windows

pyproject.toml             [EDIT]  add fastapi, uvicorn[standard], jinja2, portalocker
```

## Phase positioning

**Phase 7 — Command management web UI.** Lands after Phase 6 (VAD streaming). Branch from `feat/vad-streaming` (pragmatic — UI changes stack on top of VAD changes) OR from `main` after VAD merges.
