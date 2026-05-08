# Primitives-Only MVP — Design Spec

**Date:** 2026-05-08
**Status:** Approved, pending implementation plan
**Supersedes:** Partial — Merlin-gated verb router (2026-04-30) stays, but verb-router rules are reduced to primitives only.

## Problem

The catalog and codebase carry a long tail of features that pre-date the "primitives only" MVP direction:

- 9 disabled but still-importable legacy verbs (close, close_window, minimize, maximize, last, summon_commander, mute, done, ask_user)
- 4 disabled perception tools (get_focused_window, list_windows, get_clipboard, list_processes)
- speak / dictation toggle subsystem (Right-Ctrl mute key, speak-mode, dictation tool)
- Remote whisper.cpp transcription backend (unused)
- HUD `Summarizer` with LLM-fallback (Merlin-deferred → dead path)
- VerbRouter rules covering copy/cut/paste/undo/redo/save/refresh/minimize/maximize/close/new (semantic shortcuts that should be authored as graphs by the user)

Symptom that surfaced the cleanup: utterance "Click." → MISS, because VerbRouter has no `click` rule.

Cluttered surface. Hard to reason about. Every utterance lookup walks past dead branches. User wants clean slate: 7 action primitives + 4 perception primitives, one router (VerbRouter), Merlin gated and deferred.

## Goal

Reduce the LLM-visible + voice-visible tool surface to:

| Action (7) | Perception (4) |
|---|---|
| `click(button="left")` | `get_active_window_title()` |
| `focus(target)` | `get_cursor_pos()` |
| `open(target)` | `read_clipboard()` |
| `press(combo)` | `ocr_region(x,y,w,h)` |
| `scroll(direction, amount=3)` | |
| `type(text)` | |
| `wait(ms)` | |

Plus `no_match(reason)` — LLM-only escape hatch, hidden from VerbRouter.

VerbRouter rule set covers only direct primitive invocation. Anything semantic ("copy", "minimize", "new tab") is authored by the user as a graph in the Builder UI, dispatching to `press(combo=...)`.

## Architecture (post-cleanup)

```
HotkeyCtrl ─toggle─▶ StreamingRecorder ─ndarray─▶ Transcriber ─text─▶ Router ─plan─▶ Dispatcher ──▶ primitive
  pynput            sounddevice + soxr +          faster-whisper      ↓                 run_plan()    + resolver.*
                    silero-vad                    (CUDA, small.en)    │                                (focus/open)
                                                                      ├─ normal: VerbRouter (deterministic first-word)
                                                                      └─ merlin: LLMRouter (LM Studio, gated, deferred)
                                                                      │ if both miss → feedback.on_miss()
                                                                      ▼
                                                                  graph tool? GraphRuntime (Builder-authored DAGs)
```

Threads: PortAudio callback → VAD worker → pipeline worker, hotkey listener, 1 Hz heartbeat. Unchanged.

Subsystems retained: VerbRouter, LLMRouter (gated), Builder SPA + GraphRuntime + GraphStore + registrar, Observability (runs.db, /page/runs, span tree, error taxonomy), Sprite + HUD (animations preserved, summarizer stripped), Web prompt inspector.

Subsystems deleted: speak/dictation, RemoteTranscriber, HUD Summarizer LLM fallback, all legacy semantic verbs, all disabled perception tools.

## Tool surface — exact deletions

### `src/voice_commander/tools/primitives.py`

**Delete functions:** `close`, `close_window`, `minimize`, `maximize`, `_show_window`, `_close_with_verify`, `last`, `summon_commander`, `mute`, `done`, `ask_user`.

**Delete supporting state:** `_set_mute_callback`, `_mute_callback`, `_COMMANDER_CWD`, `_COMMANDER_CMD`, `_CLOSE_VERIFY_TIMEOUT_MS`, `_CLOSE_VERIFY_POLL_INTERVAL_MS`.

**Keep:** `focus`, `type_text` (registered as `type`), `open_target` (registered as `open`), `press`, `wait`, `click`, `scroll`, `no_match`. Preserve `_LAUNCH_BLOCKLIST`, `_SYSTEM_PATH_RE`, `_PRESS_BLOCKLIST`, `_MAX_TYPE_TEXT_LEN`, `_OPEN_VERIFY_*` and `_verify_open()`.

### `src/voice_commander/tools/primitives.toml`

**Delete blocks:** `[tools.close]`, `[tools.close_window]`, `[tools.minimize]`, `[tools.minimize.args.target]`, `[tools.maximize]`, `[tools.maximize.args.target]`, `[tools.last]`, `[tools.last.args.tab]`, `[tools.last.returns.hwnd]`, `[tools.summon_commander]`, `[tools.mute]`, `[tools.done]`, `[tools.done.args.success]`, `[tools.done.args.summary]`, `[tools.ask_user]`, `[tools.ask_user.args.question]`, `[tools.ask_user.args.options]`.

**Keep:** focus, type, open, press, wait, click, scroll, no_match.

### `src/voice_commander/tools/perception.py`

**Delete functions:** `get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`, `_get_process_name`.

**Keep:** `read_clipboard`, `get_active_window_title`, `get_cursor_pos`.

### `src/voice_commander/tools/perception.toml`

**Delete blocks:** `[tools.get_focused_window]`, `[tools.list_windows]`, `[tools.get_clipboard]`, `[tools.list_processes]`.

**Keep:** read_clipboard, get_active_window_title, get_cursor_pos.

### `src/voice_commander/tools/_system.py` + `_system.toml`

**Delete files entirely.** Removes `speak()` from registry.

### `src/voice_commander/tools/ocr.py` + `ocr.toml`

**Untouched.** `ocr_region` already a primitive.

## Daemon strip

### `src/voice_commander/daemon.py`

**Delete:**
- `_speak_mode` field, `_speak_fuzzy_threshold` field
- `on_speak_toggle()` method
- Speak-mode wake-word block in `_process_utterance` (`if self._speak_mode: ...`)
- `tool_primitives._set_mute_callback(daemon.on_scroll_lock)` call in `build_streaming_daemon`
- `mute_key` parameter in `run()`, mute-key binding branch
- Speak-mode resets in `on_scroll_lock` and `shutdown`
- `import rapidfuzz.fuzz` if unused after speak-mode removal (verify)

**Verify retained:** Merlin mode (`_merlin_mode`, `_is_merlin_toggle`) — gated LLM path stays.

### `src/voice_commander/config.py` + `config.toml`

**Delete:**
- `[hotkey].mute_key` field + Config dataclass field
- `[transcription].backend`, `remote_endpoint_url`, `remote_timeout_ms`
- `[speak]` block (`fuzzy_threshold`)
- `cfg.llm.warmup_on_startup` stays (Merlin retained)

**Update:** `start.ps1` to drop mute-key reference if present.

### `src/voice_commander/transcriber.py`

**Delete:** `RemoteTranscriber` class. Simplify `TranscriberProtocol` if its sole non-`Transcriber` impl was `RemoteTranscriber` — collapse to direct `Transcriber` import in daemon.

**Update:** `build_streaming_daemon` `if cfg.transcription.backend == "remote"` branch — remove. Always construct `Transcriber`.

## VerbRouter rewrite

### `src/voice_commander/verb_router.py`

**Add to `VerbRule` dataclass:**

```python
@dataclass(frozen=True)
class VerbRule:
    name: str
    aliases: tuple[str, ...]
    default_target: RouteTarget | None = None
    subcommands: tuple[SubcommandRule, ...] = ()
    raw_tail_tool: str | None = None
    raw_tail_arg: str | None = None
    tail_coerce: Callable[[str], object] | None = None  # NEW
```

**Update `route()`:** in the `raw_tail_tool` branch, apply `tail_coerce(tail)` if set.

**Replace `build_default_rules()`:**

```python
def build_default_rules() -> tuple[VerbRule, ...]:
    return (
        VerbRule("click",  ("click",),  default_target=RouteTarget("click",  {})),
        VerbRule("scroll", ("scroll",),
                 default_target=RouteTarget("scroll", {"direction": "down"}),
                 subcommands=(
                     SubcommandRule(("up",),   RouteTarget("scroll", {"direction": "up"})),
                     SubcommandRule(("down",), RouteTarget("scroll", {"direction": "down"})),
                 )),
        VerbRule("focus", ("focus",), raw_tail_tool="focus", raw_tail_arg="target"),
        VerbRule("open",  ("open",),  raw_tail_tool="open",  raw_tail_arg="target"),
        VerbRule("type",  ("type",),  raw_tail_tool="type",  raw_tail_arg="text"),
        VerbRule("press", ("press",), raw_tail_tool="press", raw_tail_arg="combo"),
        VerbRule("wait",  ("wait",),  raw_tail_tool="wait",  raw_tail_arg="ms",
                 tail_coerce=lambda s: int(s.split()[0])),
    )
```

`wait` coerce is permissive: takes `"500"`, `"500 ms"`, `"500 milliseconds"` → `500`.

## Graph / Workflow wipe

- `commands.json` → `[]`
- `workflows.json` → `[]`
- `commands.default.json` / `workflows.default.json`: verify already `[]` (commit 3fd61a4)
- Validator behavior: existing graphs referencing deleted tools (close/minimize/speak/etc) fail loud at startup. User re-authors in Builder. No migration tooling — boil the ocean.

## HUD summarizer strip

Locate `Summarizer` class (search: `class Summarizer` under `voice_sprite/` and `src/voice_commander/`). Delete the LLM-fallback method. `ChatLogRenderer` consumes `tool_fired` events and renders the raw `tool_name` string. On `plan_outcome` with `status=miss`, renders `"no match"`. Sprite state machine and animations untouched.

## Web UI

- Builder SPA (React Flow): unchanged. Palette auto-narrows to surviving 11 primitives + any user graphs. No code change needed in `web/builder-ui/` — palette reads from `/api/tools`.
- Prompt inspector: unchanged.
- `/page/runs`: unchanged.
- `/page/primitives`: unchanged (just shows fewer entries).

## Docs

- `CLAUDE.md` — update "Current state" paragraph: verb-router is the daily path, Merlin deferred, catalog reduced to 11 primitives, graph runtime preserved for Builder-authored user commands
- `docs/architecture.md` — shrink tool list table, delete agentic-loop references that depended on deleted perception tools
- `docs/agents/technical-decisions.md` — append row for ADR 0074
- `docs/decisions/0074-primitives-only-mvp.md` — new ADR recording this decision
- `README.md` — drop Right-Ctrl mute hotkey reference; Scroll Lock is sole hotkey

## Testing

| Layer | Coverage |
|---|---|
| Unit (primitives) | Each primitive: arg validation, blocklist, log-on-bad-input, no-op vs raise. Existing tests retained, deleted-tool tests removed. |
| Unit (verb router) | Every rule routes correctly. Punct strip ("Click." → click). `wait 500` → `wait(ms=500)`. `wait 500 ms` → same. `scroll` → down. `scroll up` → up. Unknown verb → None. |
| Integration | Daemon `_process_utterance` smoke: stub transcript → VerbRouter plan → dispatcher fires registry tool. Mock `pyautogui` + `win32gui`. |
| Manual E2E | Smoke matrix: "click", "scroll up", "press control t", "type hello world", "open notepad", "focus chrome", "wait 500". Each fires correct primitive. Merlin toggle still works both directions. Builder loads, palette = 11 primitives, drag-drop a primitive node, save, hot-reload registers it. |

**Test deletions:** all tests against `close`, `close_window`, `minimize`, `maximize`, `last`, `summon_commander`, `mute`, `done`, `ask_user`, `speak`, `get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`, `RemoteTranscriber`. Files removed entirely where they cover only deleted tools.

## Risk register

| Risk | Mitigation |
|---|---|
| User has uncommitted graph using deleted tool | Validator fails loud at startup with tool name. User re-authors. Acceptable — boil the ocean. |
| LLM prompt template references deleted tools | Re-render template against live registry on hot-reload. Audit `prompt_template.txt` during Phase 7. |
| Sprite/HUD breaks on unknown tool name | HUD renders raw string per "just shows what tool is being triggered". Default state thinking → tool_name string. |
| `mute()` voice command users have wired | Deleted. Scroll Lock toggles session. Documented in README. |
| Right-Ctrl muscle memory | README note. Scroll Lock sole hotkey. |
| `param_resolver._set_config(cfg.llm)` still required | Yes — `focus`/`open` use `resolve_window`/`resolve_app` with fuzzy thresholds. `[llm].focus_fuzzy_threshold` and `[llm].open_fuzzy_threshold` stay in config. |

## Out of scope

- Merlin prompt re-tuning (deferred)
- New primitives (drag, double-click, etc)
- Graph migration tooling
- LLMRouter behavior changes
- Builder SPA UI changes (palette auto-narrows from registry)

## Phasing

1. **Tool surface shrink** — delete primitives.py functions, perception.py functions, _system.py, primitives.toml + perception.toml entries
2. **Daemon strip** — speak-mode, mute-key, RemoteTranscriber, related config
3. **VerbRouter rewrite** — primitives-only rule set + tail_coerce
4. **Graph wipe** — commands.json + workflows.json → []
5. **HUD summarizer strip** — Summarizer LLM fallback gone
6. **Verification** — tests + manual smoke
7. **Docs** — CLAUDE.md, architecture.md, ADR 0074, README

Each phase commits independently. Phase 6 verification is the human-in-the-loop validation gate.

## Acceptance criteria

- `python -m pytest` green
- Daemon starts, web UI loads, sprite + HUD animate
- All 11 primitives invokable via voice (where applicable: `click`, `scroll`, `focus X`, `open X`, `type X`, `press X`, `wait N`)
- "Merlin" voice toggle flips to LLM path; "merlin" again flips back
- Builder palette shows 11 entries
- Drag-dropping a primitive into a fresh graph saves and hot-reloads
- No references in code or config to: speak, mute_key, close, close_window, minimize, maximize, last, summon_commander, done, ask_user, get_focused_window, list_windows, get_clipboard, list_processes, RemoteTranscriber, transcription.backend
