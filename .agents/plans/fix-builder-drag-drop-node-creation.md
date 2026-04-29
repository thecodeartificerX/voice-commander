# Feature: Fix Builder UI Drag-and-Drop Node Creation

The following plan should be complete, but it's important that you validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils types and models. Import from the right files etc.

## Feature Description

The Builder UI (`/page/builder`) is a React 18 + Vite + React Flow SPA used to author command and workflow graphs visually. Users drag items from the left-hand palette (Commands, Workflows, Pipeline, Control, Perception sections) onto the canvas to add nodes. Currently, **drag-and-drop is completely broken**: dragged palette items never land on the canvas. The same is true for the "New Command" flow, which lists existing commands and expects users to drag them onto a fresh canvas.

This feature wires up React Flow's drop handlers on the canvas, converts screen-space drop coordinates to flow-space, generates a unique node id, derives the correct registered `type` from the dragged `ref` (e.g. `command.greet` → `command`, `control.branch` → `branch`), and pushes the new node into the Zustand graph store. After the fix, dragging any palette item — Commands, Workflows, Pipeline tools, Control, or Perception — adds a properly-typed node at the cursor location, and the graph becomes dirty so it can be saved.

## User Story

As a Voice Commander power user authoring graphs in the Builder UI
I want to drag any palette item onto the canvas and have it become a real, configurable node
So that I can compose commands and workflows visually instead of editing JSON by hand.

## Problem Statement

`web/builder-ui/src/canvas/Canvas.tsx` registers handlers for nodes/edges/connect/click, but never registers `onDrop` or `onDragOver` on the `<ReactFlow>` component. Without `onDragOver` calling `event.preventDefault()`, the browser silently rejects every drop, so `onDrop` would never fire even if it existed. `PaletteItem.tsx` correctly fires `onDragStart` with `dataTransfer.setData('application/vc-palette', JSON.stringify({ ref, kind }))`, but there is nothing on the canvas side to receive that payload, convert coordinates, generate an id, or call `graphStore.setNodes()`. ADR 0071 declared "drag-from-palette preserved" but the React migration (commit 24e0f96) shipped without porting this flow.

## Solution Statement

Add `onDragOver` and `onDrop` handlers to `<ReactFlow>` inside `CanvasInner` (which is already wrapped by `<ReactFlowProvider>`, so `useReactFlow()` is available). On `dragOver`, call `event.preventDefault()` and set `dataTransfer.dropEffect = 'move'`. On `drop`, parse the JSON payload from the `application/vc-palette` MIME type, validate it, project the screen coordinates into flow space via `useReactFlow().screenToFlowPosition()`, derive the registered React Flow `type` from `ref` using the existing `refToNodeType()` helper in `lib/graphSerialize.ts`, generate a unique node id with `crypto.randomUUID()`, and push the new node into the store via `setNodes((current) => [...current, newNode])`. The new node's `data` field is `{ ref, kwargs: {} }`. Mark the new node as selected so the inspector panel opens.

## Feature Metadata

**Feature Type**: Bug Fix
**Estimated Complexity**: Low
**Primary Systems Affected**: Builder UI Canvas (`web/builder-ui/src/canvas/Canvas.tsx`)
**Dependencies**: `reactflow` v11.11.4 (already installed); existing `graphStore`, `refToNodeType`, `PaletteItem` drag protocol — no new packages.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE BEFORE IMPLEMENTING

- `web/builder-ui/src/canvas/Canvas.tsx` (full file, ~98 lines)
  - Why: Primary edit site. `<ReactFlow>` JSX is at lines 64–86. Handlers `onNodesChange`, `onEdgesChange`, `onConnect`, `onNodeClick`, `onPaneClick` already wired — mirror their style. `<ReactFlowProvider>` wraps `<CanvasInner />` at lines 92–98, so `useReactFlow()` is safe inside `CanvasInner`.
- `web/builder-ui/src/palette/PaletteItem.tsx` (full file, ~30 lines)
  - Why: Drag source. `onDragStart` (lines 20–23) sets MIME `'application/vc-palette'` with payload `JSON.stringify({ ref: ref_, kind })`. Do not change this file — the canvas must consume this exact protocol.
- `web/builder-ui/src/palette/Palette.tsx`
  - Why: Confirms the five sections (Commands, Workflows, Pipeline, Control, Perception) and the `PaletteEntry` shape `{ name, description?, ref? }`. Hardcoded items (control/perception) and dynamic items (commands/workflows/pipeline) all funnel through `PaletteItem`, so a single drop handler covers every case.
- `web/builder-ui/src/store/graphStore.ts` (lines 23, 155–174)
  - Why: `nodes: Node[]` state at line 23. `setNodes(updater)` at lines 155–160 — accepts function or array, sets `dirty: true`. `applyNodeChangesStore` at lines 169–174 (already wired into canvas `onNodesChange`). Also has `selectNode(id | null)` action — call this with the new node's id after drop so the inspector opens.
- `web/builder-ui/src/canvas/nodes/index.ts` (lines 1–14)
  - Why: `nodeTypes` registration. Registered keys: `tool`, `command`, `workflow`, `branch`, `foreach`, `perception`. The `type` field of any new node MUST be one of these strings, otherwise React Flow falls back to the default node and the custom rendering breaks.
- `web/builder-ui/src/canvas/nodes/ToolNode.tsx` (lines 1–79)
  - Why: Example custom node. `ToolNodeData` interface (lines 6–10): `{ ref: string; kwargs: Record<string, unknown>; label?: string }`. New nodes must populate `data.ref` and `data.kwargs` to render.
- `web/builder-ui/src/lib/graphSerialize.ts` (lines 26–35 + `refToNodeType` at ~lines 137–144)
  - Why: `refToNodeType(ref)` maps a canonical ref string to a registered node-types key. **Reuse this helper** — do not duplicate the mapping logic. Mapping: `'control.branch' → 'branch'`, `'control.foreach' → 'foreach'`, `'perception.*' → 'perception'`, `'command.*' → 'command'`, `'workflow.*' → 'workflow'`, else `'tool'`. `toFlowNode(gn)` at lines 26–35 shows the canonical Node shape: `{ id, type, position, data: { ref, kwargs } }` — mirror this exactly.
- `web/builder-ui/src/types/graph.ts` (lines 17–22)
  - Why: `GraphNode` canonical shape `{ id, ref, kwargs?, position? }`. The Zustand store holds React Flow `Node` objects (with `type`, `position`, `data`); the canonical `GraphNode` shape is only used at serialize/deserialize time.
- `web/builder-ui/src/App.tsx` (lines 52–62)
  - Why: Existing `crypto.randomUUID()` usage with `Math.random()` fallback for graph names. Mirror this pattern for node id generation.
- `docs/decisions/0071-builder-react-spa.md`
  - Why: Background on the React SPA migration; "Canvas behaviour (drag-from-palette, wire, save) is preserved with React Flow" — this fix delivers on that commitment.

### New Files to Create

None. This is a pure edit to `Canvas.tsx` plus a small unit test file.

- `web/builder-ui/src/canvas/__tests__/Canvas.dragdrop.test.tsx` — Vitest unit test for the drop handler logic (mock `useReactFlow`, mock `graphStore`, simulate drag+drop events).

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING

- [React Flow v11 — Drag and Drop example](https://reactflow.dev/examples/interaction/drag-and-drop)
  - Specific section: "Adding Nodes on Drop" — full reference implementation for the v11 API.
  - Why: Canonical pattern. Note the `onDragOver` `preventDefault()` requirement and `screenToFlowPosition` usage.
- [React Flow v11 — `useReactFlow` hook](https://reactflow.dev/api-reference/hooks/use-react-flow)
  - Specific section: `screenToFlowPosition({ x, y })` returns flow-space coordinates accounting for pan/zoom.
  - Why: This is the modern replacement for the v10-era `project()` method. Confirm signature before use.
- [MDN — `DataTransfer.getData()`](https://developer.mozilla.org/en-US/docs/Web/API/DataTransfer/getData)
  - Why: How to read the JSON payload set by `PaletteItem`.
- [MDN — `dragover` event](https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement/dragover_event)
  - Specific section: "preventDefault() must be called to allow drop." — explains the silent-no-op footgun.
  - Why: Root-cause-adjacent reading.

### Patterns to Follow

**Naming conventions (TypeScript + React, this repo):**

- Handler functions: `onX` prefix when passed as props (`onNodesChange`), `handleX` for internal handlers when needed. Use `onDragOver` / `onDrop` to match React Flow's prop names directly when defining inline.
- Helper functions inside components: `useCallback` with explicit dependency arrays. Mirror the style at `Canvas.tsx:27` (`const onNodesChange = useCallback((changes) => { ... }, [applyNodeChangesStore])`).
- Type imports separated from value imports: `import type { Node } from 'reactflow'` vs `import { ReactFlow } from 'reactflow'`.

**Drag payload contract (already defined by `PaletteItem.tsx:20–23`, do not change):**

```ts
// Drag source (PaletteItem.tsx)
e.dataTransfer.effectAllowed = 'move'
e.dataTransfer.setData('application/vc-palette', JSON.stringify({ ref: ref_, kind }))
```

```ts
// Drop sink (NEW, in Canvas.tsx)
const raw = event.dataTransfer.getData('application/vc-palette')
if (!raw) return
const { ref, kind } = JSON.parse(raw) as { ref: string; kind: string }
```

**Node creation pattern (mirror `toFlowNode` in `lib/graphSerialize.ts:26–35`):**

```ts
const newNode: Node = {
  id: crypto.randomUUID(),
  type: refToNodeType(ref),       // reuse existing helper
  position,                        // from screenToFlowPosition
  data: { ref, kwargs: {} },
}
```

**Store update pattern (functional updater, marks dirty automatically — see `graphStore.ts:155–160`):**

```ts
setNodes((current) => [...current, newNode])
selectNode(newNode.id)
```

**Error handling pattern:** This codebase prefers silent early returns over thrown errors in UI event handlers (see `onPaneClick` returning bare `selectNode(null)` without try/catch). Wrap `JSON.parse` in `try` and return on failure rather than crashing the canvas.

**Logging pattern:** No `console.log` in shipped code. If you need temporary logs during dev, remove them before committing — `make builder-lint` will not catch this, but a self-review must.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation

Read every file in CONTEXT REFERENCES to confirm the exact line numbers, types, and helpers still exist and match the descriptions above. Confirm that `refToNodeType` is exported from `lib/graphSerialize.ts` — if it is not exported, export it (this is a pure function with no side effects, so exporting it is safe).

**Tasks:**

- Read `Canvas.tsx`, `graphStore.ts`, `lib/graphSerialize.ts`, `nodes/index.ts`, `PaletteItem.tsx` end-to-end.
- Confirm `refToNodeType` is exported. If not, export it.
- Verify `reactflow` package version in `package.json` is still v11.x — if it has been upgraded to `@xyflow/react` v12, the import paths and `screenToFlowPosition` API are unchanged in v12 but the package name is different. Adjust imports accordingly.

### Phase 2: Core Implementation

Add `onDragOver` and `onDrop` handlers to `<ReactFlow>` inside `CanvasInner` in `Canvas.tsx`. Wire them through `useReactFlow()` for coordinate conversion and through `useGraphStore()` for state mutation.

**Tasks:**

- Add `useReactFlow` import from `reactflow`.
- Add `screenToFlowPosition` ref via `useReactFlow()` inside `CanvasInner`.
- Define `onDragOver` callback: `event.preventDefault()` + `event.dataTransfer.dropEffect = 'move'`.
- Define `onDrop` callback: parse payload, validate, compute position, build node, push to store, select node.
- Wire both as JSX props on `<ReactFlow>`.

### Phase 3: Integration

The drop handler integrates with the existing palette (no palette change), the existing node-type registry, the existing store actions, and the existing inspector panel (which reacts to `selectNode`). Confirm each integration point still works after the fix.

**Tasks:**

- Manually drag one item from each of the 5 palette sections (Commands, Workflows, Pipeline, Control, Perception) and confirm it appears on the canvas with the correct visual styling.
- Confirm the inspector panel opens with the new node selected.
- Confirm the graph is marked dirty (Save button enables) after the first drop.
- Confirm the new node has a unique id (drop two of the same item — they should not collide).

### Phase 4: Testing & Validation

Add a Vitest unit test that mocks `useReactFlow` and the graph store, simulates a drag+drop event with a known payload, and asserts the store action was called with the correct node shape. Run lint, typecheck, build, and manual browser validation.

**Tasks:**

- Write `Canvas.dragdrop.test.tsx` covering: happy path (command ref), happy path (control.branch ref), invalid JSON payload (handler returns silently), missing MIME type (handler returns silently).
- Run `make builder-lint` — must pass with zero errors.
- Run `make builder-build` — must produce `src/voice_commander/web/static/builder/` artifacts.
- Manually verify in browser at `make builder-dev` (port 5173) — drag every palette section.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### UPDATE `web/builder-ui/src/lib/graphSerialize.ts`

- **IMPLEMENT**: Confirm `refToNodeType` is exported. If currently `function refToNodeType(...)`, change to `export function refToNodeType(...)`. No other changes.
- **PATTERN**: Mirror existing exports in this file — `export function toFlowNode` at line 26.
- **IMPORTS**: None added.
- **GOTCHA**: This function is referenced by `toFlowNode` already — exporting it is purely additive. Do not change its body.
- **VALIDATE**: `cd web/builder-ui && pnpm typecheck` — must succeed.

### UPDATE `web/builder-ui/src/canvas/Canvas.tsx` — add imports

- **IMPLEMENT**: Add `useReactFlow` to the existing `reactflow` import. Add `refToNodeType` import from `../lib/graphSerialize`. Add `Node` type import from `reactflow` if not already present.
- **PATTERN**: Existing import block at top of file. Mirror style.
- **IMPORTS**:
  ```ts
  import { ReactFlow, ReactFlowProvider, addEdge, useReactFlow, type Node } from 'reactflow'
  import { refToNodeType } from '../lib/graphSerialize'
  ```
- **GOTCHA**: Do not double-import existing names. If `Node` is already imported as a type, leave it alone.
- **VALIDATE**: `cd web/builder-ui && pnpm typecheck`.

### UPDATE `web/builder-ui/src/canvas/Canvas.tsx` — add `useReactFlow` hook call inside `CanvasInner`

- **IMPLEMENT**: Inside `CanvasInner` function body, after the existing `useGraphStore` selector calls, add `const { screenToFlowPosition } = useReactFlow()`.
- **PATTERN**: Mirror destructured-store-selector style at lines 20–25.
- **IMPORTS**: None added (already added in previous task).
- **GOTCHA**: `useReactFlow()` only works inside a `<ReactFlowProvider>` subtree. `CanvasInner` is wrapped at lines 92–98, so this is safe. Do NOT call `useReactFlow()` from the outer `Canvas` export — it will throw at runtime.
- **VALIDATE**: `cd web/builder-ui && pnpm typecheck`.

### UPDATE `web/builder-ui/src/canvas/Canvas.tsx` — add `onDragOver` callback

- **IMPLEMENT**: Add `useCallback` for `onDragOver` that calls `event.preventDefault()` and sets `event.dataTransfer.dropEffect = 'move'`. Type the parameter as `React.DragEvent<HTMLDivElement>`.
- **PATTERN**: Mirror `onNodesChange` callback style at lines 27–30.
- **IMPORTS**: Already added.
- **GOTCHA**: **Without `preventDefault()`, the drop will silently fail. This is the single most common React Flow drag-drop footgun.** Both calls are required.
- **VALIDATE**: `cd web/builder-ui && pnpm typecheck`.

### UPDATE `web/builder-ui/src/canvas/Canvas.tsx` — add `onDrop` callback

- **IMPLEMENT**: Add `useCallback` for `onDrop`. Body: call `event.preventDefault()`. Read `event.dataTransfer.getData('application/vc-palette')` into `raw`. If empty, return. Wrap `JSON.parse(raw)` in `try`. Validate the parsed object has `ref: string` and `kind: string` — if not, return. Compute `position` via `screenToFlowPosition({ x: event.clientX, y: event.clientY })`. Build `newNode: Node` with `id: crypto.randomUUID()`, `type: refToNodeType(ref)`, `position`, `data: { ref, kwargs: {} }`. Call `setNodes((current) => [...current, newNode])`. Call `selectNode(newNode.id)`.
- **PATTERN**: Mirror `onConnect` style at lines 37–50 (multi-step useCallback that mutates store).
- **IMPORTS**: Already added.
- **GOTCHA**: `crypto.randomUUID` is supported in all evergreen browsers but not in older Safari and not in Node test environments. The mocked test environment must polyfill it (Vitest does this automatically with happy-dom v15+; double-check). Wrap `JSON.parse` in `try/catch` — malformed payloads must not crash the canvas.
- **GOTCHA**: Dependency array MUST include `screenToFlowPosition`, `setNodes`, and `selectNode`. Missing deps cause stale closures.
- **VALIDATE**: `cd web/builder-ui && pnpm typecheck && pnpm lint`.

### UPDATE `web/builder-ui/src/canvas/Canvas.tsx` — wire handlers into JSX

- **IMPLEMENT**: Add `onDragOver={onDragOver}` and `onDrop={onDrop}` props to the `<ReactFlow>` element at lines 64–86.
- **PATTERN**: Mirror existing prop placement (one per line, alphabetized informally — match surrounding style).
- **IMPORTS**: None.
- **GOTCHA**: Both props must be on the `<ReactFlow>` element itself, not on an outer wrapper `<div>`. React Flow forwards drag events from its internal panning surface; placing handlers on a wrapper will receive events but `screenToFlowPosition` coordinates assume the React Flow viewport bounds.
- **VALIDATE**: `cd web/builder-ui && pnpm typecheck && pnpm lint && pnpm build`.

### CREATE `web/builder-ui/src/canvas/__tests__/Canvas.dragdrop.test.tsx`

- **IMPLEMENT**: Vitest unit test. Mock `reactflow` `useReactFlow` to return a stub `screenToFlowPosition` that returns `{ x: 100, y: 200 }`. Mock `useGraphStore` to capture `setNodes` and `selectNode` calls. Render `<Canvas />`. Simulate `dragOver` (assert `preventDefault` called) and `drop` with a known payload (assert `setNodes` called with a node whose `type === 'command'`, `data.ref === 'command.greet'`, `position.x === 100`, `position.y === 200`). Cover three cases: (1) valid command ref, (2) `control.branch` payload → `type === 'branch'`, (3) malformed JSON → no `setNodes` call, no crash.
- **PATTERN**: Look at any existing `web/builder-ui/src/**/*.test.tsx` for the project's test conventions. If none exist yet, use Vitest + React Testing Library. The repo has `pnpm test --run` configured at `package.json:12`.
- **IMPORTS**:
  ```ts
  import { describe, it, expect, vi, beforeEach } from 'vitest'
  import { render, fireEvent } from '@testing-library/react'
  import { Canvas } from '../Canvas'
  ```
- **GOTCHA**: `crypto.randomUUID` may need explicit polyfill in test env. Stub it with `vi.spyOn(crypto, 'randomUUID').mockReturnValue('test-uuid-1234')` to make assertions deterministic.
- **GOTCHA**: `DataTransfer` cannot be constructed directly in JSDOM/happy-dom in some versions. Use `new Event('drop')` and manually attach a `dataTransfer` mock object via `Object.defineProperty`.
- **VALIDATE**: `cd web/builder-ui && pnpm test --run -- Canvas.dragdrop`.

### MANUAL VALIDATION — browser smoke test

- **IMPLEMENT**: Run `make builder-dev`. Open `http://localhost:5173/page/builder`. Drag one item from each of the five palette sections onto the canvas. Confirm each lands at cursor position with the correct color/shape. Confirm the inspector panel opens for the dropped node. Confirm Save button enables (graph is dirty).
- **PATTERN**: This is the human validation step required by `CLAUDE.md` — phased delivery means a human must confirm before claiming done.
- **GOTCHA**: If the dev server's proxy to `http://localhost:8765` fails (the daemon isn't running), the palette will be empty for Commands/Workflows/Pipeline. Start the daemon first OR drag from Control/Perception sections (hardcoded, no API needed) for an offline smoke test.
- **VALIDATE**: Manual browser test — every palette section produces a node on drop.

---

## TESTING STRATEGY

### Unit Tests

Use Vitest (already configured at `web/builder-ui/package.json:12`) with React Testing Library. Test file: `web/builder-ui/src/canvas/__tests__/Canvas.dragdrop.test.tsx`.

Cover:

- Happy path: drop a `command.*` ref → store receives a node with `type='command'`, correct `data.ref`, correct `position`, unique `id`.
- Happy path: drop a `control.branch` ref → store receives `type='branch'`.
- Happy path: drop a `perception.ocr` ref → store receives `type='perception'`.
- Edge case: malformed JSON payload → handler does not call `setNodes`, does not throw.
- Edge case: missing MIME type → handler does not call `setNodes`, does not throw.
- Behaviour: `onDragOver` calls `preventDefault()` (otherwise drop never fires).

Mock `useReactFlow` to control `screenToFlowPosition` output. Mock the Zustand store with a vi spy on `setNodes` and `selectNode`.

### Integration Tests

The repo has Playwright e2e tests configured (`pnpm test:e2e`). Add a single e2e test that:

1. Opens the Builder UI.
2. Locates a known palette item (e.g. the hardcoded `control.branch` since it has no API dependency).
3. Performs a HTML5 drag-drop using Playwright's `dragTo()` API.
4. Asserts a node with the expected `data-testid` or visible label appears on the canvas.

If the existing test infrastructure does not already cover the Builder page, this single happy-path test is sufficient — do not boil the ocean on test infra in a bug-fix PR.

### Edge Cases

- **Drop outside React Flow viewport**: cannot happen — `<ReactFlow>` is the drop target, not the document.
- **Drop with no active drag**: `onDrop` only fires when a drag is in progress; not a real edge case.
- **Two drops of the same palette item in quick succession**: each must produce a unique `id` (covered by `crypto.randomUUID`).
- **Drag from outside the app (e.g. desktop file)**: `getData('application/vc-palette')` returns empty string; handler returns silently. Verified by the malformed-payload test.
- **Drop while in read-only / pan mode**: out of scope — current Builder has no read-only mode. Skip.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness.

### Level 1: Syntax & Style

```bash
cd web/builder-ui && pnpm lint
cd web/builder-ui && pnpm typecheck
```

Both must exit 0.

### Level 2: Unit Tests

```bash
cd web/builder-ui && pnpm test --run -- Canvas.dragdrop
cd web/builder-ui && pnpm test --run
```

The first targets the new test specifically; the second runs the whole Vitest suite to confirm no regressions.

### Level 3: Build

```bash
make builder-build
```

Must produce artifacts in `src/voice_commander/web/static/builder/` with no errors. Equivalent to `cd web/builder-ui && pnpm build`.

### Level 4: Manual Validation

1. Start the daemon: `python -m voice_commander` (or whatever the project's start command is — check `Makefile` or `pyproject.toml`).
2. In a second terminal: `make builder-dev`.
3. Open `http://localhost:5173/page/builder` (Vite dev server, with proxy to daemon at `:8765`).
4. Drag one item from each section: Commands, Workflows, Pipeline, Control (e.g. branch), Perception (e.g. ocr).
5. Confirm: node appears at cursor, has correct color, inspector panel opens with new node selected, Save button enables.
6. Click Save. Reload page. Confirm dropped nodes persist.
7. Open the "New Command" flow. Drag a listed command onto the fresh canvas. Confirm the node appears.

### Level 5: Additional Validation

- Run the full project test suite if one exists: `make test` or `pytest` from repo root. Out-of-scope code paths should be unaffected, but a green run gives confidence.
- Run the linter on the daemon side too: `make lint` from repo root. Should be unaffected.

---

## ACCEPTANCE CRITERIA

- [ ] Dragging any item from any palette section (Commands, Workflows, Pipeline, Control, Perception) produces a node on the canvas at the cursor position.
- [ ] Each dropped node has a unique id.
- [ ] Each dropped node renders with the correct custom node component (ToolNode, BranchNode, ForeachNode, PerceptionNode) per `nodeTypes` registration.
- [ ] The inspector panel opens with the dropped node selected.
- [ ] The Save button enables after the first drop (graph dirty flag set).
- [ ] Saved graphs reload correctly with all dropped nodes intact.
- [ ] `pnpm lint` exits 0.
- [ ] `pnpm typecheck` exits 0.
- [ ] `pnpm test --run` exits 0.
- [ ] `make builder-build` succeeds.
- [ ] No regressions in existing canvas behaviour: connecting nodes, selecting/deselecting, applying changes, panning, zooming.
- [ ] No `console.log` or debug code left in shipped files.
- [ ] ADR 0071 is unaffected — this fix delivers behaviour the ADR already promised; no new ADR required.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order.
- [ ] Each task validation passed immediately.
- [ ] All Level 1–4 validation commands executed successfully.
- [ ] Full unit test suite passes.
- [ ] No linting or type checking errors.
- [ ] Manual browser testing confirms all five palette sections produce nodes.
- [ ] Save/reload round-trip works.
- [ ] Acceptance criteria all met.
- [ ] Code reviewed for quality and maintainability.
- [ ] Commit message follows repo conventions (Conventional Commits — see recent log: `fix(builder): ...`).

---

## NOTES

**Why no new ADR.** ADR 0071 already states "Canvas behaviour (drag-from-palette, wire, save) is preserved with React Flow." This fix delivers that promise — it does not change architecture or introduce a new pattern. A `fix:` commit is the right vehicle, not a new ADR.

**Why reuse `refToNodeType` instead of inlining.** The mapping from canonical `ref` strings to React Flow node-type keys is the single source of truth for how the Builder renders nodes. Duplicating that logic in the drop handler would create a drift hazard: if a new perception primitive or control flow type is added, the registry would be updated in `lib/graphSerialize.ts` but the drop handler would silently fall back to `'tool'`, producing wrong-shaped nodes that look correct in the palette and broken on the canvas. Reuse keeps both paths in lockstep.

**Why `crypto.randomUUID()` and not a counter.** Counters require store coordination (read max id, increment, write) which is racy. `crypto.randomUUID()` is collision-safe, requires no shared state, and matches the existing pattern in `App.tsx:52–62`. Backend (`commands/graph_schema.py`) accepts arbitrary string ids and does not require a specific format.

**Why `setNodes` over a hypothetical `addNode`.** The store currently exposes `setNodes(updater)` with a functional updater, which is React-idiomatic and Zustand-idiomatic. Adding a separate `addNode(node)` action would be a thin wrapper with no clear win. Use the existing API.

**On the "New Command" flow.** Investigation revealed that the "New Command" page reuses the same Builder Canvas component — it is not a separate UI. Once drag-drop works on the main canvas, the New Command flow inherits the fix automatically. No separate change required.

**Confidence score for one-pass implementation: 9/10.** The bug is well-isolated, the fix is small (~30 lines added to one file plus one test file), every helper and integration point already exists, and the React Flow v11 drag-drop pattern is well-documented and stable. The 1-point deduction accounts for possible test-environment quirks around `DataTransfer` mocking in JSDOM/happy-dom.
