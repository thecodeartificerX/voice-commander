# ADR 0075 — Primitives-only MVP

**Status:** Accepted
**Date:** 2026-05-08
**Supersedes:** ADR 0025 (mute hotkey), ADR 0072 (speak/dictation toggle), ADR 0073 (remote transcription backend) — and reduces ADR 0043 (nine-verb primitive catalog) to seven action verbs + four perception verbs.
**Partially superseded by:** ADR 0076 (registry-aware VerbRouter routing — the primitives-only routing claim no longer holds; VerbRouter now also routes user-authored command/workflow names via registry lookup)

## Context

The catalog and codebase carried a long tail of features that pre-dated the primitives-only MVP direction:
- 9 disabled-but-still-importable legacy verbs (`close`, `close_window`, `minimize`, `maximize`, `last`, `summon_commander`, `mute`, `done`, `ask_user`)
- 4 disabled perception tools (`get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`)
- The speak / dictation toggle subsystem (Right-Ctrl mute key, `[speak]` config block, `_system.speak()` tool)
- Remote `whisper.cpp` transcription backend (unused)
- HUD `Summarizer` rule table (Merlin-deferred → effectively dead path)
- VerbRouter rules covering `copy`/`cut`/`paste`/`undo`/`redo`/`save`/`refresh`/`minimize`/`maximize`/`close`/`new` (semantic shortcuts that the user authors as graphs)

Symptom that surfaced the cleanup: utterance "Click." → MISS, because VerbRouter had no `click` rule.

## Decision

Reduce the LLM- and voice-visible tool surface to:

| Action (7) | Perception (4) |
|---|---|
| `click(button="left")` | `get_active_window_title()` |
| `focus(target)` | `get_cursor_pos()` |
| `open(target)` | `read_clipboard()` |
| `press(combo)` | `ocr_region(x, y, w, h)` |
| `scroll(direction, amount=3)` | |
| `type(text)` | |
| `wait(ms)` | |

Plus `no_match(reason)` — LLM-only escape hatch, hidden from VerbRouter.

VerbRouter rules cover only direct primitive invocation (`click`, `scroll`, `focus X`, `open X`, `type X`, `press X`, `wait N`). Anything semantic (`copy`, `minimize`, `new tab`) is authored by the user as a graph in the Builder UI, dispatching to `press(combo=...)`.

`tail_coerce` on `VerbRule` lets the `wait` rule accept `"500"`, `"500 ms"`, `"500 milliseconds"` and coerce to `int`.

Speak-mode, the Right-Ctrl mute hotkey, RemoteTranscriber, and the HUD summarizer's rule table are all deleted. The HUD now renders raw `tool_fired.name` values directly into the chat log; `plan_outcome` with `status="miss"` becomes the literal string `"no match"`.

## Consequences

- User commands referencing deleted tools fail loud at startup; user re-authors them in the Builder UI.
- Right-Ctrl muscle memory is broken — Scroll Lock is now the sole hotkey.
- The HUD loses pretty rule-driven phrasing (`copied`, `pasted`, `searched <browser> for "..."`); it gains transparency by rendering the raw tool name the dispatcher fired.
- Merlin-mode LLM routing remains intact for open-ended utterances; the small-MoE tool list shrinks accordingly, raising tool-call reliability.

## Alternatives considered

- Migrating user-authored graphs that depended on deleted tools — explicitly rejected ("boil the ocean"; user re-authors).
- Keeping the rule-table summarizer — rejected as dead weight given the absence of an LLM fallback.

## References

- Spec: `docs/superpowers/specs/2026-05-08-primitives-only-mvp-design.md`
- Plan: `docs/superpowers/plans/2026-05-08-primitives-only-mvp.md`
