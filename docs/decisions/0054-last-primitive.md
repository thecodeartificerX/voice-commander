# ADR 0054: `last` primitive — Alt+Tab / Ctrl+Tab verb

**Status:** Accepted
**Date:** 2026-04-23
**Amends:** [ADR 0043](0043-nine-verb-primitive-catalog.md)

## Context

ADR 0043 locked a nine-verb catalog as the LLM-visible tool surface. In
practice users issue three closely related utterances that do not fit
cleanly into any existing verb:

- "last", "last window", "previous window", "go back" — return to the
  previously focused application.
- "last tab", "next tab", "switch tab" — cycle to the next tab inside
  the currently focused app (browser, editor, terminal).

The LLM can satisfy both by emitting `press(combo="alt+tab")` and
`press(combo="ctrl+tab")`, but those chords are already on the ban
list for other verbs (e.g. `press(win+d)` → `minimize()`). Allowing
"alt+tab" only for this one intent leaks a dangerous catch-all
keystroke into the model's vocabulary and removes the single-source-
of-truth guarantee ADR 0043 relies on.

## Decision

Add a tenth canonical verb: **`last`**.

| Verb (LLM-visible) | Python symbol | Arguments | Implementation |
|---|---|---|---|
| `last` | `last` | `tab: bool = False` | `tab=False` → `pyautogui.hotkey("alt", "tab")` routed through `_close_with_verify` for foreground-change verify. `tab=True` → `pyautogui.hotkey("ctrl", "tab")` with no self-verify (tab switches stay inside the same process, foreground hwnd does not change). |

Total catalog now: **10 LLM-visible verbs** (`focus`, `type`, `open`,
`close`, `close_window`, `press`, `wait`, `click`, `no_match`,
`last`).

### Hard bans updated

The system prompt in `llm_router.py` grows three new entries in the
"Hard bans" block so the LLM never routes around `last`:

- `press(combo="alt+tab")` → `last()`
- `press(combo="ctrl+tab")` → `last(tab=true)`
- `press(combo="ctrl+shift+tab")` → `last(tab=true)`

The `press` primitive's runtime guards are unchanged — this is a prompt-
level routing constraint, not a defence-in-depth reject. If the model
still emits a banned chord the chord executes; the ban just removes the
"correct" path the model would otherwise pick.

### Few-shot examples

Two examples added to the prompt so the model sees the verb in use:

```
User: "last"
Tools: last()

User: "last tab"
Tools: last(tab=true)
```

## Consequences

**Pros.**

- Covers the three common utterances above without teaching the model a
  bare Alt+Tab chord.
- Semantics stay orthogonal: `close`/`close_window` close the active
  surface; `last` navigates between existing surfaces. No verb overlap.
- `tab=False` reuses the existing `_close_with_verify` helper so
  verification + timeout logging are free.

**Cons.**

- Ten verbs instead of nine. ADR 0043's small-MoE reliability argument
  assumed a minimal catalog; adding a verb nudges closer to the ceiling.
  Mitigation: every remaining verb must clear the same bar (displaces
  a dangerous `press` chord or covers a distinct semantic).
- Prompt prefill grows by one tool definition + two few-shot lines.
  Measured impact at time of writing is < 30 tokens.

## Follow-up

- ADR 0043's verb table needs an amendment row pointing at this ADR.
  Done in the same commit that adds `last`.
- If a future verb joins the catalog, repeat this pattern (separate
  ADR, amendment row in 0043) rather than edit-in-place.
