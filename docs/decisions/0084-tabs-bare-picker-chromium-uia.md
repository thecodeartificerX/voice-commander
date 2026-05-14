# ADR 0084 — `tabs` Bare Picker via Chromium UI Automation

**Status:** Accepted
**Date:** 2026-05-14
**Builds on:** ADR 0083 (bare-primitive picker framework)

## Context

Sakib wants a `tabs` verb that, when said bare, opens a numbered modal of
the foreground window's tab titles and switches to the chosen tab when he
says its number. ADR 0083 already shipped the picker framework as a
pluggable extension point: any new bare verb just needs a
`BarePickerProvider` closure and a primitive tool to dispatch on
selection. `tabs` is the second consumer of that framework.

Three implementation questions had to be answered:

1. Which browsers does v1 support?
2. How are tab titles enumerated from a running browser?
3. How is the chosen tab activated?

## Decision

- **D1. Chromium-only via process allowlist.** v1 supports Chromium-derived
  browsers (Chrome, Edge, Brave, Comet, Vivaldi, Opera, Arc, Yandex,
  Chromium itself). Firefox uses a different accessibility model and is
  out of scope. Electron apps (VS Code, Slack, Discord) share the
  Chromium `ClassName` but expose unrelated `TabControl`s and are
  excluded by process-name allowlist (`CHROMIUM_BROWSER_PROCESSES` in
  `tools/tabs_uia.py`).
- **D2. UIA enumeration, no DevTools / debugger ports.** Tabs are read
  by walking the window's UI Automation tree for the page-tab
  `TabControl`. We collect every descendant `TabItemControl` (Chromium
  nests them as `TabControl > Pane > Pane > TabItem`, not as direct
  children — so a direct-child check is too strict; descendant
  containment is the actual invariant). DevTools / `--remote-debugging-port`
  were considered and rejected: they require running the browser with a
  flag the user controls, and we already have UIA working for the focus
  picker.
- **D3. UIA `SelectionItemPattern.Select()` for activation.** Chromium
  `TabItem`s implement `SelectionItemPattern`; calling `Select()` both
  activates the tab and updates `IsSelected`. We fall back to
  `LegacyIAccessiblePattern.DoDefaultAction` for forks that omit
  `SelectionItemPattern`. Keyboard chord (`Ctrl+1..8`) was rejected — it
  caps at 8 tabs.
- **D4. Pre-built `Plan` carries `(browser_hwnd, index, title_hint)`.**
  The picker provider snapshots the browser HWND, tab index, and tab
  title at enumeration time and pre-builds the dispatch `Plan`. On
  selection the daemon dispatches that plan directly — no
  re-enumeration in the hot path. The activator re-resolves by index
  first, falls back to title-equality scan when the strip has shifted
  between picker open and selection.
- **D5. Single-line card layout.** Every row of a `tabs` picker comes
  from the same browser, so repeating "Comet" / "Chrome" on each card
  is noise. The provider puts the page title in the `app` slot (bold
  primary line) and leaves `title` empty, so the modal draws a clean
  single-line card instead of stacking a muted secondary text
  underneath.
- **D6. Empty list ⇒ miss-chime.** When the foreground window is not a
  recognised Chromium browser, or is one but has no resolvable tab
  strip (rare PWA window, hung renderer), the provider returns `[]`.
  The framework miss-chimes on empty without opening an empty modal —
  satisfying the spec's "brings up nothing if the active window doesn't
  have tabs."
- **D7. Bare-only verb rule.** `VerbRule("tabs", ("tabs",))` has neither
  `default_target` nor `raw_tail_tool`, so `tabs <anything>` deliberately
  falls through to `None`. The router only ever routes bare `tabs.` to
  the picker. There is no plausible meaning for a tail argument — page
  titles are too noisy to fuzzy-match on, and the picker numbers are
  the right interface.

## Consequences

- New module: `voice_commander.tools.tabs_uia` — Chromium process
  allowlist, `_find_tab_strip`, `_collect_tab_items`,
  `list_chromium_tabs`, `invoke_chromium_tab`.
- New module: `voice_commander.tools.tabs_picker` — provider closure +
  global registration helper.
- New primitive: `tabs(_browser_hwnd, _tab_index, _tab_title)` in
  `tools/primitives.py`. Marked `internal = true` — picker-only,
  invisible to the LLM and Builder UI.
- New verb rule: bare-only `tabs` in `verb_router.build_default_rules`.
- New config: `PickerTabsConfig(cap=9)` under `picker.tabs`.
- New dep: `uiautomation>=2.0` (pulls `comtypes`).
- New tests: `tests/unit/test_picker_tabs_provider.py`,
  `tests/unit/test_tabs_uia.py`, plus `tabs`-specific cases in
  `tests/unit/test_verb_router.py`.
- New harness: `scripts/picker_tabs_e2e.py` — three phases:
  Phase A renders the sprite modal with `verb="tabs"` via a fake SSE
  server (`PrintWindow` capture + gold-pixel assertion).
  Phase B enumerates the user's running Comet via UIA and asserts the
  enumerator returns ≥ 2 page tabs.
  Phase C calls `tabs(_browser_hwnd, _tab_index, _tab_title)` against a
  non-current tab, polls UIA for the selection flip, then restores the
  originally-active tab so the user's session is left exactly as found.
  All three phases pass.

## Why this isn't a generic "tabs anywhere" framework

The spec mentioned "if I have partial open and partial has active tabs."
Generalising past Chromium would need per-app UIA shape detection
(every tab-bearing app exposes its strip differently — Edge legacy
tabs, IE, Office, Explorer, Slack, etc.) and per-app activation
semantics. v1 targets the one shape Sakib actually uses (Comet, a
Chromium fork). Adding another tab-bearing app means a new provider
or extending `CHROMIUM_BROWSER_PROCESSES`, not a framework rewrite.
