---
name: commander
description: Interactive skill for creating, editing, renaming, moving, disabling, or deleting voice-commander tools in this repo. Walks the user through an interview, then writes or patches the Python function, sidecar TOML metadata, and unit test, and verifies with pytest. Use whenever the user says “new voice command”, “add voice command”, “create a voice tool”, “edit voice command”, “change a phrase”, “rename command”, “disable command”, “delete command”, “move command to a different group”, “extend the voice commander”, “/commander”, or otherwise wants to wire up, modify, or remove a spoken-phrase action in this repo. Invoke even when the user does not name the skill — any request to add, change, or remove a phrase-triggered action here should trigger it.
---

# Commander — Voice Tool Creator & Editor

Guides the user through an interactive interview and produces or patches a voice-commander tool (Python function + sidecar TOML + unit test) with all paths, patterns, and library choices hard-coded. Do not discover anything; this file has the facts.

## Trigger

- **Create**: “new voice command”, “add voice command”, “make a voice tool”
- **Edit**: “change the phrases for X”, “rename command”, “move X to a different group”, “edit the action for X”, “update description for X”
- **Toggle**: “disable command X”, “enable command X”, “turn off X”
- **Delete**: “remove command X”, “delete voice tool X”
- **Locate**: “where is the command for X”, “which file holds X”
- “/commander”
- Any request to add, change, or remove a phrase-triggered action in this repo

## Mode selection (first question after trigger)

If the user’s opening message does not already pin it down, ask:

> “Are we **creating**, **editing**, **toggling enabled/disabled**, or **deleting** a command?”

Each mode below is self-contained. Skip straight to the matching section.

## Baked-in project facts — do not re-discover

### Paths

| What | Where |
|---|---|
| Tool modules | `src/voice_commander/tools/<group>.py` |
| Sidecar TOML | `src/voice_commander/tools/<group>.toml` |
| Unit tests | `tests/unit/test_tools_<group>.py` |
| Dependencies | `pyproject.toml` — add via `uv add <pkg>`, never hand-edit |
| Registry pair test | `tests/unit/test_discover_pairing.py` (validates Python ↔ TOML sync) |

### Existing tool groups

- `clipboard` — copy, paste, cut, select_all
- `window` — focus_browser, focus_terminal, minimize, maximize
- `browser` — new_tab, close_tab, reopen_tab, reload
- `system` — lock_screen, take_screenshot

User either appends to one of these or creates a new group.

### Libraries already installed

| Library | Use for |
|---|---|
| `pyautogui` | Keystrokes, hotkeys, mouse. `pyautogui.hotkey("ctrl", "c")`. First choice for keyboard shortcuts. |
| `psutil` | Process enumeration, PID lookup. |
| `pywin32` | `win32gui`, `win32con`, `win32process`, `win32api` — Windows-specific window / focus / shell ops. |
| `subprocess` (stdlib) | Launch external binaries. |
| `webbrowser` (stdlib) | Open URLs in the default browser. |

If the action needs something else, run `uv add <pkg>` and announce it before writing code. Never edit `pyproject.toml` by hand.

### Function pattern (exact)

Bare `@tool` decorator. Returns `None`. Docstring optional — first sentence is a useful fallback description. Phrases do **not** live in the decorator; they live in the sidecar TOML.

**Argument-free tool** (phrases_only routing):

```python
from __future__ import annotations

import pyautogui

from ..registry import tool


@tool
def copy() -> None:
    """Sends Ctrl+C to copy the current selection."""
    pyautogui.hotkey("ctrl", "c")
```

**Argument-bearing tool** (llm_only or both routing):

```python
from __future__ import annotations

import subprocess

from ..registry import tool


@tool
def open_app(app_name: str) -> None:
    """Launches an application by name."""
    subprocess.Popen(["start", app_name], shell=True)
```

Typed parameters appear directly in the function signature. Use Python built-in types (`str`, `int`, `float`, `bool`) or `Literal[...]` from `typing` for enumerated choices. Keep parameters minimal — the LLM router fills them from the utterance; the user never types them.

### Sidecar TOML pattern (exact)

Top-level `category = "<group>"`, then one `[tools.<fn_name>]` table per function. Table key must match the Python function name character-for-character. Phrases lowercase, short, natural.

**Argument-free tool (phrases_only)**:

```toml
category = "clipboard"

[tools.copy]
phrases = ["copy", "copy that", "yank"]
description = "Copy selected text to clipboard."
enabled = true
settle_ms = 0
llm_only = false
```

**Argument-bearing tool (llm_only)**:

```toml
category = "system"

[tools.open_app]
phrases = []
description = "Launches an application by name."
enabled = true
settle_ms = 300
llm_only = true

[tools.open_app.args.app_name]
type = "str"
description = "The name or path of the application to launch."
required = true
```

**Tool registered for both rapidfuzz phrases AND LLM routing**:

```toml
category = "browser"

[tools.navigate_to]
phrases = ["go to", "open site", "navigate to"]
description = "Opens a URL in the default browser."
enabled = true
settle_ms = 0
llm_only = false

[tools.navigate_to.args.url]
type = "str"
description = "The URL to open."
required = true
```

**Key rules:**
- `llm_only = true` → set `phrases = []`. The rapidfuzz matcher ignores it; the LLM router may invoke it from natural utterances.
- `settle_ms` — milliseconds to wait after execution before the daemon accepts the next command. Use 0 for instant keyboard shortcuts; 200–500 for focus/launch actions.
- `[tools.<fn>.args.<param>]` sub-tables mirror the Python signature. Each entry must have `type`, `description`, and `required`. Optional args also include `default`.
- The registry pairs Python ↔ TOML by function name at daemon startup. A function with no TOML entry (or a TOML entry with no function) raises at startup. Keep them in sync on every edit.

### Test pattern (exact)

One test per tool function. Mock the external side-effect so tests stay hermetic. For argument-bearing tools, also assert the function signature matches expectations.

**Argument-free tool**:

```python
from unittest.mock import patch

from voice_commander.tools import clipboard


def test_copy_sends_ctrl_c():
    with patch("voice_commander.tools.clipboard.pyautogui.hotkey") as hk:
        clipboard.copy()
    hk.assert_called_once_with("ctrl", "c")
```

**Argument-bearing tool**:

```python
import inspect
from unittest.mock import patch

from voice_commander.tools import system


def test_open_app_signature():
    sig = inspect.signature(system.open_app)
    assert "app_name" in sig.parameters
    assert sig.parameters["app_name"].annotation is str


def test_open_app_launches_process():
    with patch("voice_commander.tools.system.subprocess.Popen") as popen:
        system.open_app("notepad")
    popen.assert_called_once_with(["start", "notepad"], shell=True)
```

For subprocess-based tools, patch `subprocess.Popen` / `subprocess.run`. For `webbrowser`, patch `webbrowser.open`. For `pywin32`, patch the specific `win32gui.*` call.

## Locating an existing command

Whenever a mode needs to find a tool the user named (by function name, phrase, or rough description), use this deterministic lookup — do not guess.

1. **Grep the sidecar TOMLs** for a phrase or the function name:
   ```
   rg -n "^\[tools\.<fn>\]|"<phrase>"" src/voice_commander/tools/*.toml
   ```
   The file that matches tells you the group. The `[tools.<fn>]` table name is the Python function name.

2. **Open the matching `.py`** (`src/voice_commander/tools/<group>.py`) to see the current implementation.

3. **Open `tests/unit/test_tools_<group>.py`** to see the current test(s) for that function.

4. **Show the user a one-line confirmation** before changing anything: `"Found <fn> in <group>.py, phrases = [...]. Proceed?"`

If the user only gives a vague description (“the clipboard one that copies”), list every `[tools.*]` table in the most likely TOML and ask them to pick.

## Mode: Create

Ask one question at a time. Stay terse. Propose defaults from prior answers so the user can confirm with one word.

1. **Describe the command** — “What should it do? Plain language.”
2. **Phrases** — “Which spoken phrases? Give 2–5 natural variants. (Skip if LLM-only.)”
3. **Function name** — propose a `snake_case` name from the description. Confirm.
4. **Group** — “Which group: `clipboard`, `window`, `browser`, `system`, or a new one?” If new, ask for a short name.
5. **Action mechanism** — “How does it fire? Keystroke / launch app / URL / subprocess / win32 call / other?”
6. **Library** — pick from the installed set. If a new one is needed, name it and confirm `uv add <pkg>` before writing code.
7. **One targeted edge-case question** based on mechanism:
   - Keystroke → “Modifier variants? Need a `time.sleep` between keys?”
   - Launch → “Full path or rely on `PATH`? Detach from daemon (`subprocess.Popen` without `.wait()`)?”
   - URL → “Which URL? New tab or reuse?”
   - Subprocess → “Check return code? Timeout?”
   - win32 → “Which window-class / exe name to target?”
8. **Arguments** — “Does this tool take arguments? For each argument provide: name, type (`str` / `int` / `float` / `bool` / `Literal[...]`), description, required or optional, and default value (if optional).” If none, record as argument-free.
9. **Settle delay** — “Does this tool need a settle delay after execution? (e.g., focus and launch tools typically need ~200–500 ms before the next action is safe.) Enter the delay in milliseconds, or 0 for none.”
10. **Routing mode** — “Is this tool LLM-only (triggered by natural-language intent, no fixed voice phrase), rapidfuzz-only (phrase-triggered, no argument parsing), or both?” — `llm_only`, `phrases_only`, or `both`.

Then run the **Create checklist** below.

### Create checklist

1. **Add dependency** (only if a new library is required): `uv add <pkg>`. Announce success. Stop on failure and surface the error.
2. **Edit or create `<group>.py`** — add imports + `@tool` function with typed parameters in the signature (omit parameters for argument-free tools). New group: start the file with `from __future__ import annotations`, the library import, and `from ..registry import tool`.
3. **Edit or create `<group>.toml`** — add the `[tools.<fn>]` table with `settle_ms` and `llm_only`. Add `[tools.<fn>.args.<param>]` sub-tables for every argument. New group: start with `category = "<group>"` on line 1. Set `phrases = []` when `llm_only = true`.
4. **Edit or create `tests/unit/test_tools_<group>.py`** — one mocked test per new function. For argument-bearing tools, include a signature validation assertion.
5. **Verify** — run the shared verification block below.
6. **Post-write validation** — run `uv run python -m voice_commander --validate` to confirm the registry loads cleanly with the new tool. Surface any error before reporting done.
7. **Report** — see reporting block below.

## Mode: Edit

First, run the **Locate** steps to fix the target. Then ask which fields are changing. Supported edits and the exact files each one touches:

| Edit | `<group>.py` | `<group>.toml` | `test_tools_<group>.py` | `pyproject.toml` |
|---|---|---|---|---|
| Rename function | yes (def + any internal refs) | yes (rename `[tools.<old>]` → `[tools.<new>]`) | yes (test function + patched module attr) | no |
| Change phrases | no | yes (only the `phrases` list) | no | no |
| Change description | no | yes (only `description`) | no (unless docstring sync required) | no |
| Change category label | no | yes (top-level `category = ...`) | no | no |
| Change action body | yes | no (unless description reflects behaviour) | yes (update mocks + assertions) | maybe (`uv add` if new lib) |
| Swap library | yes (import + call) | no | yes (patch target moves) | yes (`uv add <new>`; leave old unless confirmed unused elsewhere) |
| Move to a different group | yes (delete from old `.py`, add to new `.py`) | yes (delete table from old, add to new) | yes (delete test from old, add to new) | no |
| Add / change arguments | yes (update signature types) | yes (add/update `[tools.<fn>.args.*]` sub-tables) | yes (add signature assertion + update call mocks) | no |
| Change settle_ms | no | yes (only `settle_ms`) | no | no |
| Change llm_only / routing mode | no | yes (`llm_only`, and `phrases` if switching to/from `[]`) | no | no |

Interview questions for Edit are the minimal set needed for the selected edit — skip anything unchanged. Always show a unified before/after summary before writing, so the user can abort cheaply.

### Edit checklist

1. **Apply the edits** to the files listed in the table above.
2. **If renaming a function**, also update every `patch("voice_commander.tools.<group>.<oldname>...")` string in the test module and any import elsewhere that names the function (grep first).
3. **If moving to a different group**, ensure the old `.toml` does not end up with an orphan entry and the old `.py` does not export a dangling function. Delete the relevant lines from the old module/test.
4. **Verify** — shared verification block below.
5. **Post-write validation** — run `uv run python -m voice_commander --validate`.
6. **Report** — reporting block below.

## Mode: Toggle (enable / disable)

No code change. Flip the `enabled` flag in the sidecar TOML for the target function.

1. Locate the `[tools.<fn>]` table (see Locate steps).
2. Edit `enabled = true` ↔ `enabled = false`.
3. Verify — just the pairing test (the unit test does not care about `enabled`):
   ```
   uv run pytest tests/unit/test_discover_pairing.py -q
   ```
4. Report — one line: `"<fn> now enabled=<bool>; restart daemon to pick up."`

Disabled tools stay in the registry but are skipped by the matcher, so phrases will not trigger until re-enabled.

## Mode: Delete

Remove a tool entirely. Destructive. Confirm the function name and group with the user verbatim before touching files.

1. **Locate** (see Locate steps).
2. **Confirm** — “Delete `<fn>` from `<group>` and remove its test? This cannot be undone without git.” Wait for explicit yes.
3. **Remove from `<group>.py`** — delete the `@tool`-decorated function. If that was the last tool in the file, also delete the file itself and its sidecar TOML (the registry pairing test will fail otherwise).
4. **Remove from `<group>.toml`** — delete the `[tools.<fn>]` table and any `[tools.<fn>.args.*]` sub-tables beneath it. If the file is now empty of tool tables but still has `category = ...`, leave the file as-is (harmless) or delete it if the `.py` was also deleted.
5. **Remove from `tests/unit/test_tools_<group>.py`** — delete the test(s) for that function.
6. **Verify** — shared verification block below.
7. **Report** — `"Deleted <fn> from <group>; N tests remain; pairing test green."`

## Shared verification block

Run both, in order. Do not claim done until both are green.

```
uv run pytest tests/unit/test_tools_<group>.py -q
uv run pytest tests/unit/test_discover_pairing.py -q
```

If the group test is red, read the failure and fix the code (not the test, unless the test itself is wrong). If the pairing test is red, a TOML table name no longer matches a Python function name — reconcile the two.

For cross-group moves, run the test for both groups.

## Shared reporting block

4 lines max:

- Files changed (paths, relative to repo root)
- Tests passing (count)
- Phrases registered or removed (comma-separated); note `llm_only` tools have no phrases
- How to try it: `"Restart the daemon (uv run voice-commander), press Scroll Lock, say <phrase>."` For LLM-only tools: `"Restart the daemon; invoke via natural utterance (e.g., ‘<example utterance>’)."` For delete/disable, skip the phrase line.

## Red flags — stop and ask

- **Command writes, deletes, or moves files** outside a clearly scoped path: confirm blast radius and target directory.
- **Command sends network traffic** to a non-local endpoint: confirm URL, auth, and purpose.
- **Edit would rename a function used elsewhere in `src/`**: grep for the old name across the whole source tree before renaming. If it is imported from anywhere other than the registry, surface that and ask how to handle it.
- **Delete would leave a phrase collision** (another tool already owns a similar phrase): mention it so the user can decide whether to reassign phrases.
- **Argument-bearing tool with `llm_only = false` and no `phrases`**: the rapidfuzz matcher will never reach it and the LLM router will not reach it either — confirm routing intent before writing.

## Code-gen examples

### Argument-free tool (rapidfuzz phrases_only)

```python
# src/voice_commander/tools/clipboard.py
@tool
def copy() -> None:
    """Sends Ctrl+C to copy the current selection."""
    pyautogui.hotkey("ctrl", "c")
```

```toml
# src/voice_commander/tools/clipboard.toml
[tools.copy]
phrases = ["copy", "copy that", "yank"]
description = "Copy selected text to clipboard."
enabled = true
settle_ms = 0
llm_only = false
```

### Argument-bearing tool (LLM-only)

```python
# src/voice_commander/tools/window.py
@tool
def focus_app(app_name: str) -> None:
    """Brings the named application window to the foreground."""
    import win32gui, win32con
    def callback(hwnd, extra):
        if app_name.lower() in win32gui.GetWindowText(hwnd).lower():
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
    win32gui.EnumWindows(callback, None)
```

```toml
# src/voice_commander/tools/window.toml
[tools.focus_app]
phrases = []
description = "Brings the named application window to the foreground."
enabled = true
settle_ms = 250
llm_only = true

[tools.focus_app.args.app_name]
type = "str"
description = "Partial window title of the application to focus."
required = true
```

```python
# tests/unit/test_tools_window.py
import inspect
from unittest.mock import MagicMock, patch

from voice_commander.tools import window


def test_focus_app_signature():
    sig = inspect.signature(window.focus_app)
    assert "app_name" in sig.parameters
    assert sig.parameters["app_name"].annotation is str


def test_focus_app_calls_enum_windows():
    with patch("voice_commander.tools.window.win32gui") as mock_gui:
        mock_gui.GetWindowText.return_value = "Notepad"
        window.focus_app("notepad")
    mock_gui.EnumWindows.assert_called_once()
```

### Tool registered for both rapidfuzz AND LLM routing

```toml
[tools.navigate_to]
phrases = ["go to", "open site", "navigate to"]
description = "Opens a URL in the default browser."
enabled = true
settle_ms = 0
llm_only = false

[tools.navigate_to.args.url]
type = "str"
description = "The URL to open."
required = true
```

When `llm_only = false` and `phrases` is non-empty, the rapidfuzz matcher can fire the tool phrase-first (without arguments), while the LLM router can also fill `url` from a natural utterance like “navigate to github dot com”.

## Why the shape of this skill

- Hard-coded paths, decorator shape, and sidecar schema mean zero filesystem discovery per invocation.
- Mode selection up front keeps the interview short — disable should not ask seven questions.
- The Locate step is the one piece of search the skill must do, and it is deterministic (`rg` on sidecar TOMLs), so it does not drift.
- Two verification steps (group unit test + pairing test) catch the two realistic failure modes: broken call and Python ↔ TOML drift.
- The post-write `--validate` step catches registry-level mismatches (missing arg sub-tables, type mismatches) that the unit tests cannot see.
- The red-flag list reflects the real traps users fall into (network actions, cross-module renames, orphaned routing config) — catching those before writing code avoids rework.
- `llm_only = true` tools use `phrases = []` so the rapidfuzz matcher never wastes cycles on them. The LLM router reads the TOML schema to know what arguments to extract.
