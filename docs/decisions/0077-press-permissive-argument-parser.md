# ADR 0077 — `press()` Permissive Argument Parser

**Status:** Accepted
**Date:** 2026-05-09
**Supersedes:** none (extends ADR 0043 / ADR 0075's `press(combo)` primitive contract)

## Context

VerbRouter forwards the raw transcript tail verbatim to `press(combo=...)`. Whisper produces variations like "Ctrl V", "Ctrl-C", "control and shift t", "Press CTRL-C." The previous parser split only on `+`, so `combo='Ctrl V'` parsed as a single bogus token `"ctrl v"` that pyautogui silently ignored. Result: every voiced shortcut was a no-op, with no diagnostic. Symptom that surfaced this: voiced "Press Ctrl V" logged `PLAN_COMPLETE` but no key was pressed.

## Decision

`press()` now accepts permissive combos:

- Splits on `+`, `-`, whitespace, commas, and the literal word `and` (case-insensitive).
- Aliases common spellings to pyautogui-compatible keynames: `control→ctrl`, `windows/winkey/windowskey→win`, `option→alt`, `return→enter`, `escape→esc`, `del→delete`, `ins→insert`, `pgup→pageup`, `pgdn→pagedown`, `spacebar→space`.
- Lowercases all tokens.
- Validates against `pyautogui.KEYBOARD_KEYS`. Unknown tokens log a WARNING with the original combo string and the normalized form, then no-op (replaces silent failure).
- Empty combo logs WARNING + no-op.
- Existing destructive-chord blocklist (`shift+delete`, `shift+del`, `win+r`) still applies after normalization.

## Consequences

- Voiced shortcuts work via VerbRouter without per-utterance regex hardening upstream.
- Authoring commands in the Builder is more forgiving (typo `ctrl-shift-c` → fires `ctrl+shift+c`).
- Users get a WARNING when they misspell a key, not a silent no-op.

## Alternatives considered

- Fixing at VerbRouter — rejected; better to make the sink robust. The same combo strings can reach `press()` via LLM plans, hard-coded graphs, or Merlin-mode tool calls.
- Adding `cmd` alias for "command" — rejected; Windows-only project and pyautogui does not accept `cmd` as a valid key on Windows.

## Implementation pointers

- `src/voice_commander/tools/primitives.py` — `_PRESS_SPLIT_RE`, `_PRESS_ALIASES`, `press()` function.
- Tests: `tests/unit/test_tools_primitives.py` — 10 tests covering all delimiters, all aliases, unknown-key WARNING, empty combo, destructive blocklist.
