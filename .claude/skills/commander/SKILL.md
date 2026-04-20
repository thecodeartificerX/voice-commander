---
name: commander
description: Interactive skill for creating, editing, renaming, moving, disabling, or deleting voice-commander tools in this repo. Walks the user through an interview, then writes or patches the Python function, sidecar TOML metadata, and unit test, and verifies with pytest. Use whenever the user says "new voice command", "add voice command", "create a voice tool", "edit voice command", "change a phrase", "rename command", "disable command", "delete command", "move command to a different group", "extend the voice commander", "/commander", or otherwise wants to wire up, modify, or remove a spoken-phrase action in this repo. Invoke even when the user does not name the skill — any request to add, change, or remove a phrase-triggered action here should trigger it.
---

# Commander — Voice Tool Creator & Editor

Guides the user through an interactive interview and produces or patches a voice-commander tool (Python function + sidecar TOML + unit test) with all paths, patterns, and library choices hard-coded. Do not discover anything; this file has the facts.

## Trigger

- **Create**: "new voice command", "add voice command", "make a voice tool"
- **Edit**: "change the phrases for X", "rename command", "move X to a different group", "edit the action for X", "update description for X"
- **Toggle**: "disable command X", "enable command X", "turn off X"
- **Delete**: "remove command X", "delete voice tool X"
- **Locate**: "where is the command for X", "which file holds X"
- "/commander"
- Any request to add, change, or remove a phrase-triggered action in this repo

## Mode selection (first question after trigger)

If the user's opening message doesn't already pin it down, ask:

> "Are we **creating**, **editing**, **toggling enabled/disabled**, or **deleting** a command?"

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

Bare `@tool` decorator. Zero args. Returns `None`. Docstring optional — first sentence is a useful fallback description. Phrases do **not** live in the decorator; they live in the sidecar TOML.

```python
from __future__ import annotations

import pyautogui

from ..registry import tool


@tool
def copy() -> None:
    """Sends Ctrl+C to copy the current selection."""
    pyautogui.hotkey("ctrl", "c")
```

### Sidecar TOML pattern (exact)

Top-level `category = "<group>"`, then one `[tools.<fn_name>]` table per function. Table key must match the Python function name character-for-character. Phrases lowercase, short, natural.

```toml
category = "clipboard"

[tools.copy]
phrases = ["copy", "copy that", "yank"]
description = "Copy selected text to clipboard."
enabled = true
```

The registry pairs Python ↔ TOML by function name at daemon startup. A function with no TOML entry (or a TOML entry with no function) raises at startup. Keep them in sync on every edit.

### Test pattern (exact)

One test per tool function. Mock the external side-effect so tests stay hermetic.

```python
from unittest.mock import patch

from voice_commander.tools import clipboard


def test_copy_sends_ctrl_c():
    with patch("voice_commander.tools.clipboard.pyautogui.hotkey") as hk:
        clipboard.copy()
    hk.assert_called_once_with("ctrl", "c")
```

For subprocess-based tools, patch `subprocess.Popen` / `subprocess.run`. For `webbrowser`, patch `webbrowser.open`. For `pywin32`, patch the specific `win32gui.*` call.

## Locating an existing command

Whenever a mode needs to find a tool the user named (by function name, phrase, or rough description), use this deterministic lookup — do not guess.

1. **Grep the sidecar TOMLs** for a phrase or the function name:
   ```
   rg -n "^\[tools\.<fn>\]|\"<phrase>\"" src/voice_commander/tools/*.toml
   ```
   The file that matches tells you the group. The `[tools.<fn>]` table name is the Python function name.

2. **Open the matching `.py`** (`src/voice_commander/tools/<group>.py`) to see the current implementation.

3. **Open `tests/unit/test_tools_<group>.py`** to see the current test(s) for that function.

4. **Show the user a one-line confirmation** before changing anything: `"Found <fn> in <group>.py, phrases = [...]. Proceed?"`

If the user only gives a vague description ("the clipboard one that copies"), list every `[tools.*]` table in the most likely TOML and ask them to pick.

## Mode: Create

Ask one question at a time. Stay terse. Propose defaults from prior answers so the user can confirm with one word.

1. **Describe the command** — "What should it do? Plain language."
2. **Phrases** — "Which spoken phrases? Give 2–5 natural variants."
3. **Function name** — propose a `snake_case` name from the description. Confirm.
4. **Group** — "Which group: `clipboard`, `window`, `browser`, `system`, or a new one?" If new, ask for a short name.
5. **Action mechanism** — "How does it fire? Keystroke / launch app / URL / subprocess / win32 call / other?"
6. **Library** — pick from the installed set. If a new one is needed, name it and confirm `uv add <pkg>` before writing code.
7. **One targeted edge-case question** based on mechanism:
   - Keystroke → "Modifier variants? Need a `time.sleep` between keys?"
   - Launch → "Full path or rely on `PATH`? Detach from daemon (`subprocess.Popen` without `.wait()`)?"
   - URL → "Which URL? New tab or reuse?"
   - Subprocess → "Check return code? Timeout?"
   - win32 → "Which window-class / exe name to target?"

Then run the **Create checklist** below.

### Create checklist

1. **Add dependency** (only if a new library is required): `uv add <pkg>`. Announce success. Stop on failure and surface the error.
2. **Edit or create `<group>.py`** — add imports + `@tool` function. New group: start the file with `from __future__ import annotations`, the library import, and `from ..registry import tool`.
3. **Edit or create `<group>.toml`** — add the `[tools.<fn>]` table. New group: start with `category = "<group>"` on line 1.
4. **Edit or create `tests/unit/test_tools_<group>.py`** — one mocked test per new function.
5. **Verify** — run the shared verification block below.
6. **Report** — see reporting block below.

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

Interview questions for Edit are the minimal set needed for the selected edit — skip anything unchanged. Always show a unified before/after summary before writing, so the user can abort cheaply.

### Edit checklist

1. **Apply the edits** to the files listed in the table above.
2. **If renaming a function**, also update every `patch("voice_commander.tools.<group>.<oldname>...")` string in the test module and any import elsewhere that names the function (grep first).
3. **If moving to a different group**, ensure the old `.toml` doesn't end up with an orphan entry and the old `.py` doesn't export a dangling function. Delete the relevant lines from the old module/test.
4. **Verify** — shared verification block below.
5. **Report** — reporting block below.

## Mode: Toggle (enable / disable)

No code change. Flip the `enabled` flag in the sidecar TOML for the target function.

1. Locate the `[tools.<fn>]` table (see Locate steps).
2. Edit `enabled = true` ↔ `enabled = false`.
3. Verify — just the pairing test (the unit test doesn't care about `enabled`):
   ```
   uv run pytest tests/unit/test_discover_pairing.py -q
   ```
4. Report — one line: `"<fn> now enabled=<bool>; restart daemon to pick up."`

Disabled tools stay in the registry but are skipped by the matcher, so phrases won't trigger until re-enabled.

## Mode: Delete

Remove a tool entirely. Destructive. Confirm the function name and group with the user verbatim before touching files.

1. **Locate** (see Locate steps).
2. **Confirm** — "Delete `<fn>` from `<group>` and remove its test? This cannot be undone without git." Wait for explicit yes.
3. **Remove from `<group>.py`** — delete the `@tool`-decorated function. If that was the last tool in the file, also delete the file itself and its sidecar TOML (the registry pairing test will fail otherwise).
4. **Remove from `<group>.toml`** — delete the `[tools.<fn>]` table. If the file is now empty of tool tables but still has `category = ...`, leave the file as-is (harmless) or delete it if the `.py` was also deleted.
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
- Phrases registered or removed (comma-separated)
- How to try it: `"Restart the daemon (uv run voice-commander), press Scroll Lock, say <phrase>."` For delete/disable, skip the phrase line.

## Red flags — stop and ask

- **Command needs arguments** ("open <app>", "play <song>"): MVP tools are argument-free. Argument routing is Phase 6 (local LLM). Offer a hard-coded variant ("open readme" → always opens the project README) or defer.
- **Command writes, deletes, or moves files** outside a clearly scoped path: confirm blast radius and target directory.
- **Command sends network traffic** to a non-local endpoint: confirm URL, auth, and purpose.
- **Edit would rename a function used elsewhere in `src/`**: grep for the old name across the whole source tree before renaming. If it's imported from anywhere other than the registry, surface that and ask how to handle it.
- **Delete would leave a phrase collision** (another tool already owns a similar phrase): mention it so the user can decide whether to reassign phrases.

## Why the shape of this skill

- Hard-coded paths, decorator shape, and sidecar schema mean zero filesystem discovery per invocation.
- Mode selection up front keeps the interview short — disable shouldn't ask seven questions.
- The Locate step is the one piece of search the skill must do, and it's deterministic (`rg` on sidecar TOMLs), so it doesn't drift.
- Two verification steps (group unit test + pairing test) catch the two realistic failure modes: broken call and Python ↔ TOML drift.
- The red-flag list reflects the real traps users fall into (argument-bearing asks, cross-module renames, network actions) — catching those before writing code avoids rework.
