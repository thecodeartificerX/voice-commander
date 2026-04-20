---
name: commander
description: Interactive skill for creating new voice-commander tools in this repo. Walks the user through an interview — name, phrases, group, action, library — then writes the Python function, sidecar TOML metadata, and unit test, and verifies with pytest. Use whenever the user says "new voice command", "add voice command", "create a voice tool", "extend the voice commander", "/commander", or otherwise wants to wire up a spoken phrase to a keystroke, launcher, script, or system action. Invoke even when the user does not name the skill — any request to add a phrase-triggered action in this repo should trigger it.
---

# Commander — Voice Tool Creator

Guides the user through an interactive interview and produces a working voice-commander tool (Python function + sidecar TOML + unit test) with all paths, patterns, and library choices hard-coded. Do not discover anything; this file has the facts.

## Trigger

- "new voice command", "add voice command", "make a voice tool"
- "/commander"
- Any request to map a spoken phrase to an action in this repo

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

## Interview flow

Ask one question at a time. Stay terse. Do not dump the whole form. Propose defaults based on prior answers so the user can confirm with one word.

1. **Describe the command** — "What should it do? Plain language."
2. **Phrases** — "Which spoken phrases? Give 2–5 natural variants."
3. **Function name** — propose a `snake_case` name from the description. Confirm.
4. **Group** — "Which group: `clipboard`, `window`, `browser`, `system`, or a new one? (If new, give a short name.)"
5. **Action mechanism** — "How does it fire? Keystroke / launch app / URL / subprocess / win32 call / other?" If unsure, suggest based on the description.
6. **Library** — pick from the installed set. If a new one is needed, name it and confirm `uv add <pkg>` before writing code.
7. **One targeted edge-case question** based on mechanism:
   - Keystroke → "Modifier variants? Need a `time.sleep` between keys?"
   - Launch → "Full path or rely on `PATH`? Detach from daemon (`subprocess.Popen` without `.wait()`)?"
   - URL → "Which URL? New tab or reuse?"
   - Subprocess → "Check return code? Timeout?"
   - win32 → "Which window-class / exe name to target?"

Stop and clarify when requirements conflict. Do not guess.

## Execution checklist (after interview)

Run in this order. Do not skip steps.

1. **Add dependency** (only if a new library is required):
   ```
   uv add <pkg>
   ```
   Announce success before proceeding. If it fails, stop and surface the error.

2. **Edit or create `<group>.py`** — add imports + `@tool` function. If the group is new, create the file with `from __future__ import annotations`, the library import, and `from ..registry import tool`.

3. **Edit or create `<group>.toml`** — add the `[tools.<fn>]` table. If the group is new, start the file with `category = "<group>"` on the first line, then the table.

4. **Edit or create `tests/unit/test_tools_<group>.py`** — add one mocked test per new function. Match the existing style (see clipboard test above).

5. **Verify the new test** — run:
   ```
   uv run pytest tests/unit/test_tools_<group>.py -q
   ```
   Must be green before claiming done. If red, read the failure, fix the code (not the test, unless the test itself is wrong), rerun.

6. **Verify the registry pairing** — run:
   ```
   uv run pytest tests/unit/test_discover_pairing.py -q
   ```
   This catches any Python ↔ TOML mismatch (missing table, typo in function name, extra orphan entry) before the daemon would crash on startup.

7. **Report** to the user in 4 lines max:
   - Files changed (paths)
   - Tests passing (count)
   - Phrases registered
   - How to try it: "Restart the daemon (`uv run voice-commander`), press Scroll Lock, say `<phrase>`."

## Red flags — stop and ask

- **Command needs arguments** ("open <app>", "play <song>"): the MVP tools are argument-free. Argument routing is Phase 6 (local LLM). Offer a hard-coded variant ("open readme" → always opens the project README) or defer.
- **Command writes, deletes, or moves files** outside a clearly scoped path: confirm blast radius and target directory with the user.
- **Command sends network traffic** to a non-local endpoint: confirm URL, auth, and purpose.
- **User wants to disable an existing command**: flip `enabled = false` in the sidecar TOML — no new code or test needed. Still run step 6.
- **User wants to rename an existing phrase**: edit the `phrases` list in the sidecar TOML, rerun step 6. Do not touch Python unless the function name is changing.

## Why the shape of this skill

- Hard-coded paths and patterns mean zero filesystem discovery per invocation — saves tokens and removes a failure mode (missed file, wrong directory).
- One question at a time keeps the interview conversational and lets the user redirect cheaply.
- Two verification steps (unit test + pairing test) catch the two realistic failure modes: broken call and Python ↔ TOML drift.
- The red-flag list exists because users routinely ask for argument-bearing commands that the MVP cannot serve; catching that before writing code saves a rewrite.
