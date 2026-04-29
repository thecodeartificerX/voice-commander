# Feature: Fix "New command" loads first existing graph + add Back button to Builder

The following plan should be complete, but it's important that you validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types and models. Import from the right files.

## Feature Description

Two related Builder UX bugs:

1. **"+ New command" link opens an existing graph.** Clicking the green
   "+ New command" button on `/page/commands` navigates to
   `/page/builder?kind=command` (no `name` param). The SPA bootstrap
   sees no name in the URL and falls through to
   `discoverFirstGraph()`, which loads the **first command** in the
   palette (typically `new_tab` alphabetically). The user sees the
   `new_tab` graph instead of a fresh, blank command — there is no
   "create" path at all.

2. **No back button in Builder.** Once inside the SPA, the only way
   back to the parent list (`/page/commands` or `/page/workflows`) is
   the browser back button. The toolbar has Save / Prompt / LLM
   toggles but no breadcrumb or back chevron. Users feel trapped.

Both are surfaced in the same toolbar area and share a single PR.

## User Story

As a user authoring voice commands in the Builder
I want clicking "+ New command" to give me a blank graph,
And I want a back button to return to the command list
So that I can author new commands without accidentally editing an existing one
And navigate without relying on the browser back button.

## Problem Statement

- The bootstrap sequence in `App.tsx` cannot distinguish "user came
  here to create a new graph" from "first time loading the SPA". Both
  paths fall through to `discoverFirstGraph()`.
- The Builder toolbar has no navigation affordance back to its parent
  page (`/page/commands` or `/page/workflows`).

## Solution Statement

1. **New-command flow.** Treat
   `/page/builder?kind=command|workflow` *with no `name` param* as an
   explicit "new graph" intent. Skip both the `localStorage` fallback
   and `discoverFirstGraph()`. Initialise `graphStore` with a blank
   draft graph (name = a sensible placeholder, kind matches URL,
   empty `nodes`/`edges` plus the minimum nodes the validator
   requires — typically a singleton `input` and `output` node so the
   first save validates). Add a "name your command" inline input in
   the toolbar where the graph title currently lives, so the user can
   pick a name before saving.

2. **Back button.** Add a `ChevronLeft` icon button at the left edge
   of the toolbar that links to `/page/commands` when
   `graphKind === 'command'` and `/page/workflows` when
   `graphKind === 'workflow'`. Use a plain `<a>` (full-page nav, no
   React Router needed). When the graph is dirty, show a `confirm()`
   prompt before navigating away.

## Feature Metadata

**Feature Type**: Bug Fix (with small UX enhancement for new-graph naming)
**Estimated Complexity**: Low–Medium
**Primary Systems Affected**: `web/builder-ui/` (App.tsx, Toolbar.tsx, graphStore.ts)
**Dependencies**: `lucide-react` (already present), no new packages.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: READ THESE FILES BEFORE IMPLEMENTING

- `web/builder-ui/src/App.tsx` (lines 22–117) — Bootstrap sequence.
  Specifically `readGraphFromUrl()` (22–31), `discoverFirstGraph()`
  (57–80), and the `useEffect` that orchestrates the load (100–117).
  This is the **primary bug site** for #1.
- `web/builder-ui/src/toolbar/Toolbar.tsx` (entire file) — Header bar
  where the back button + name input must live. Existing pattern of
  `<Button size="sm" variant="ghost">` from `@/components/ui/button`
  with `lucide-react` icons.
- `web/builder-ui/src/store/graphStore.ts` (lines 48–81) — Zustand
  store. `load()` calls `apiGetGraph(name)`. Need a new
  `initBlank(kind, name)` action that sets state without an API call.
- `web/builder-ui/src/types/graph.ts` — `Graph`, `GraphKind`,
  `GraphNode`, `GraphEdge` types. Use these verbatim — do not invent
  new shapes.
- `web/builder-ui/src/lib/graphSerialize.ts` — `deserializeGraph` /
  `serializeGraph`. The blank scaffold must round-trip cleanly here.
  **Read this before scaffolding** to confirm what the canonical
  blank looks like.
- `web/builder-ui/src/api/graphs.ts` — `apiGetGraph`, `apiSaveGraph`,
  `apiGetPalette`. `apiSaveGraph(name, canonical)` accepts a
  brand-new name; backend validates schema and writes a new file.
- `src/voice_commander/web/builder.py` (lines 170–210) — `POST
  /graph/{name}` server-side. Accepts new names; rejects mismatch
  between path `{name}` and body `g.name`. Confirms our save flow
  works for new graphs without a separate "create" endpoint.
- `src/voice_commander/web/templates/page_commands.html` (line 10) —
  The "+ New command" button source. Currently
  `href="/page/builder?kind=command"`. Leave this URL shape alone;
  fix the SPA to handle it.
- `src/voice_commander/commands/graph_validator.py` — Validator
  rules. Read this to determine the **minimum valid graph**. If it
  requires at least an output node, the blank scaffold must include
  one. If it requires the graph to have at least one entry/exit, add
  those.
- `src/voice_commander/commands/graph_schema.py` (`CURRENT_SCHEMA_VERSION`)
  — Use this exact version number in the blank scaffold.
- `web/builder-ui/src/canvas/Canvas.tsx` — Skim only; the Canvas
  renders nodes/edges from `graphStore`, so initialising with empty
  arrays should "just work" once the store is populated.

### New Files to Create

None. All changes are edits to existing files.

### Relevant Documentation YOU SHOULD READ THESE BEFORE IMPLEMENTING

- [Zustand store pattern (already in use)](https://zustand-demo.pmnd.rs/)
  - Why: New `initBlank` action follows the same pattern as `load()`
    in `graphStore.ts` — set state, mark not-dirty.
- [lucide-react icon list](https://lucide.dev/icons/)
  - Specific icon: `ChevronLeft`
  - Why: Match existing toolbar icons (`Save`, `Eye`, `EyeOff`,
    `RefreshCw` already imported from lucide-react).
- [React Flow — controlled nodes/edges](https://reactflow.dev/learn/advanced-use/uncontrolled-flow)
  - Why: Confirms empty `nodes: []` and `edges: []` is valid initial
    state — Canvas will render an empty pane.

### Patterns to Follow

**Naming Conventions:**

- File names in `web/builder-ui/src/`: lowercase camelCase for
  single-purpose modules (`graphStore.ts`, `graphSerialize.ts`),
  PascalCase for components (`Toolbar.tsx`, `App.tsx`).
- Zustand actions: lowerCamelCase verbs (`load`, `save`,
  `selectNode`). The new action is `initBlank`.
- URL query params already used: `kind`, `name`. Keep these.

**Error Handling:**

- Existing pattern: `try/catch` around async actions, surface via
  `toast.error()` from `sonner` (see `Toolbar.tsx:14–24`). Use the
  same for any new failure paths.

**Toolbar Button Pattern (copy verbatim):**

```tsx
<Button size="sm" variant="ghost" onClick={...} className="gap-1 text-xs">
  <ChevronLeft className="h-3 w-3" />
  Back
</Button>
```

**State init pattern (copy verbatim from graphStore.load):**

```ts
set({
  graphId: name,
  graphKind: kind,
  graphMeta: { ...blankGraphMeta },
  nodes: [...blankNodes],
  edges: [],
  selectedNodeId: null,
  dirty: false,
  llmVisible: blankGraphMeta.llm_visible,
  runStatusByNodeId: {},
})
```

**Anti-patterns to avoid:**

- Do **not** add React Router. The SPA does not use it; the back
  button is a plain `<a href>`.
- Do **not** create a new `/graph/new` API endpoint. The existing
  `POST /graph/{name}` already accepts new names.
- Do **not** persist the blank draft to `localStorage` until the
  user has saved it once. The `LAST_GRAPH_KEY` should only store
  *real* (saved) graphs.
- Do **not** auto-generate a graph name on the backend. The user
  picks the name in the toolbar input before save.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation

Establish a "blank graph" scaffold and make the store aware of new-graph mode.

**Tasks:**

- Read `graph_validator.py` to determine the minimum valid graph
  shape. Determine whether the validator requires an `input` node, an
  `output` node, or both.
- Define a `makeBlankGraph(kind: GraphKind, name: string): Graph`
  helper in a new section of `graphSerialize.ts` (or inline in
  `graphStore.ts` if it's only used once). Include
  `schema_version: CURRENT_SCHEMA_VERSION_FROM_BACKEND` (mirror the
  Python value — read it from `graph_schema.py`).
- Add a `draft: boolean` flag to `GraphState` so the UI knows the
  graph has never been saved (used to distinguish "Save" enabled
  state and to show placeholder name styling).

### Phase 2: Core Implementation

**Tasks:**

- Add `initBlank(kind: GraphKind, name: string)` action to
  `graphStore.ts`. It sets state to the blank scaffold without
  calling the API. Sets `draft: true`, `dirty: true` (so Save is
  immediately available).
- Modify `App.tsx` bootstrap:
  - Detect "new" intent: URL has `kind=command|workflow` AND no
    `name`. Use a new helper `readNewIntentFromUrl()`.
  - When new intent detected: skip storage + discovery, call
    `initBlank(kind, defaultPlaceholderName)`. Default placeholder
    e.g. `untitled_command_<short-id>` or simply `untitled`. The
    user must change it before save (validator rejects spaces /
    invalid chars — see `graph_schema.py`).
  - Otherwise, keep existing
    `readGraphFromUrl() ?? readGraphFromStorage() ?? discoverFirstGraph()`.
- Modify `Toolbar.tsx`:
  - Replace the static `<span>{graphId ?? '…'}</span>` with an
    inline editable input when `draft === true`. Use a controlled
    `<input>` bound to `graphId` via a new `renameDraft(name)` store
    action.
  - When `draft === false`, render the title as before (read-only
    span).
- Add `renameDraft(name: string)` action to `graphStore.ts`. Updates
  `graphId` and the `name` field in `graphMeta`. Marks `dirty: true`
  but only when `draft === true` (renaming a saved graph is a
  separate, out-of-scope feature — emit a console warn if called on
  a non-draft).
- After a successful save of a draft, set `draft: false` in the
  store's `save()` action and `persistLastGraph(kind, graphId)` so
  reloads come back to the just-saved graph.

### Phase 3: Integration

**Tasks:**

- Add `<Button>` with `ChevronLeft` icon at the left edge of
  `Toolbar.tsx` (before the `<span>` title), wrapped in `<a>` for
  navigation. Compute `href` as
  `graphKind === 'workflow' ? '/page/workflows' : '/page/commands'`.
  Default to `/page/commands` when `graphKind` is null.
- Guard navigation when `dirty === true`: `onClick` checks
  `useGraphStore.getState().dirty` and calls
  `window.confirm('Discard unsaved changes?')`. If confirmed, allow
  navigation; otherwise `e.preventDefault()`.
- Verify the existing Save flow handles draft saves end-to-end:
  user clicks Back → confirm → /page/commands. Or user types name +
  edits canvas + clicks Save → POST `/graph/{name}` → backend
  validates → response 200 → store flips `draft: false` and persists
  in localStorage.

### Phase 4: Testing & Validation

**Tasks:**

- Add Vitest unit tests for the new `graphStore` actions
  (`initBlank`, `renameDraft`, save-clears-draft).
- Add a Vitest test for `readNewIntentFromUrl()` covering: kind+no
  name → new intent, kind+name → existing graph, no params → null.
- Manual end-to-end: build the SPA, restart daemon, open
  `/page/commands`, click "+ New command", verify blank canvas +
  editable name input, verify Back button returns to `/page/commands`.

---

## STEP-BY-STEP TASKS

Execute in order, top to bottom.

### 1. UPDATE `web/builder-ui/src/types/graph.ts`

- **IMPLEMENT**: No type changes required — `Graph` already covers
  the blank scaffold shape. Verify by re-reading the file before
  starting.
- **VALIDATE**: `cd web/builder-ui && pnpm tsc --noEmit`

### 2. CREATE blank-graph helper inline in `web/builder-ui/src/store/graphStore.ts`

- **IMPLEMENT**: Add a `makeBlankGraph(kind, name)` private function
  near the top of the file. Returns a `Graph` with:
  - `schema_version`: matches `CURRENT_SCHEMA_VERSION` from backend
    (hardcode the int; add a TODO comment cross-referencing
    `graph_schema.py` so future bumps are caught).
  - `name`: passed-in name.
  - `kind`: passed-in kind.
  - `description`: empty string.
  - `enabled`: `true`.
  - `llm_visible`: `true` for `command`, `false` for `workflow`
    (mirror project default — verify by reading
    `graph_schema.py` defaults; ADR 0067 specifies this).
  - `inputs`: `[]`.
  - `nodes`: minimum required by validator. Default to a singleton
    `input` node + `output` node connected by an `ok` edge if
    validator requires it; otherwise `[]`.
  - `edges`: `[]` or the connecting edge if nodes are scaffolded.
- **PATTERN**: Mirror the shape produced by `serializeGraph` in
  `web/builder-ui/src/lib/graphSerialize.ts` for a freshly loaded
  graph.
- **GOTCHA**: The validator may reject empty graphs. Read
  `src/voice_commander/commands/graph_validator.py` first; adapt the
  scaffold accordingly.
- **VALIDATE**: `cd web/builder-ui && pnpm tsc --noEmit`

### 3. UPDATE `web/builder-ui/src/store/graphStore.ts`

- **IMPLEMENT**:
  - Add `draft: boolean` to `GraphState` (default `false`).
  - Add `initBlank(kind: GraphKind, name: string): void` action.
    Sets state via `set({ ... })` to the blank scaffold; sets
    `draft: true`, `dirty: false` (no edits yet — Save will be gated
    on user interaction). On reflection, prefer `dirty: false` and
    let the first node-add or rename mark dirty.
  - Add `renameDraft(name: string): void` action. Updates `graphId`
    and `graphMeta.name`. Marks `dirty: true` only when
    `state.draft === true`. No-op + console.warn otherwise.
  - Modify `save()` to set `draft: false` after a successful
    `apiSaveGraph` round-trip.
- **PATTERN**: Mirror existing `load()` action structure
  (graphStore.ts:59–73) for the state shape.
- **IMPORTS**: No new imports.
- **GOTCHA**: `apiSaveGraph(graphId, canonical)` uses `graphId` as
  the URL path; the canonical body's `name` field must match
  (server-side check at `builder.py:182–186`). Renaming a draft must
  update both `graphId` and `graphMeta.name` atomically — already
  handled in `renameDraft`. Do not allow rename after first save —
  guard by `draft` flag.
- **VALIDATE**: `cd web/builder-ui && pnpm tsc --noEmit && pnpm test --run`

### 4. UPDATE `web/builder-ui/src/App.tsx` — new-intent detection

- **IMPLEMENT**:
  - Add `readNewIntentFromUrl(): GraphKind | null` near the existing
    `readGraphFromUrl`. Returns `kind` when `kind=command|workflow`
    is present AND `name` is absent. Otherwise null.
  - In the bootstrap `useEffect`: branch first on the new-intent
    helper. If new intent: call `initBlank(kind, 'untitled')` (or
    a generated unique placeholder). Skip storage + discovery.
  - Pull `initBlank` from `useGraphStore` alongside `loadGraph`.
- **PATTERN**: Mirror `readGraphFromUrl` style and the existing
  bootstrap flow (App.tsx:100–117).
- **IMPORTS**: Existing only.
- **GOTCHA**: Make sure the placeholder name passes the validator's
  name regex (likely `[a-z][a-z0-9_]*`). Read
  `graph_schema.py` to confirm. If `untitled` clashes with an
  existing graph, append a short random suffix —
  `untitled_${crypto.randomUUID().slice(0,4)}`.
- **VALIDATE**: `cd web/builder-ui && pnpm tsc --noEmit`

### 5. UPDATE `web/builder-ui/src/toolbar/Toolbar.tsx` — Back button + draft name input

- **IMPLEMENT**:
  - Import `ChevronLeft` from `lucide-react`.
  - Pull `graphKind`, `draft`, `renameDraft`, `dirty` from
    `useGraphStore`.
  - Render a `<a>`-wrapped `<Button>` at the very start of the
    `<header>` content. `href` =
    `graphKind === 'workflow' ? '/page/workflows' : '/page/commands'`
    (default `/page/commands`).
  - Add an `onClick` guard: when `dirty === true`, call
    `window.confirm('Discard unsaved changes?')`. If user cancels,
    `e.preventDefault()`.
  - Replace the title `<span>` with a controlled `<input>` when
    `draft === true`. Style: same `text-sm font-semibold` plus
    `bg-transparent border-b border-border`. `onChange` →
    `renameDraft(e.target.value)`.
  - Keep the read-only span path for non-draft graphs.
- **PATTERN**: Match existing toolbar `<Button>` style (`size="sm"
  variant="ghost" className="gap-1 text-xs"`).
- **IMPORTS**: Add `ChevronLeft` to existing lucide-react import.
- **GOTCHA**: The `<a>` must be inside the existing flex header so
  the layout doesn't break. Use `className="contents"` on the `<a>`
  if necessary, or wrap the Button content in the anchor.
- **VALIDATE**: `cd web/builder-ui && pnpm tsc --noEmit`

### 6. ADD Vitest tests under `web/builder-ui/src/`

- **IMPLEMENT**:
  - `store/graphStore.test.ts` (or extend existing): test
    `initBlank` sets expected state, `renameDraft` updates name when
    draft, `save()` clears `draft` flag.
  - `App.test.tsx` (or new file `App.bootstrap.test.tsx`): test
    `readNewIntentFromUrl` with various URL shapes. Use jsdom +
    mocked `window.location.search`.
  - `Toolbar.test.tsx`: render the toolbar in draft mode, assert
    name input present + Back link href matches `graphKind`.
- **PATTERN**: Mirror existing tests in `web/builder-ui/src/`. Use
  `@testing-library/react` already in devDeps.
- **IMPORTS**: `vi`, `describe`, `it`, `expect` from vitest;
  `render`, `screen` from `@testing-library/react`.
- **GOTCHA**: Vitest config may already mock `window.location` —
  check `vitest.config.ts` and existing tests before reinventing.
- **VALIDATE**: `cd web/builder-ui && pnpm test --run`

### 7. UPDATE Builder build artefact

- **IMPLEMENT**: Rebuild the SPA so the daemon serves the fix.
- **VALIDATE**:
  `cd web/builder-ui && pnpm build` — verify
  `src/voice_commander/web/static/builder/index.html` updated.

### 8. UPDATE ADR (if a new pattern is introduced)

- **IMPLEMENT**: If we add a `draft` flag to the graph store as a
  durable concept, file a short ADR under `docs/decisions/` (next
  number after 0071) describing the new-graph flow. If it's a pure
  bug fix, skip.
- **PATTERN**: Existing ADRs in `docs/decisions/`.
- **VALIDATE**: ADR row added to
  `docs/agents/technical-decisions.md`.

### 9. MANUAL end-to-end validation

- **IMPLEMENT**:
  - Start the daemon.
  - Open `/page/commands`. Click "+ New command".
  - Verify: blank canvas, editable name input pre-filled with
    `untitled_xxxx`, Save button disabled until first edit.
  - Type a name `my_test_command`, drop a node from palette,
    click Save. Verify success toast, daemon reloads, command
    appears at `/page/commands`.
  - Click Back from inside Builder. Verify return to
    `/page/commands`.
  - Make an edit, click Back, verify confirm prompt fires.
- **VALIDATE**: Manual user confirmation per CLAUDE.md (phased
  delivery — humans validate at every phase boundary).

---

## TESTING STRATEGY

The Builder SPA uses **Vitest 1.x** with `@testing-library/react`
and jsdom. Tests live next to the modules they cover under
`web/builder-ui/src/`.

### Unit Tests

- `graphStore.initBlank` sets `graphId`, `graphKind`, `nodes`,
  `edges`, `dirty`, `draft` correctly.
- `graphStore.renameDraft` updates name when draft, no-ops on saved
  graphs.
- `graphStore.save` clears the `draft` flag after a successful API
  call.
- `readNewIntentFromUrl` returns expected shape for: kind only,
  kind+name, neither, invalid kind.
- Toolbar renders editable name input when `draft === true`.
- Toolbar back-link href reflects `graphKind`.

### Integration Tests

End-to-end is manual (above). No headless integration test exists
for this stack today; do not add one in scope.

### Edge Cases

- User opens `/page/builder?kind=command` directly with a stale
  `LAST_GRAPH_KEY` in localStorage: must take new-intent path, **not**
  fall back to localStorage.
- Validator rejects the blank scaffold: catch the error in
  `apiSaveGraph` and show a toast.
- User types a name colliding with an existing graph name: backend
  returns 422 (overwrite would be silent today — verify by reading
  `store.py`). If overwrite is not blocked, add a precheck — issue a
  GET first or call `/graph/validate` with the new name. Out of
  scope for this PR if not already broken; capture as a follow-up.
- User renames the draft to an empty string: input should keep last
  valid value or block save until non-empty.
- Browser back vs Builder back button: both should work. Test both.

---

## VALIDATION COMMANDS

### Level 1: Syntax & Style

```
cd web/builder-ui && pnpm tsc --noEmit
cd web/builder-ui && pnpm lint
```

### Level 2: Unit Tests

```
cd web/builder-ui && pnpm test --run
```

### Level 3: Integration Tests

(none — manual)

### Level 4: Manual Validation

1. `cd web/builder-ui && pnpm build`
2. `python -m voice_commander` (or `start.ps1`)
3. Open `http://localhost:<port>/page/commands`.
4. Click "+ New command" → verify blank canvas + editable name.
5. Add a node, save, verify it appears in the command list.
6. Re-open the new command, verify it loads correctly (regression
   check: `discoverFirstGraph` path still works for *real* graphs).
7. Click Back from Builder → verify navigation.
8. Make a dirty edit, click Back → verify confirm prompt.

### Level 5: Additional Validation

- `git diff --stat` to confirm scope is contained to
  `web/builder-ui/` and (optionally) one ADR under `docs/decisions/`.

---

## ACCEPTANCE CRITERIA

- [ ] Clicking "+ New command" on `/page/commands` opens a blank
      Builder canvas (no nodes from `new_tab` or any other existing
      graph).
- [ ] The toolbar shows an editable name input pre-filled with a
      placeholder (e.g. `untitled_xxxx`).
- [ ] Saving a draft graph creates a new file in the command store
      and the graph appears at `/page/commands`.
- [ ] After save, the title becomes read-only (no longer a draft).
- [ ] Toolbar has a back chevron button at the left edge.
- [ ] Back button navigates to `/page/commands` for commands and
      `/page/workflows` for workflows.
- [ ] Back button shows a confirm prompt when there are unsaved
      changes.
- [ ] Existing graph open + edit + save flow is unchanged
      (regression check on `discoverFirstGraph` and
      `readGraphFromUrl`).
- [ ] All Vitest tests pass.
- [ ] `pnpm tsc --noEmit` and `pnpm lint` clean.
- [ ] Manual end-to-end completed by human reviewer.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order.
- [ ] Each task validation passed immediately.
- [ ] All validation commands executed successfully.
- [ ] Vitest suite green.
- [ ] No TS or lint errors.
- [ ] Manual testing confirms both bugs are fixed.
- [ ] Acceptance criteria all met.
- [ ] Code reviewed for quality and maintainability.
- [ ] If `draft` becomes a durable concept, ADR filed and
      `docs/agents/technical-decisions.md` updated.

---

## NOTES

- **Why no React Router.** The SPA is a single-pane editor; the only
  "navigation" is back to the parent list. A plain `<a href>` is
  cheaper, matches the rest of the daemon's web UI (htmx + page
  links), and avoids a 30 KB router dep.
- **Why we don't persist the draft to localStorage.** Drafts are
  ephemeral until first save. Persisting an unsaved draft and
  reloading would either confuse the user (they expect their tab to
  pick up where they left off) or surface a half-typed name as the
  next session's "last graph". Keep `LAST_GRAPH_KEY` for saved
  graphs only.
- **Why the back button is in the toolbar (not breadcrumbs).** The
  toolbar is the only persistent header in the SPA. Adding a
  separate breadcrumb row would consume vertical space the canvas
  needs. A single chevron at the left edge is sufficient and matches
  conventions from VS Code's editor tabs and similar tools.
- **Future work (out of scope).** Multi-graph tabs (open multiple
  drafts), explicit "Save As" rename for non-drafts, name collision
  precheck. These can ship in follow-up PRs.

**Confidence score for one-pass success**: 8/10. Main risk is the
exact validator-required minimum graph shape — the implementer must
read `graph_validator.py` before scaffolding. Secondary risk is the
schema_version drift between client and server; mitigated by a TODO
comment on the constant.
