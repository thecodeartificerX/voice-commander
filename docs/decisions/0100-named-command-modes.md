# ADR 0100 — Named command modes (scoped, voice-switchable catalogs)

**Status:** Accepted
**Date:** 2026-05-23

## Context

Voice Commander's global command registry is flat: every word the user says is
matched against every registered command and primitive in one shared namespace.
This is fine for a small catalog, but a domain-specific workflow — video
editing, slide authoring, a CAD tool — needs dozens of context-specific
shortcuts (split, ripple-delete, undo, redo, nudge-left, …) that would bloat
the global registry and, worse, risk colliding with everyday commands. Users
would also have to memorise which short phrase maps to which shortcut, which is
exactly what Voice Commander is trying to avoid.

What's needed is a way to define **scoped, named catalogs** that activate on
demand and suppress the global command namespace while active, so the user can
say natural, domain-specific words ("split", "ripple", "undo") that map to
the right keystrokes without those words interfering with normal operation.

## Decision

### Catalog format

Each mode is a single TOML file under `modes/` (configurable via
`[modes] dir`, default `"modes"`). The filename stem is the default trigger
word. The `[mode]` table (all fields optional) overrides:

```toml
[mode]
trigger    = "video"          # spoken word to enter the mode (default: stem)
end_phrase = "video end"      # spoken phrase to exit (default: "<trigger> end")
badge      = "🎬 VIDEO"      # text displayed on the sprite badge (default: TRIGGER)

[[command]]
phrases = ["split", "blade", "razor", "cut clip"]   # spoken synonyms
action  = "press ctrl+b"                             # base-primitives transcript

[[command]]
phrases = ["undo"]
action  = "press ctrl+z"
```

### Action compilation

Each `action` string is compiled **at load time** through a
**base-primitives `VerbRouter`** — built from `build_default_rules()` with
no command registry and no picker registry — into a cached `Plan` of
primitive `ToolCall`s. This means a mode action can only be a primitive
(`press`, `type`, `open`, `focus`, `click`, `scroll`, `wait`), a primitive
`chain`, or a repeat-count variant of these. It cannot be a user-authored
command or workflow, and it cannot be a synthetic intercept step. Actions that
fail to route, or that resolve to a synthetic intercept (`__dictation.start`,
`__picker.open`), are rejected at load time with a `ModeLoadError`.

### Routing while in a mode (ModeRouter)

`ModeRouter` wraps one `ModeDefinition` and a base-primitives `VerbRouter`.
For each utterance:

1. Normalize the transcript (lowercase, punctuation-stripped). Phrase keys
   with underscores are pre-converted to spaces at build time so they match
   spoken words.
2. Match against the mode's phrase→Plan map, longest token-count first, exact.
3. If no phrase matches, fall through to the base-primitives router (so raw
   primitives, chain, and repeat-count still work in-mode).
4. If the fallthrough resolves to a synthetic intercept step (name starts with
   `__`), treat as a miss — synthetic intercepts (bare dictate / picker) are
   not available inside a mode.
5. `None` result → miss, stays in the mode, chimes once.

Normal user commands and workflows are **not** available while a mode is active.

### Lifecycle (ModeSession)

A mode is a **sub-state of a running voice session**:

- **Enter:** in normal mode, any utterance that exactly matches a registered
  mode trigger calls `ModeSession.try_enter`. The trigger check is
  first-come, first-served; you cannot enter a second mode while one is
  active (**must-end-first** rule). On entry, `mode.enter {"name", "badge"}`
  is published on the EventBus.
- **Exit:** saying the mode's end phrase (exact match, normalized) exits the
  mode. `mode.exit {"name", reason:"end_phrase"}` is published.
- **Scroll Lock close:** force-resets the mode via `ModeSession.reset()`,
  publishing `mode.exit {"reason":"session_ended"}`. The session-close logic
  calls `reset()` before publishing `session_stopped`.
- **Primitive fallthrough:** while in a mode, the base-primitives router
  handles utterances that don't match any mode phrase — so `press ctrl+z`
  still works. Chain and repeat-count are also available via the same path.

### Feedback

- **Entering a mode** plays the recording-START chime (same asset reused;
  `FeedbackSink.on_mode_enter()` → `_play(self._start)`).
- **Exiting a mode** plays the recording-STOP chime (`on_mode_exit()` →
  `_play(self._stop)`).
- The sprite shows a **persistent top-centre green badge** (`(120, 220, 140)`
  RGB) displaying the mode's `badge` text while the mode is active. The badge
  is cleared on `mode.exit`. Bottom-centre badges (DICTATING / PROCESSING /
  CANCELLED) are unaffected and can coexist.

### Discovery and hot-reload

`ModeRegistry` globs `modes/*.toml` at startup in sorted order:

- Files that fail TOML parsing or semantic validation are logged as a WARNING
  and skipped — a bad file never prevents the daemon from starting or other
  modes from loading.
- Duplicate trigger keys (after normalization): first sorted file wins, the
  duplicate logs a WARNING and is skipped.

`ModesWatcher` (watchdog `Observer`, 500 ms debounce) watches the modes
directory for `*.toml` creates, modifications, deletes, and renames. Any
change triggers a full `ModeRegistry.reload()` with no daemon restart. While
a mode is active, reload is safe: the `ModeSession` holds a reference to its
captured `ModeRouter` and is unaffected until the mode exits. On reload, the
daemon publishes `modes_reloaded {"count": N}` on the EventBus.

`[modes] enabled = false` disables the subsystem entirely; `ModeSession` is
not created and no watcher is started.

### Shipped starter

`modes/video.toml` — a DaVinci Resolve Edit-page shortcut pack (split,
undo, redo, navigation keys 1/2/3, ripple delete, back/forward, play/pause).
Vendored shortcut reference: `docs/references/davinci-resolve-shortcuts.md`.

### Refinement vs the original spec §2

The spec described "a per-mode `VerbRouter` backed by a per-mode
`ToolRegistry`" populated with `ToolEntry` objects for each mode command.
The **shipped design** does not build per-mode `ToolRegistry`/`ToolEntry`
objects or involve per-mode dispatch. Instead, a mode command is a
`(phrases, compiled primitive Plan)` pair, and plan execution reuses the
**global** registry via the existing `Dispatcher.run_plan()` call — because
a compiled action is already a sequence of primitive `ToolCall`s that are in
the global registry. The isolation goal (suppress user commands while in a
mode) is achieved not by filtering the registry but by having `ModeRouter`
**never** touch the global command registry: the phrase map is consulted
first; the base-primitives fallthrough has no registry at all. This ADR is
the authoritative record of this mechanism.

## Consequences

### In-scope in-mode

- Mode phrases + global primitives (including chain and repeat-count).
- Primitive-level chain (`chain press_ctrl+b press_space`): the same
  chain-token-forbidden-verbs rule from ADR 0085 applies — arg-taking verbs
  (`press`, `type`, `open`, `focus`, `wait`, `tabs`, `chain`) are forbidden
  inside a `chain` invocation, so a mode action `chain` can only sequence
  nullary primitives (e.g. `chain click scroll`).

### Out-of-scope in-mode

- Normal user commands and workflows are suppressed; they do not route.
- Synthetic intercepts (`dictate`, bare `focus`/`open`/`tabs` pickers): the
  ModeRouter rejects them as misses.
- `chain … twice` (repeat-count on a chain): the chain-head intercept wins
  first, and the chain parser rejects the trailing count word — unsupported.

### Operational

- Bad mode files are load-isolated (WARNING, skipped); the daemon never
  crashes on a malformed catalog.
- Hot-reload is daemon-restart-free; the watcher fires after a 500 ms quiet
  window so rapid file-save sequences coalesce into one reload.
- No new LLM-visible primitives; tool catalog unchanged at 11.

## Alternatives rejected

**(a) Scoped-registry filter over the global registry.** Allow mode commands
to register as `ToolEntry` objects in a separate per-mode registry, and at
route time filter the global registry to only this mode's entries + the
primitives. Rejected because it introduces `ToolEntry`/`ToolRegistry`
coupling into every mode reload cycle and makes action validation depend on
registry machinery that doesn't exist at load time.

**(b) Phrase map only, no primitive fallthrough.** In-mode utterances match
only the mode's phrase catalog; anything else is a miss. Rejected because it
silently disables primitives the user may reasonably expect to still work
(`press Escape`, `click`, `wait 1`), making modes more restrictive than
necessary without a clear benefit.

## References

- Spec: `docs/superpowers/specs/named-modes.md`
- Plan: `docs/superpowers/plans/named-modes.md`
- Chain token rules: ADR 0085
- Repeat-count modifier: ADR 0098
- Bare-primitive pickers: ADR 0083
- Dictation sub-state: ADR 0086
- Sprite badge rendering: `src/voice_sprite/window.py`, `src/voice_sprite/__main__.py`
- Visual E2E harness: `scripts/mode_visual_e2e.py`
- Starter catalog: `modes/video.toml`
- DaVinci shortcut reference: `docs/references/davinci-resolve-shortcuts.md`
- Implementation: `src/voice_commander/modes/`
