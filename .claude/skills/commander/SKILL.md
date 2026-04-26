---
name: commander
description: Interactive skill for creating voice-commander primitives, commands, and workflows. For primitives: walks through a seven-question interview, then writes the Python function in `tools/primitives.py`, its sidecar TOML entry, its unit test, and (when disambiguation is involved) the system prompt. For commands/workflows: interviews for graph metadata and node/edge definitions, then writes canonical JSON to `commands.json`/`workflows.json` via GraphStore and runs `--validate`. Also handles editing, renaming, toggling, and deleting existing verbs. Use whenever the user says "new voice command", "add a verb", "create a voice tool", "build a workflow", "make a command", "edit voice command", "change tool description", "rename command", "disable command", "delete command", "extend the voice commander", "/commander", or otherwise wants to wire up, modify, or remove an LLM-routable verb. Invoke even when the user does not name the skill — any request to add, change, or remove an LLM-visible verb here should trigger it.
---

# Commander — Verb Catalog Editor (LLM-only world)

Guides the user through a terse interview and produces or patches a voice-commander verb
across three authoring layers:

- **Primitive** — Python function + sidecar TOML + unit test (+ optional prompt edit)
- **Command** — single-graph canonical JSON written to `commands.json` via `GraphStore`
- **Workflow** — multi-step canonical JSON written to `workflows.json` via `GraphStore`

All paths, decorator shape, resolver touchpoints, safety blocklists, and JSON schema
details are hard-coded below. Do not discover anything — this file has the facts.

The repo is post-rework: rapidfuzz no longer routes commands, there are no tool groups,
there is no `phrases_only` / `both` routing mode. A single LLM plans tool chains from a
small verb catalog, with graphs registering as ToolEntry closures. See ADRs 0040–0044,
0062–0068 for the full history. This skill reflects `main` today.

**ADR:** [0068 — Commander skill multi-layer authoring](../../../docs/decisions/0068-commander-skill-multi-layer-authoring.md)

## Trigger

- **Create**: "new voice command", "add a verb", "add tool", "make a primitive", "build a workflow", "make a command"
- **Edit**: "change the description for X", "rename verb X", "edit the action for X",
  "update prompt disambiguation for X"
- **Toggle**: "disable verb X", "enable verb X"
- **Delete**: "remove verb X", "delete tool X"
- **Locate**: "where is the tool for X", "which function handles X"
- "/commander"
- Any request to add, change, or remove an LLM-visible verb in this repo

## Question 0 — Authoring layer (always ask first)

Before anything else, ask:

> "Are you authoring a **primitive** (new Python function + TOML + test), a **command** (single graph in `commands.json`), or a **workflow** (multi-step graph in `workflows.json`)?"

- **Primitive** — continue to the existing **Mode selection** and interview below (unchanged).
- **Command** — jump to **Mode: Create Command** section below.
- **Workflow** — jump to **Mode: Create Workflow** section below.

If the user's message makes the layer obvious (e.g. "add a Python tool for volume up" = primitive; "build a workflow that opens Spotify then types a song name" = workflow), skip the question and confirm your interpretation.

## Mode selection (first question after layer = primitive)

If the user's message does not already pin it down, ask:

> "Are we **creating**, **editing**, **toggling**, or **deleting** a verb?"

Each mode below is self-contained. Skip straight to the matching section.

## Baked-in project facts — do not re-discover

### Paths

| What | Where |
|---|---|
| Tool module (canonical) | `src/voice_commander/tools/primitives.py` |
| Sidecar TOML | `src/voice_commander/tools/primitives.toml` |
| Tests | `tests/unit/test_tools_primitives.py` |
| Resolver (fuzzy param grounding) | `src/voice_commander/resolver.py` |
| Resolver tests | `tests/unit/test_resolver.py` |
| Dispatcher | `src/voice_commander/dispatcher.py` |
| System prompt (LLM surface) | `src/voice_commander/llm_router.py` — `_SYSTEM_PROMPT_TEMPLATE` |
| Config + source logging | `src/voice_commander/config.py` (`LLMConfig`, `log_llm_sources`) |
| Config | `config.toml` (single source of truth, committed) |
| Env-var override prefix | `VC_LLM_<FIELD_UPPER>` |
| Add a dependency | `uv add <pkg>` — NEVER hand-edit `pyproject.toml` |
| Validate registry | `uv run python -m voice_commander --validate` |
| Run verb tests | `uv run pytest tests/unit/test_tools_primitives.py -q` |
| Run resolver tests | `uv run pytest tests/unit/test_resolver.py -q` |
| Commands store | `src/voice_commander/commands/store.py` — `GraphStore` |
| Commands JSON | `commands.json` (user-data dir, seeded from starter pack) |
| Workflows JSON | `workflows.json` (user-data dir, seeded from starter pack) |
| Graph schema | `src/voice_commander/commands/graph_schema.py` — `parse_graph`, `serialise_graph` |
| Graph validator | `src/voice_commander/commands/graph_validator.py` — `validate` |
| Graph registrar | `src/voice_commander/commands/registrar.py` — `reload_all` |

### One home for verbs

All LLM-visible tools live in **one** module: `src/voice_commander/tools/primitives.py`
plus its sidecar `primitives.toml` and test file `test_tools_primitives.py`. There are no
tool groups — `tools/window.py`, `tools/browser.py`, `tools/clipboard.py`, `tools/system.py`,
`tools/mouse.py` were deleted in the ADR-0040 rework.

Exception — a brand-new *domain* with distinct dependencies (e.g. `tools/audio.py` for a
hypothetical audio-device verb) can justify a new module. Default is "add to primitives".
If the user proposes a new module, ask why, and only approve it when the dep set would
pollute primitives.

### Current verb catalog (reference)

Read `primitives.toml` for truth; this table is a cheat sheet.

| LLM-visible name | Python symbol | Params | Self-verify | settle_ms |
|---|---|---|---|---|
| `focus` | `focus` | `target: str` | yes (`_verify_foreground`) | 200 |
| `type` | `type_text` | `text: str` | no | 50 |
| `open` | `open_target` | `target: str` | yes (`_verify_open`, best-effort) | 500 |
| `close` | `close` | — | yes (fg hwnd change poll) | 100 |
| `close_window` | `close_window` | — | yes (fg hwnd change poll) | 100 |
| `minimize` | `minimize` | `target: str \| None = None` | — (ShowWindow is synchronous) | 100 |
| `maximize` | `maximize` | `target: str \| None = None` | — | 100 |
| `press` | `press` | `combo: str` | no | 50 |
| `wait` | `wait` | `ms: int` | — | 0 |
| `click` | `click` | `button: str = "left"` | no | 50 |
| `scroll` | `scroll` | `direction: str, amount: int = 3` | no | 0 |
| `no_match` | `no_match` | `reason: str` | — (router intercepts, body is no-op) | 0 |

`type` and `open` shadow Python builtins — their Python symbols are `type_text` and
`open_target`, and they are registered under the short LLM-visible names via
`@tool(name=...)`. Keep new verbs under plain `@tool` unless the Python name would collide
with a builtin or stdlib symbol.

## `@tool` decorator semantics

Signature lives in `src/voice_commander/registry.py`. Two forms:

```python
# Bare — Python symbol == LLM-visible name. Default for new verbs.
@tool
def minimize(target: str | None = None) -> None: ...

# Named — LLM-visible name differs from the Python symbol.
# Use ONLY when the Python symbol would shadow a builtin / stdlib import.
@tool(name="type")
def type_text(text: str) -> None: ...
```

`@tool(name=...)` is optional kwargs only. Positional form `@tool("type")` does not work —
pass `name=` explicitly.

The decorator registers into the **global registry** at import time. Don't instantiate
your own `ToolRegistry` in `primitives.py` — the daemon constructs one and `discover()`
binds sidecar metadata.

## Sidecar TOML shape (primitives.toml)

One `[tools.<llm-visible-name>]` table per verb. The table key must match the LLM-visible
name (not the Python symbol) — i.e. `[tools.type]` for `@tool(name="type") def type_text`.

```toml
category = "primitives"

[tools.<name>]
phrases = []            # Kept for TOML round-trip back-compat. Never used for routing.
description = "..."     # Prompt surface. Authored carefully. See below.
enabled = true
settle_ms = 100
llm_only = true         # Only valid value on main.

[tools.<name>.args.<param>]
description = "..."     # Prompt surface — the LLM reads this to fill the param.
required = true         # or false (then add `default = ...`)
type = "string"         # or "integer", "boolean", "number"
# default = ...         # only when required = false
```

**Key rules:**

- `llm_only = true` is the **only** routing mode. There is no hybrid, no phrases-only, no
  both. `phrases = []` stays present purely for TOML round-trip; the matcher is gone.
- `settle_ms` governs `time.sleep(settle_ms / 1000)` in `Dispatcher.run_plan()` after the
  step executes. 0 for pure waits / no-ops, 50–100 ms for keystroke verbs, 200–500 ms for
  focus / launch verbs (so the next step lands against a settled window).
- Every arg needs its own `[tools.<name>.args.<arg>]` sub-table with `description`,
  `required`, `type`, and `default` when `required = false`. The registry uses these to
  build the OpenAI-style tools array the LLM sees.

### `description` is prompt surface

The LLM picks a tool by reading its `description`. Write for the model, not for humans:

- State what the tool does in one sentence.
- State **DEFAULTS** explicitly. Example from `primitives.toml`:
  > "Close the current tab/document via Ctrl+W. **DEFAULT for any bare 'close' utterance.**"
- State **BANS** explicitly. Example:
  > "ALWAYS use this tool for 'minimize' utterances. **NEVER emit press(combo='win+d')**
  > or press(combo='win+m') — those minimize everything."
- State explicit **"use X not Y"** disambiguation when the verb overlaps another verb's
  natural-language surface. See `close` vs `close_window` for the canonical example.

When you write or edit a description with "DEFAULT / ALWAYS / NEVER" guidance, the LLM
system prompt usually needs a matching rule — see the **System prompt integration**
section below.

## Self-verifying verbs

Any verb that changes foreground, creates a window, closes something, or modifies visible
state should verify its outcome and log a WARNING on failure. Do NOT raise on self-verify
timeout for best-effort verbs — raising halts the plan chain.

Helpers already in `primitives.py`:

- `_verify_foreground(hwnd)` — `focus` delegates to this. Polls `GetForegroundWindow`
  for ~200 ms. Raises `FocusWindowError` on mismatch (focus is strict — wrong foreground
  means keystrokes go to the wrong app).
- `_verify_open(target)` — after `os.startfile`, polls `EnumWindows` for up to 500 ms
  looking for a title whose `WRatio(target, title) >= 60`. Logs INFO on success,
  WARNING on timeout, never raises.
- `_close_with_verify(combo, verb=...)` — snapshots `GetForegroundWindow` pre-hotkey,
  issues the chord, then polls for ~100 ms expecting the fg hwnd to change or the
  previous hwnd to become invalid. WARNING on timeout.
- `_show_window(target, action="minimize"|"maximize")` — delegates to resolver when
  `target` is provided; falls back to `GetForegroundWindow()` when `target is None`.
  ShowWindow is synchronous, so no post-call poll is needed.

For a new window-touching verb, pattern-match on the closest existing helper. Pure
keystroke verbs (`press`, `type`, `click`, `scroll`, `wait`) are intentionally NOT
self-verifying — there is nothing to verify without a round trip to the UI.

## Resolver integration — don't roll your own enumeration

If the verb takes a target that is a human utterance (e.g. "comet", "notepad", "spotify",
"chrome"), route it through `voice_commander.resolver`:

- `resolver.resolve_window(target) -> int` — enumerate visible windows, fuzzy-match
  `target` against `max(WRatio(target, proc_name), WRatio(target, title))`, return the
  hwnd above `focus_fuzzy_threshold`. Raises `FocusWindowError` with top-3 candidates
  on miss.
- `resolver.resolve_app(target) -> str` — URI pass-through, existing-path pass-through,
  else fuzzy-match against Start Menu `.lnk` stems + `shell:AppsFolder` display names
  (cached for daemon lifetime on first call). Returns a launch token. Raises
  `OpenResolveError` with top-3 on miss.

Canonical usage:

```python
# Window verb — focus / minimize(target) / maximize(target)
from .. import resolver
hwnd = resolver.resolve_window(target)  # raises FocusWindowError on miss

# Launch verb — open
token = resolver.resolve_app(target)    # raises OpenResolveError on miss
os.startfile(token)
```

Thresholds come from `LLMConfig.focus_fuzzy_threshold` / `open_fuzzy_threshold` via the
daemon-injected `resolver._config_ref`. Default 70.

**The resolver already filters destructive display names** via
`_DANGEROUS_DISPLAY_PATTERNS` (uninstall / repair / setup / diskmgmt / regedit / gpedit /
services / event viewer / etc.). A new launch-style verb gets that filter for free when
it calls `resolve_app`.

## Four safety blocklists (know all of them)

All live in `primitives.py` and `resolver.py`. When adding a new verb, consider whether
it intersects any of these:

1. **`_LAUNCH_BLOCKLIST`** (`primitives.py`) — basenames rejected by `open`. Applied to
   both the raw user-provided target AND the resolved launch token. Covers interpreters
   (cmd, powershell, wscript, cscript, rundll32), destructive utilities (regedit,
   diskmgmt, diskpart, format, cipher, gpedit, secpol, services, shutdown, taskkill,
   msconfig). Extend if your verb can launch executables.
2. **`_SYSTEM_PATH_RE`** (`primitives.py`) — regex rejecting resolved paths under
   `\System32\`, `\SysWOW64\`, `\WinSxS\`. Catches renamed copies of blocked utilities.
   Already applied post-resolve inside `open`.
3. **`_PRESS_BLOCKLIST`** (`primitives.py`) — set of `frozenset({...})` destructive
   chords rejected by `press` (`shift+delete`, `win+r`). Normalized to sorted lowercase
   token set. Extend if a new chord class is destructive.
4. **`_DANGEROUS_DISPLAY_PATTERNS`** (`resolver.py`) — regex over Start Menu +
   AppsFolder display names; filtered **at cache-build time**, so `resolve_app` never
   even scores destructive entries.

**Rule for new verbs:** any tool that introduces a *new* destructive capability
(file deletion, process kill, registry write, arbitrary shell exec) must require a
`confirm: bool = False` param with a guard that logs + no-ops when False, AND get an
entry in the relevant blocklist. Flag this in the interview (red flags section).

## System prompt integration

The LLM learns the verb catalog from **two** surfaces:

1. **Tools array** — auto-generated from `primitives.toml` via `tool_schema.py` — passed
   in the OpenAI-style `tools=[...]` field of the chat completion. Descriptions and arg
   schemas come from here.
2. **System prompt** — `_SYSTEM_PROMPT_TEMPLATE` in `llm_router.py`. Contains:
   - Principles (prefer precise verbs; bans on window-sweep chords).
   - Disambiguation rules (close vs close_window; "browser" to default_browser).
   - Hard bans (destructive chords / launches).
   - Few-shot examples with `{default_browser}` templated.

### When to edit the system prompt

Edit `_SYSTEM_PROMPT_TEMPLATE` when the new / edited verb introduces any of:

- **Disambiguation vs another verb.** Canonical example: `close` vs `close_window`.
- **Hard ban against a chord that would otherwise look tempting.**
- **A new natural-language surface** the few-shot examples don't already cover.

Do NOT edit the prompt for a verb that is an additive keystroke with no overlap — the
tools array carries the description through on its own.

### How to edit

Find `_SYSTEM_PROMPT_TEMPLATE` in `src/voice_commander/llm_router.py`. It is a
module-level constant, interpolated once in `LLMRouter._build_system_prompt()` with
`{default_browser}`. Any other `{...}` in the string WILL break formatting — keep curly
braces out, or double them (`{{`, `}}`) if you need literal braces.

After editing, run the prompt test suite (`test_llm_router_prompt.py`) and update the
golden file / assertions as needed.

## Config — when the verb needs a tunable

Tools read config via the daemon-injected `resolver._config_ref`. Do not pass config
through the tool signature — the LLM builds calls from the TOML schema, and adding a
config-shaped param breaks the router.

If a new verb needs a tunable (threshold, timeout, default target), add a field to
`LLMConfig` in `src/voice_commander/config.py`:

1. Add the field to the `@dataclass(frozen=True) class LLMConfig:` with a default.
2. The `_resolve_llm_fields()` helper picks it up automatically — env var
   (`VC_LLM_<FIELD_UPPER>`), then `config.toml`, then default.
3. Add a row to committed `config.toml` under `[llm]` so the default is documented.
4. Read it from the resolver or verb via `resolver._config_ref.<field>` (with a
   `hasattr` guard for tests that don't wire config).
5. Log-format validation is automatic: `log_llm_sources(cfg)` emits one INFO line per
   field at daemon startup, with the winning source name. Verify after edit with
   `uv run python -m voice_commander --validate` and check the log.

## Testing patterns

Exemplars live in `tests/unit/test_tools_primitives.py` and `tests/unit/test_resolver.py`.
Mirror their shape.

### Keystroke / mouse / wait verbs

Patch the pyautogui symbol on the primitives module (not the `pyautogui` package), so
imports resolve through the same path the verb uses:

```python
from unittest.mock import patch
from voice_commander.tools.primitives import press

def test_press_splits_on_plus() -> None:
    with patch("voice_commander.tools.primitives.pyautogui.hotkey") as mock_hotkey:
        press("ctrl+shift+t")
    mock_hotkey.assert_called_once_with("ctrl", "shift", "t")
```

Same shape for `pyautogui.write` (`type_text`), `pyautogui.click` (`click`),
`pyautogui.scroll` (`scroll`). For `wait`, patch `primitives.time.sleep`.

### `focus`-style verbs (resolver + win32)

1. Monkeypatch `resolver.resolve_window` on the primitives module.
2. Stub `_do_focus` with a fake that calls the mocked `win32gui` surface you want to
   assert against. This sidesteps the real AttachThreadInput / Alt-tap routine.
3. Stub `_verify_foreground` to return `True` (or `False` for the failure-path test).
4. Use `patch.dict("sys.modules", {"win32gui": mock_win32gui, ...})` to satisfy the
   inline `import win32gui` inside the verb body.

See `test_focus_calls_resolver_and_routes_hwnd` for the full pattern.

### `open`-style verbs

Mock `resolver.resolve_app` to return a fake launch token. Patch `os.startfile` on the
primitives module. Neutralize `_verify_open` to a no-op so the test doesn't block on the
poll loop. See `test_open_uri_shortcut` and `test_open_calls_resolve_app`.

### Resolver-touching tests

Use the `_install_fake_win32` helper in `test_resolver.py`. It installs fake
`win32gui` / `win32process` / `win32api` / `win32con` AND a fake `psutil` (resolver
prefers psutil for proc names — if you mock only the win32 surface, the test hits the
real psutil and flakes). The helper also handles `denied_pids` for simulating permission
denial.

### Arg-bearing verbs — signature assertion

Always include a signature assertion so arg renames fail loudly:

```python
import inspect
from voice_commander.tools import primitives

def test_myverb_signature() -> None:
    sig = inspect.signature(primitives.myverb)
    assert "target" in sig.parameters
    assert sig.parameters["target"].annotation is str
```

### Config-reading verbs

Use `voice_commander.resolver._set_config(LLMConfig(...))` to wire a test-only config
into the resolver; reset via the `_reset_resolver_state` autouse fixture in
`test_resolver.py`.

## Locating an existing verb

`primitives.toml` is small — read it end-to-end before grepping. If you prefer a targeted
search:

```bash
rg -n "^\[tools\.<name>\]|<description fragment>" src/voice_commander/tools/primitives.toml
rg -n "^def <name>|@tool\(name=\"<name>\"\)" src/voice_commander/tools/primitives.py
```

Show the user a one-line confirmation before changing anything: "Found <name> in
primitives.py (Python symbol: <symbol>), settle_ms=<n>, description=<oneline>. Proceed?"

## Mode: Create (primitive)

Ask one question at a time. Propose defaults from prior answers so the user can confirm
with one word.

1. **Intent sentence** — "One sentence the LLM will read as the tool description. State
   the default utterance this tool owns; state any hard bans."
2. **Python symbol** — propose a `snake_case` name. Only diverges from the LLM-visible
   name when it would shadow a builtin.
3. **LLM-visible name** — same as Python symbol unless shadowed. If different, confirm
   and plan `@tool(name="<llm-name>")`.
4. **Parameters** — for each: name, type (`str` / `int` / `bool`), required or optional
   + default. If the value is a human utterance that names a window / app / file, flag
   it for the resolver.
5. **Action mechanism** — keystroke (pyautogui.hotkey / write / click / scroll), window
   op (win32gui.ShowWindow / SetForegroundWindow via `_do_focus`), shell launch
   (os.startfile after `resolve_app`), or other. Only add a new library via `uv add`,
   never hand-edit `pyproject.toml`.
6. **Resolver use** — does the tool take a fuzzy-matched target? Call
   `resolver.resolve_window(target)` or `resolver.resolve_app(target)` — do NOT roll new
   enumeration logic.
7. **Self-verify** — required for any tool that changes foreground, creates a window,
   closes something, or types into an input.
8. **settle_ms** — 0 for no-ops / pure waits; 50–100 ms for keystrokes; 200–500 ms for
   focus / launch.
9. **Disambiguation bans** — any utterance that should NOT map here?
10. **Config fields** — need a tunable? Propose `LLMConfig.<field>` shape; pick a
    default; confirm you'll add a `config.toml` row.
11. **Safety** — does the verb introduce a new destructive capability (delete, kill,
    write, exec)? If yes, require `confirm=True` default-False, and identify which
    blocklist gets a new entry.

Then run the **Create checklist (primitive)** below.

### Create checklist (primitive)

1. **Add dependency if needed**: `uv add <pkg>`. Announce success. Stop on failure.
2. **Edit `primitives.py`** — add the `@tool` (or `@tool(name="...")`) function. Import
   anything lazily at the top of the function body if it is Windows-only.
3. **Edit `primitives.toml`** — add `[tools.<name>]` with `phrases = []`,
   `description`, `enabled = true`, `settle_ms`, `llm_only = true`, plus
   `[tools.<name>.args.<arg>]` sub-tables.
4. **Edit `test_tools_primitives.py`** — one mocked test per behavior you care about.
   Include a signature assertion for arg-bearing verbs.
5. **Edit `_SYSTEM_PROMPT_TEMPLATE`** in `llm_router.py` — ONLY if the verb introduces
   disambiguation, a hard ban, or a new natural-language surface.
6. **Edit `config.py` + `config.toml`** — ONLY if the verb needs a new `LLMConfig` field.
7. **Verify** — shared verification block below.
8. **Post-write validation** — `uv run python -m voice_commander --validate`.
9. **Report** — reporting block below.

## Mode: Create Command

A **command** is a single-purpose graph in `commands.json`: typically a linear or
branching sequence of primitives wired together via data edges. The LLM sees it as a
named tool call.

### Interview for a command

Ask one question at a time:

1. **Name** — `snake_case`, unique across commands and primitives.
2. **Synonyms** — comma-separated alternate phrasings the LLM can use to invoke it.
   Can be empty.
3. **Description** — one sentence. State the default utterance. Write for the LLM.
4. **llm_visible** — should the LLM see this as a top-level tool? Default `true`.
   Set `false` for helper graphs called only from workflows (ADR 0067).
5. **Inputs** (typed, optional) — if the graph needs runtime parameters, list them:
   `name`, `type` (`str`/`int`/`bool`/`float`), `required`, `description`. For a
   zero-input command (fixed sequence), skip.
6. **Nodes** — list each step as `tool_name` + `kwargs` (baked-in values).
   Assign each a short id (e.g. `n1`, `n2`) for edge wiring.
7. **Edges (data wiring)** — which output ports feed which input kwargs?
   For a linear command with no data dependencies, no edges needed.

Then run the **Create checklist (command / workflow)** below.

### Canonical graph JSON shape

```json
{
  "schema_version": 1,
  "graphs": {
    "<name>": {
      "schema_version": 1,
      "name": "<name>",
      "kind": "command",
      "description": "...",
      "synonyms": ["..."],
      "llm_visible": true,
      "enabled": true,
      "inputs": [
        {"name": "query", "type": "str", "required": true, "description": "..."}
      ],
      "outputs": [],
      "nodes": [
        {"id": "n1", "tool": "focus", "kwargs": {"target": "chrome"}},
        {"id": "n2", "tool": "press", "kwargs": {"combo": "ctrl+t"}}
      ],
      "edges": [
        {"src": {"node_id": "n1", "port": "result"}, "dst": {"node_id": "n2", "port": "target"}}
      ]
    }
  }
}
```

`inputs[]` items with `required=true` become mandatory kwargs the LLM must supply.
`edges[]` src/dst `port` values must match actual tool output/input names — run
`--validate` to confirm.

### Create checklist (command / workflow)

1. **Read the current store file** — `commands.json` or `workflows.json`. If absent,
   create it with the minimal `{"schema_version": 1, "graphs": {}}` wrapper.
2. **Validate the node tool names** — each `tool` value must be registered. Run
   `uv run python -m voice_commander --validate` to confirm after writing.
3. **Write the graph entry** into the `"graphs"` object using the canonical shape above.
   Use atomic tmp+rename (write `.json.tmp`, then `os.replace`) if editing programmatically.
4. **Run `uv run python -m voice_commander --validate`** — must exit 0.
5. **No unit test required for the graph itself** — `graph_validator.py` validates
   structure; the runtime integration test covers execution. If the command uses a new
   primitive that has no test, add the primitive test first.
6. **Report** — reporting block below (adapted: "Graph written to `commands.json`").

## Mode: Create Workflow

A **workflow** is a multi-step graph in `workflows.json`: same JSON shape as a command
but `"kind": "workflow"`. Workflows can call other commands/workflows as sub-graphs
(cross-graph references via `tool` field = graph name).

### Interview for a workflow

Same questions as **Create Command** (name, synonyms, description, llm_visible, inputs,
nodes, edges) plus:

- **Inputs (typed, required)** — workflows typically have typed inputs since they
  orchestrate other tools. Collect all input names, types, and descriptions up front.
- **Sub-graph calls** — if a node's `tool` field refers to another command/workflow name
  (not a primitive), confirm the referenced graph exists and is enabled. Cross-graph
  calls are resolved at runtime via `GraphRuntime`'s `lookup` function.

### Create checklist (workflow)

Same as **Create checklist (command / workflow)** above, but write to `workflows.json`
with `"kind": "workflow"`.

## Mode: Edit

Run the Locate steps first. Then ask only the questions the edit requires. Edit matrix:

| Edit | `primitives.py` | `primitives.toml` | `test_tools_primitives.py` | `llm_router.py` | `config.py` + `config.toml` |
|---|---|---|---|---|---|
| Rename Python symbol (LLM name unchanged) | yes | no | yes (import + patch targets) | no | no |
| Rename LLM-visible name | maybe (`@tool(name=...)` if symbol stays) | yes (`[tools.<old>]` to `[tools.<new>]`) | yes | yes (all prompt mentions) | no |
| Change description | no | yes | no (unless docstring kept in sync) | maybe (if disambiguation changed) | no |
| Change action body | yes | no | yes (mocks / assertions) | no | maybe |
| Swap library | yes | no | yes | no | maybe (`uv add`) |
| Add / change arguments | yes (signature) | yes (args sub-tables) | yes (signature assertion + call mocks) | maybe | no |
| Change settle_ms | no | yes | no | no | no |
| Add disambiguation / ban | no | yes (reflect in description) | no | yes | no |
| Add a tunable | maybe (read it) | no | yes | no | yes |

For graph commands/workflows: edit the JSON directly in `commands.json` / `workflows.json`,
then re-run `--validate`.

Always show a unified before/after summary before writing.

### Edit checklist

1. Apply edits per the matrix.
2. If renaming the **LLM-visible name**: grep `_SYSTEM_PROMPT_TEMPLATE` and the
   `llm_router.py` examples for the old name — they use bare-verb mentions (e.g.
   `close_window()`) that will silently rot if skipped.
3. If renaming the **Python symbol**: grep `src/` + `tests/` for the old name. Any
   `patch("voice_commander.tools.primitives.<old>")` strings need updating.
4. Verify — shared block below.
5. Post-write validation — `uv run python -m voice_commander --validate`.
6. Report — reporting block below.

## Mode: Toggle (enable / disable)

**Primitive:** No code change. Flip `enabled` in the sidecar TOML.

1. Locate the `[tools.<name>]` table.
2. `enabled = true` or `enabled = false`.
3. Verify — primitives test file suffices:
   ```bash
   uv run pytest tests/unit/test_tools_primitives.py -q
   ```
4. Report — one line: "<name> now enabled=<bool>; disabled verbs are filtered out of
   the LLM's tools array on next daemon start."

**Command / Workflow:** Flip `"enabled": false` in the graph's JSON entry in
`commands.json` / `workflows.json`. No code change. Run `--validate` to confirm.

Disabled verbs stay registered but `ToolRegistry.all_llm_visible()` skips them, so the
LLM never sees them in the tools array.

## Mode: Delete

Destructive. Confirm the verb name with the user verbatim before touching files.

**Primitive:**

1. Locate.
2. Confirm — "Delete `<name>` from primitives, its test, and any prompt mentions? This
   cannot be undone without git." Wait for explicit yes.
3. **Remove from `primitives.py`** — delete the decorated function. If it has private
   helpers (`_verify_X`, `_show_X`) used only by this verb, delete those too.
4. **Remove from `primitives.toml`** — delete `[tools.<name>]` and any
   `[tools.<name>.args.*]` sub-tables.
5. **Remove from `test_tools_primitives.py`** — delete tests referencing the verb.
   Also delete the symbol from the `test_imports_expose_expected_symbols` list at the
   bottom of the file (it will fail otherwise).
6. **Remove from `_SYSTEM_PROMPT_TEMPLATE`** in `llm_router.py` — any few-shot example,
   principle, or hard-ban line mentioning the verb. Update the prompt golden test.
7. **Remove from `config.py` + `config.toml`** — any `LLMConfig` field only this verb
   used. Update `log_llm_sources` tests.
8. Verify — shared block.
9. Post-write validation — `uv run python -m voice_commander --validate`. Must not list
   the deleted verb.
10. Report.

**Command / Workflow:**

1. Confirm the graph name verbatim.
2. Delete the `"<name>"` key from the `"graphs"` object in `commands.json` / `workflows.json`.
3. Run `uv run python -m voice_commander --validate`.
4. Report.

## Shared verification block

Run, in order. Do not claim done until all green.

```bash
uv run pytest tests/unit/test_tools_primitives.py -q
# When the edit touches the resolver or a resolver-reading verb:
uv run pytest tests/unit/test_resolver.py -q
# When the edit touches the prompt:
uv run pytest tests/unit/test_llm_router_prompt.py -q
# Always:
uv run python -m voice_commander --validate
```

If a test is red, read the failure and fix the code (not the test, unless the test
itself is wrong). If `--validate` fails, the registry and TOML are out of sync — the
most common cause is a Python symbol renamed without updating the `[tools.<name>]`
table, or a `@tool(name=...)` override the TOML doesn't know about.

## Shared reporting block

Four lines max:

- Files changed (paths, relative to repo root).
- Tests passing (count).
- Prompt changed: Y / N (and which rule/example if Y).
- How to try it: "Restart the daemon (uv run voice-commander), press Scroll Lock, say
  '<example utterance>'. The LLM should emit <expected tool chain>." For delete, skip
  the utterance line.

## Red flags — stop and ask

- **Writes, deletes, or moves files** outside a clearly scoped, user-provided path —
  demand a `confirm: bool = False` param. Document blast radius in the description.
  Consider whether the filename belongs in a blocklist.
- **Network traffic to a non-local endpoint** — demand `confirm: bool = False` and URL
  logging at INFO. The LLM prompt needs a rule so it doesn't emit the call for
  ambiguous utterances.
- **Rename of a Python symbol that's imported elsewhere in `src/`** — grep the whole
  source tree before renaming. The primitives module is intentionally self-contained,
  but private helpers occasionally get referenced from tests.
- **New destructive capability** (delete_file, kill_process, write_registry,
  arbitrary_exec) — require `confirm=True` default-False; AND add to the relevant
  blocklist (`_LAUNCH_BLOCKLIST` / `_PRESS_BLOCKLIST` / a new verb-specific blocklist).
  Update the system prompt with a hard ban.
- **Verb signature that can't express via JSON schema** (nested objects, unions beyond
  `str | None`, callables) — flatten. The OpenAI tools array doesn't carry complex
  shapes cleanly across LM Studio backends.
- **Description collides with an existing verb's natural-language surface** (e.g. a new
  "close something" verb, or a new "open X" variant) — design the disambiguation rule
  BEFORE writing code; update `_SYSTEM_PROMPT_TEMPLATE` in the same PR.
- **Verb takes a human utterance but doesn't call the resolver** — redirect to
  `resolver.resolve_window` / `resolver.resolve_app`. Rolling a new EnumWindows / Start
  Menu scan is almost always a mistake (misses the `_DANGEROUS_DISPLAY_PATTERNS` filter,
  misses threshold config, bypasses caching).
- **Graph node references an unregistered tool name** — run `--validate` before claiming
  the graph is complete. Unknown tool names are validation rule 1.

## Why this skill is shaped like this

- Hard-coded paths and decorator shape mean zero filesystem discovery per invocation.
  The verb home is one file pair, not a tree.
- Question 0 (layer selection) separates the primitive interview from the graph JSON
  authoring flow, keeping both short and focused.
- Mode selection up front keeps the interview short — Toggle should not ask nine
  questions.
- Resolver + safety-blocklist rules catch the two realistic "new destructive verb"
  traps (launching cmd.exe, sweep-window chords) before code is written.
- The "edit prompt when disambiguation changes" rule closes the loop between
  `primitives.toml` description authoring and the prose surface the LLM reads at
  inference time — the two always drift otherwise.
- Config + `log_llm_sources` ensures no silent default ever lands on a user's machine
  without a visible log line at startup. A new tunable without a `config.toml` row is
  a bug.
- Graph commands/workflows write directly to `commands.json` / `workflows.json` so the
  hot-reload path (`reload_all`) picks them up on the next daemon restart without any
  web-UI dependency (ADR 0068).
