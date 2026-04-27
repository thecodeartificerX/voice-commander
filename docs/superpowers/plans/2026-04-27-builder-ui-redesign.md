# Feature: Builder UI Redesign — Visual Taxonomy + Primitive Drag Bug Fix

The following plan should be complete, but it is important that you validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types, and models. Import from the right files. The canonical Graph JSON schema is **immutable** — this work is visual + interaction only.

## Feature Description

Redesign the Drawflow-based node graph builder at `/page/builder` to make node categories, port semantics, and wiring intent legible at a glance. Replace the current monochrome cyan-on-dark scheme with a category-coded gradient palette, add per-category node shapes, color-code control-flow vs data ports, and remove visual noise (raw timestamp IDs). Fix the bug where pipeline primitive palette buttons (`press`, `click`, `type`, `focus`, `open`, etc.) silently fail to add nodes to the canvas.

## User Story

As a Voice Commander power user authoring graphs in the Builder UI,
I want each node category to have a distinct color, shape, and port semantics,
So that I can read a graph at a glance, wire control vs data flows confidently, and reach for primitive nodes without the palette swallowing my clicks.

## Problem Statement

The current Builder UI fails on three axes:

1. **Readability.** All nodes use the same cyan-on-dark gray box. Title text (`#5eead4` on `#1f2937`) has poor contrast at small sizes; the canonical node ID (`n1777252522214`) is rendered as prominent body content despite being a developer-only token. Pure black canvas + white I/O ports give no semantic signal.
2. **Taxonomy invisibility.** Pipeline primitives, user commands, workflows, control-flow nodes, and value nodes look identical aside from a thin border tint on `control` and `value`. Wiring intent (control flow vs data flow, branch outcome vs success/error) is opaque — users must remember the schema.
3. **Broken primitive interaction.** Clicking a pipeline primitive button in the palette throws a TypeError in `addNodeToCanvas`. The backend serialises pipeline `args` as a **dict** (`tool_schema.describe_tool_for_builder`); `builder.js:255` iterates with `for...of args` which fails on plain objects. Commands and workflows are unaffected because `_describe_graph` returns `inputs` as an array.

## Solution Statement

Three coordinated workstreams, each behind a phase gate:

- **Phase 1 — Bug fix.** Normalise pipeline `args` to an array shape inside `addNodeToCanvas` (or upstream in the response builder) so primitives drag/click identically to commands and workflows. Add a regression test.
- **Phase 2 — Visual taxonomy.** Introduce a per-category palette (Pipeline / Command / Workflow / Control / Value) using sleek dark gradients with high-contrast titles. Add per-category node shapes via CSS clip-path or border-radius profiles. Hide canonical node IDs from the node body (move them to a tooltip / config rail subtitle). Promote node label typography (size, weight, color) so the ref name is the dominant visual element.
- **Phase 3 — Port semantics.** Color and shape control-flow ports (`ok`, `error`, `true`, `false`, `item`, `after`, `in`) distinct from data ports. Render port labels on hover. Apply matching connection-line tint to make the wiring legible end-to-end.

All work stays inside `src/voice_commander/web/static/builder.js`, `src/voice_commander/web/templates/page_builder.html`, and a new dedicated stylesheet `src/voice_commander/web/static/builder.css`. Backend changes are restricted to one shape normalisation in `src/voice_commander/tool_schema.py` (or equivalently a one-line normalisation in builder.js, see Task 1.1 — pick one). The canonical graph schema, runtime, and registrar are untouched.

## Feature Metadata

**Feature Type**: Enhancement + Bug Fix
**Estimated Complexity**: Medium
**Primary Systems Affected**:
- `src/voice_commander/web/static/builder.js`
- `src/voice_commander/web/templates/page_builder.html`
- `src/voice_commander/web/static/builder.css` (NEW)
- `src/voice_commander/tool_schema.py` (1-line shape change OR leave as-is; see Task 1.1)
- `tests/integration/test_builder_routes.py` (regression coverage)
- `tests/unit/` — no Python builder unit tests today; add coverage for the schema normalisation if chosen

**Dependencies**: None new. Uses existing vendored Drawflow 1.x at `src/voice_commander/web/static/drawflow.min.js` + Tailwind CDN (page-level `<link>`).

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE BEFORE IMPLEMENTING

- `src/voice_commander/web/static/builder.js` (lines 80–355, full reading recommended)
  - Why: All palette load, port resolution, node creation, hydration, and export flows live here. Every visual or interaction change touches one of `renderPaletteSection` (138–154), `resolvePortsForRef` (178–206), `addNodeToCanvas` (245–302), `hydrateFromCanonical` (318–355).
- `src/voice_commander/web/templates/page_builder.html` (full file, 51 lines)
  - Why: Hosts the inline `<style>` block currently providing `.vc-node*` rules. Move these into the new `builder.css`. Three-pane layout: `#builder-palette` (left), `#builder-canvas` (center, Drawflow mounts), `#builder-config` (right).
- `src/voice_commander/web/builder.py` (lines 84–125)
  - Why: `/graph/palette` endpoint. Returns `{pipeline, commands, workflows, control, value}`. Pipeline items have `args` as **dict**; commands/workflows have `inputs` as **array**. This shape mismatch is the root cause of the primitive drag bug.
- `src/voice_commander/tool_schema.py` (function `describe_tool_for_builder`)
  - Why: Builds the pipeline descriptor. Either change it to emit `args` as an array of `{name, type, description, required}` objects, OR leave it and normalise in builder.js. See Task 1.1 for the choice.
- `src/voice_commander/web/static/drawflow.min.css` (vendored)
  - Why: Drawflow defaults for `.drawflow-node`, `.input`, `.output`, `.connection .main-path`. Our new `builder.css` overrides these — read the relevant selectors so overrides are surgical, not global.
- `src/voice_commander/web/static/drawflow.min.js` (vendored, ~45 KB)
  - Why: Reference only. Never edit. We work with the published API: `addNode(name, in, out, x, y, class, data, html)`, `addConnection`, events.
- `src/voice_commander/commands/graph_runtime.py` (lines 110–455)
  - Why: Authoritative source for what each control-flow port name means (`ok`/`error`/`true`/`false`/`item`/`after`/`in`). The visual scheme must match runtime semantics.
- `src/voice_commander/commands/graph.py`
  - Why: `Graph` and `Node` dataclass shapes. Read once to confirm we never change canonical schema.
- `tests/integration/test_builder_routes.py`
  - Why: Existing palette test asserts only structure. Add a test that asserts pipeline `args` is iterable in the same way commands' `inputs` are (i.e. arrays of typed objects), or that round-trips a primitive node through the canvas state.

### New Files to Create

- `src/voice_commander/web/static/builder.css`
  - Holds all builder visual rules: node base, per-category gradients/shapes, port colors/shapes, connection tints, header/sidebar polish.
- `tests/integration/test_builder_palette_shapes.py` (or extend `test_builder_routes.py`)
  - Regression test for pipeline `args` shape and for the consistent palette contract.

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING

- [Drawflow GitHub README](https://github.com/jerosoler/Drawflow)
  - Section: "Custom node design" and the events list (`nodeSelected`, `nodeUnselected`, `connectionCreated`, `connectionRemoved`).
  - Why: Confirm the addNode signature, custom HTML conventions, and that custom CSS targeting `.drawflow-node[class~="vc-node-X"] .input_1` works as expected.
- [Drawflow node + port styling guide](https://github.com/jerosoler/Drawflow#about-input--output-)
  - Specific section: how `.input_1`, `.input_2`, `.output_1`, etc. are generated and how to target them by index.
  - Why: We need to color individual ports by name, but Drawflow only exposes ports by 1-indexed numeric class. Strategy: add a `data-port-name="ok"` attribute on each port via a post-addNode DOM pass keyed off the node's `_in_ports`/`_out_ports` arrays.
- [MDN — CSS clip-path](https://developer.mozilla.org/en-US/docs/Web/CSS/clip-path)
  - Why: Used to give `control.branch` a hexagonal hint and `value.*` a parallelogram tilt without breaking Drawflow's hit-testing on the underlying rectangular box.
- [MDN — CSS linear-gradient](https://developer.mozilla.org/en-US/docs/Web/CSS/gradient/linear-gradient)
  - Why: Per-category gradient backgrounds.
- Project ADRs (read both):
  - `docs/decisions/0022-vendored-frontend-no-react-spa.md` — confirms no toolchain changes; vanilla JS + CSS only.
  - `docs/decisions/0064-drawflow-builder.md` (if present; otherwise the most recent ADR in the 0062–0068 graph series) — for any constraints on Drawflow customisation.
- `CLAUDE.md` (root) — project workflow conventions, especially the "boil the ocean" + phased delivery rule.

### Patterns to Follow

**Naming conventions** (observed):
- CSS classes for builder: `vc-node`, `vc-node-<category>` where category ∈ {`pipeline`, `command`, `workflow`, `control`, `value`}. Continue this pattern. Add `vc-port`, `vc-port-<name>` (e.g. `vc-port-ok`, `vc-port-error`, `vc-port-true`, `vc-port-false`, `vc-port-data`, `vc-port-in`).
- JavaScript: camelCase functions, `df-`-prefixed Drawflow attribute hooks (`df-kwarg-<name>`). Maintain.
- Python: snake_case, dataclasses for typed payloads, FastAPI route decorators on a router instance (see `builder.py` for the pattern).

**Error handling** (observed in builder.js):
- Network failures → log to `console.error` AND populate `#builder-errors` with a user-readable line, then `classList.remove('hidden')`. Mirror this for any new failure surface (e.g. failed port hydration on legacy graphs).
- Defensive null coalescing in port/kwarg paths (`desc.args || desc.inputs || []`). Maintain — but normalise dict→array first to avoid masking the bug we are fixing.

**Logging pattern** (Python side):
- Module-level `logger = logging.getLogger(__name__)`.
- Log shape mismatches in `tool_schema.describe_tool_for_builder` at `INFO` if the schema is enriched, so any future regression surfaces in the daemon log on startup.

**Other patterns**:
- All inline styles currently in `page_builder.html` move to `builder.css`. The template references the new stylesheet via `<link rel="stylesheet" href="/static/builder.css">` next to the existing Drawflow CSS link.
- DOM mutation for safety: when annotating ports with `data-port-name`, use `setAttribute`, never `innerHTML` (consistent with the M2 XSS fix in PR #70).

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — Fix the primitive drag bug + scaffolding

Restore primitive usability first; the visual work depends on being able to actually drop a `pipeline.press` on the canvas to inspect rendering. Also create the empty `builder.css` and wire it into the template so subsequent phases edit a real file.

**Tasks:**

- Decide the normalisation site (backend `tool_schema.describe_tool_for_builder` vs frontend `addNodeToCanvas`). Recommendation: **frontend normalisation** keeps backend stable, avoids touching the dispatcher's other consumers of `args`, and is one change in one file. Do that unless you find a backend caller that would benefit from the array shape.
- Normalise `args` into a uniform array of `{name, type, description, required}` at the top of `addNodeToCanvas` and inside `resolvePortsForRef` (both currently iterate `args`).
- Create `builder.css`, move existing `.vc-node*` rules out of `page_builder.html`, and reference the new sheet.
- Add a regression test that fetches `/graph/palette`, picks a pipeline entry, and asserts the descriptor exposes argument names in a predictable form.

### Phase 2: Visual Taxonomy — Per-category gradients, shapes, typography

Apply a coherent visual language so nodes self-identify by category at a glance. Introduce category gradients, distinct node shapes (subtle, not gimmicky), and quiet the canonical-id noise.

**Tasks:**

- Define a five-color palette with dark-friendly gradients and white/near-white titles:
  - **Pipeline** (action verbs / leaf primitives): amber → bronze (`linear-gradient(135deg, #d97706 0%, #92400e 100%)`).
  - **Command** (user-defined named shortcuts): teal → deep-cyan (`linear-gradient(135deg, #0d9488 0%, #134e4a 100%)`).
  - **Workflow** (multi-step macros): violet → indigo (`linear-gradient(135deg, #7c3aed 0%, #3730a3 100%)`).
  - **Control** (branch / foreach): rose → maroon (`linear-gradient(135deg, #e11d48 0%, #881337 100%)`).
  - **Value** (constant / input / output): emerald → forest (`linear-gradient(135deg, #059669 0%, #064e3b 100%)`).
- Apply category-distinct **shape modifiers** without breaking Drawflow's rectangular hit area:
  - Pipeline: standard rounded rect (`border-radius: 6px`), 2px solid accent border (matching gradient end stop).
  - Command: pill rounded rect (`border-radius: 14px`).
  - Workflow: rounded rect with chevron banner — implemented via `::before` pseudo carrying `▶▶▶` Unicode in 60% opacity at the top-right corner.
  - Control: rounded rect with hexagonal `clip-path` corner accent on the right edge (`clip-path: polygon(0 0, calc(100% - 12px) 0, 100% 50%, calc(100% - 12px) 100%, 0 100%)`). Verify hit-testing still works for port grabs at the trimmed corners — if not, fall back to a `::after` chevron decoration.
  - Value: parallelogram skew applied via a `::before` pseudo background (`transform: skewX(-8deg)`); the underlying Drawflow box stays rectangular for hit accuracy.
- Title typography: `font-size: 13px`, `font-weight: 600`, `color: #f9fafb`, `text-shadow: 0 1px 0 rgba(0,0,0,.4)`. Drop the existing `#5eead4` cyan title color.
- Hide the raw canonical id from the node body. Move it to:
  - The node's HTML `title=` attribute (browser tooltip).
  - The right sidebar (`#builder-config` rail) under the ref display.
  - Remove the `<div class="df-node-id">` from `addNodeToCanvas`'s `html` template entirely; do NOT keep it as `display:none` (avoid dead DOM).
- Increase node minimum width to `160px` so titles + at least one kwarg input read comfortably.
- Canvas background: switch from pure black to a subtle dotted grid (`background-color: #0a0f1a; background-image: radial-gradient(rgba(255,255,255,.04) 1px, transparent 1px); background-size: 16px 16px;`) — gives spatial cues without competing with nodes.

### Phase 3: Port Semantics — Color, shape, and labels

Make wiring intent visible. A user dragging from a `branch` should see at a glance which port is `true` vs `false`, and connecting `ok` → `in` should look obviously correct.

**Tasks:**

- Annotate each port with a `data-port-name` attribute by post-processing the DOM after `editor.addNode`. Inside `addNodeToCanvas`, after `editor.addNode(...)` returns `dfId`, look up the rendered DOM node (`document.getElementById('node-' + dfId)`) and tag each `.input_N` / `.output_N` with the corresponding name from `inPorts` / `outPorts`.
  - Example: `inPorts = ['in', 'cond']` → `.input_1` gets `data-port-name="in"`, `.input_2` gets `data-port-name="cond"`.
- Color and shape the ports via attribute selectors in `builder.css`:
  - `[data-port-name="in"]` — neutral grey square (8 px), `background: #9ca3af`, `border-radius: 2px`.
  - `[data-port-name="ok"]` — green circle, `background: #22c55e`.
  - `[data-port-name="error"]` — red circle, `background: #ef4444`.
  - `[data-port-name="true"]` — green square, `background: #22c55e`, `border-radius: 2px`.
  - `[data-port-name="false"]` — orange square, `background: #f97316`, `border-radius: 2px`.
  - `[data-port-name="item"]`, `[data-port-name="after"]` — cyan rhombus, `background: #06b6d4`, `transform: rotate(45deg)`.
  - All other ports (data ports — kwargs, return-value keys) — blue circle, `background: #3b82f6`.
- On hover, show a port label using a CSS `::after` containing `attr(data-port-name)` positioned beside the port. Keep it lightweight — no JS tooltip lib.
- Color the connection lines to match the **source** port's color via Drawflow's `connectionCreated` event: walk the SVG `.main-path` and set `stroke` based on the source port's `data-port-name` lookup. Persist by re-applying the same logic in `hydrateFromCanonical` and on every connection event.
- Update the config rail's port list (`renderConfigRail`, builder.js:486–577) to render each port name with a matching color swatch — single source of truth between canvas and rail.

### Phase 4: Palette Polish

The palette sidebar currently lists items as plain text buttons. Make it scannable.

**Tasks:**

- Group palette sections under collapsible headers (`<details>` element, native, no JS). Default open for Pipeline + Commands; default closed for Workflows / Control / Value if `> 8` items in any section.
- Each palette button gets a 6 px colored left-border strip matching its category gradient end stop. Confirms category at a glance during drag/click.
- Add a search input at the top of the palette (`<input type="search" id="palette-filter">`) that filters all sections by `name` substring. Pure JS, no debouncing needed for ~60 items.
- Show each item's description as a `title=` tooltip (already present at line 150 — verify and keep).

### Phase 5: Testing & Validation

**Tasks:**

- Extend `tests/integration/test_builder_routes.py`: assert palette pipeline entries are iterable as arrays of `{name, type, ...}` objects (or whatever the chosen normalisation produces).
- Add a JS-level smoke step in the manual validation list: open `/page/builder?kind=command`, click each pipeline primitive, confirm a node appears.
- Open the existing `commands.json` graphs (e.g. `close_window`, `next_tab`, `new_tab`, `new_window`, `branch` examples) in the builder; visually verify each category's color/shape renders correctly with no missing ports.
- Confirm hot-reload still works: edit a graph in the UI, save, see it reload in the registry; primitive drag still works after a reload.
- Run the full unit test suite (`pytest tests/unit -q`); confirm no regressions (current baseline: 765 passed, 7 skipped).

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### Task 1.1 — UPDATE src/voice_commander/web/static/builder.js: normalise pipeline `args` shape

- **IMPLEMENT**: Add a helper `function normalizeArgs(rawArgs)` that accepts either an array of `{name, ...}` objects (commands/workflows shape) or a dict `{name: {...}}` (pipeline primitives shape) and returns a uniform array `[{name, type, description, required}, ...]`. Apply it everywhere the code reads `desc.args`/`desc.inputs`. Today that is two sites: `resolvePortsForRef` (line 198) and the kwargs loop in `addNodeToCanvas` (line 254).
- **PATTERN**: Mirror the defensive `desc.args || desc.inputs || []` pattern already in builder.js:254. Replace it with `normalizeArgs(desc.args || desc.inputs || {})`.
- **IMPORTS**: None new (vanilla JS).
- **GOTCHA**: Do not assume key order from `Object.entries` matches authoring order across browsers (it does for non-numeric string keys per spec, but be explicit). The normaliser must preserve the original entry order so port indices in `inPorts` align with `addConnection` calls in `hydrateFromCanonical` (lines 344–352).
- **GOTCHA**: A pipeline tool with zero args (e.g. `wait` if it has none) yields `args = {}`. `normalizeArgs({})` must return `[]`, not `[{}]`.
- **VALIDATE**: Manually open `/page/builder?kind=command`, click Pipeline → `press`. A node titled `press` appears with one `combo` kwarg input. Save the graph; reload the page; the node hydrates with the saved `combo` value visible.

### Task 1.2 — CREATE src/voice_commander/web/static/builder.css

- **IMPLEMENT**: New empty stylesheet that will host all builder visual rules. Initial content: just a header comment + the rules currently inline in `page_builder.html`'s `<style>` block (move them, do not duplicate).
- **PATTERN**: Match the file-header comment style of `app.css`.
- **IMPORTS**: N/A.
- **GOTCHA**: Order of CSS includes matters. In the template, link `builder.css` AFTER `drawflow.min.css` so our overrides win without `!important`.
- **VALIDATE**: Visit `/page/builder`. The page renders with no visual regression vs. main (the inline styles having moved to the linked file is a no-op).

### Task 1.3 — UPDATE src/voice_commander/web/templates/page_builder.html

- **IMPLEMENT**: Remove the inline `<style>` block (lines 7–16). Add `<link rel="stylesheet" href="/static/builder.css">` immediately after the existing Drawflow CSS link (line 6).
- **PATTERN**: Same `<link>` form as line 6.
- **GOTCHA**: Keep both stylesheet links inside the `{% block content %}` (where they live now), not the `{% block scripts %}` — Jinja block ordering matters for first-paint.
- **VALIDATE**: Visit `/page/builder`. View source: confirm the `<style>` block is gone and the `builder.css` link is present. Visual identical to before.

### Task 1.4 — ADD regression test in tests/integration/test_builder_routes.py

- **IMPLEMENT**: New test `test_palette_pipeline_args_shape_is_iterable` that calls `client.get("/graph/palette")`, picks any pipeline entry, asserts that the entry has an `args` key, and that iterating it the same way commands' `inputs` are iterated yields valid `{name, type}` shapes. If you choose backend normalisation (alternative to Task 1.1), the assertion is `isinstance(entry["args"], list)`. If you choose frontend normalisation, the assertion is "the dict has consistent value shape", e.g. every value has `type` and `description` keys.
- **PATTERN**: See `test_palette_endpoint_returns_pipeline_commands_workflows_control_value` in the same file (~line 137).
- **IMPORTS**: `from fastapi.testclient import TestClient` (already present).
- **GOTCHA**: The existing palette test asserts only structure; do not delete it — extend.
- **VALIDATE**: `pytest tests/integration/test_builder_routes.py -q` passes including the new test.

### Task 2.1 — UPDATE src/voice_commander/web/static/builder.css: category gradients + shapes

- **IMPLEMENT**: Add CSS rules for `.vc-node-pipeline`, `.vc-node-command`, `.vc-node-workflow`, `.vc-node-control`, `.vc-node-value` with per-category gradients and shape modifiers per Phase 2 spec above. Set base `.vc-node` typography (`color: #f9fafb`, `font-weight: 600`, `font-size: 13px`).
- **PATTERN**: One rule block per category; comment each block with its purpose.
- **GOTCHA**: Drawflow renders nodes inside `.drawflow .drawflow-node`. Specificity for gradient backgrounds must beat Drawflow's `background: #0ff`. Either chain selectors as `.drawflow .drawflow-node.vc-node-pipeline` or use a single CSS variable + a generic `.vc-node { background: var(--vc-node-bg); }`.
- **GOTCHA**: `clip-path` on the parent `.drawflow-node` may cut off ports. Use `clip-path` on a `::before` pseudo-background instead, leaving the underlying box rectangular.
- **VALIDATE**: Drag one of each category onto the canvas. Visually distinct, readable, ports unaffected.

### Task 2.2 — UPDATE src/voice_commander/web/static/builder.js: drop canonical-id from node body

- **IMPLEMENT**: In `addNodeToCanvas`, remove the `<div class="df-node-id">${safeId}</div>` line from the `html` template (line 275). Add `title="${safeId}"` to the wrapping `<div class="df-node-wrap">` so the id is still discoverable on hover. The right-rail (`renderConfigRail`) already shows the canonical id — no change needed there.
- **PATTERN**: Match the existing `escapeAttr(id)` usage already in scope.
- **GOTCHA**: Do NOT change `nodeData._canonical_id` — that is the export contract.
- **VALIDATE**: Add a node, observe the canonical id is no longer rendered. Hover the node — the browser tooltip shows the id. Open the right rail — id is shown there.

### Task 2.3 — UPDATE src/voice_commander/web/static/builder.css: canvas background grid + base node sizing

- **IMPLEMENT**: Set `#builder-canvas { background-color: #0a0f1a; background-image: radial-gradient(rgba(255,255,255,.04) 1px, transparent 1px); background-size: 16px 16px; }`. Set `.vc-node { min-width: 160px; }`.
- **GOTCHA**: Drawflow applies its own background to `.drawflow`. Override via `.drawflow { background: transparent !important; }` so the canvas grid shows through. Confirm panning still works.
- **VALIDATE**: Reload the builder. Background shows the dot grid. Drag a node — pans and drops cleanly.

### Task 3.1 — UPDATE src/voice_commander/web/static/builder.js: tag ports with `data-port-name`

- **IMPLEMENT**: After `editor.addNode(...)` in `addNodeToCanvas`, run a small post-pass:
  ```js
  const dom = document.getElementById('node-' + dfId);
  if (dom) {
    inPorts.forEach((name, i) => {
      const el = dom.querySelector(`.input_${i + 1}`);
      if (el) el.setAttribute('data-port-name', name);
    });
    outPorts.forEach((name, i) => {
      const el = dom.querySelector(`.output_${i + 1}`);
      if (el) el.setAttribute('data-port-name', name);
    });
  }
  ```
  Apply identically inside `hydrateFromCanonical` after each `addNodeToCanvas` call (the call already invokes the same code path, so no extra work — but verify).
- **PATTERN**: DOM mutation via `setAttribute`, not `innerHTML` (per PR #70 hardening).
- **GOTCHA**: Drawflow may not have appended the node DOM by the time `addNode` returns. If `getElementById` returns null, defer with `queueMicrotask(...)` or wrap in `requestAnimationFrame`. Test in DevTools first.
- **VALIDATE**: Inspect a node in DevTools after creation. Each port `<div>` carries `data-port-name="in"` / `"ok"` / etc.

### Task 3.2 — UPDATE src/voice_commander/web/static/builder.css: port colors + shapes

- **IMPLEMENT**: Add the attribute-selector rules from Phase 3 spec for every named port (in / ok / error / true / false / item / after / data fallback).
- **PATTERN**: One selector per port name; chain off `.drawflow .drawflow-node` for specificity.
- **GOTCHA**: Drawflow's default `.input` / `.output` style sets `background-color` directly. Our overrides must be more specific. Use `.drawflow .drawflow-node .input[data-port-name="ok"]`.
- **GOTCHA**: Default un-named (data) ports must still color — fall back via `.drawflow .drawflow-node .input:not([data-port-name="in"])` style chain, or just always tag every port (preferred).
- **VALIDATE**: Add a `control.branch`. The two outputs are visibly green (true) and orange (false). Add a `pipeline.press` — `ok` is green, `error` is red.

### Task 3.3 — UPDATE src/voice_commander/web/static/builder.js: tint connection lines on create + hydrate

- **IMPLEMENT**: Subscribe to `editor.on('connectionCreated', (info) => { ... })`. Inside the handler, look up the source DOM port's `data-port-name`, map to the same color used in CSS, and set `stroke` on the SVG `.main-path` Drawflow drew between the two nodes. Drawflow stores connection paths in `editor.drawflow.drawflow.Home.data.<id>.outputs.<port>.connections` — the SVG element is queryable via `document.querySelectorAll('.connection.node_in_node-X.node_out_node-Y .main-path')`.
- **IMPLEMENT**: Apply the same tint pass at the end of `hydrateFromCanonical` (after all `addConnection` calls) by walking the existing connections.
- **PATTERN**: Mirror Drawflow's selector format — confirm by inspecting the SVG `<path>` in DevTools after a manual connection.
- **GOTCHA**: Drawflow recreates `.main-path` on reroute. Re-apply tint in `connectionCreated` and any reroute event if one exists; otherwise observe and add an event hook.
- **VALIDATE**: Connect `ok` → `in` between two pipeline nodes; line is green. Connect `error` → `in`; line is red. Connect `true` → `in`; line is green; `false` → `in` is orange.

### Task 3.4 — UPDATE src/voice_commander/web/static/builder.js: port name labels in config rail

- **IMPLEMENT**: In `renderConfigRail` (lines 486–577), where ports are listed, render each port name with a small color swatch (`<span class="vc-port-swatch" data-port-name="ok"></span>`). Define `.vc-port-swatch` in `builder.css` using the same color map.
- **PATTERN**: Existing config-rail rendering uses `document.createElement` + `appendChild`. Match that style.
- **GOTCHA**: Keep the swatch decorative — it must not be confusable with a real connectable port.
- **VALIDATE**: Select any node; the right rail lists ports each with a colored dot matching the canvas.

### Task 4.1 — UPDATE src/voice_commander/web/static/builder.js: collapsible palette sections + filter

- **IMPLEMENT**: Wrap each palette section in a `<details open>` element. For sections > 8 items (Workflows, Pipeline at full registry size), default closed. Add a `<input type="search" id="palette-filter" placeholder="filter…">` at the top of `#builder-palette`. On `input`, hide non-matching buttons across all sections.
- **PATTERN**: `<details>` is native HTML — no JS needed for collapse. Filter is a single event handler walking `paletteEl.querySelectorAll('button')`.
- **GOTCHA**: Filter must respect category — e.g. typing "press" should still show the section header for Pipeline so the user knows where the match lives.
- **VALIDATE**: Type "press" in the filter; only matching buttons remain visible; Pipeline header still visible. Clear filter; everything reappears.

### Task 4.2 — UPDATE src/voice_commander/web/static/builder.css: palette button category indicator

- **IMPLEMENT**: Each palette button gets a 6 px colored left border matching its category. CSS rule per category, e.g. `#builder-palette .palette-pipeline { border-left: 6px solid #d97706; }`.
- **IMPLEMENT**: Update `renderPaletteSection` in builder.js (line 138) to add a `palette-<category>` class on each button, derived from `refPrefix`.
- **VALIDATE**: Each palette section's buttons display the correct color stripe.

### Task 5.1 — RUN regression checks

- **IMPLEMENT**: N/A — execution only.
- **VALIDATE**: Run all four:
  ```
  ruff check src tests
  ruff format --check src tests
  pytest tests/unit -q
  pytest tests/integration -q
  ```
  Then start the daemon (`./start.bat` or `python -m voice_commander`), open `/page/builder`, and walk through the manual checks in the Validation section below.

---

## TESTING STRATEGY

### Unit Tests

No new Python unit tests required for the visual layer (CSS / JS only). If Task 1.1 chooses backend normalisation, add a unit test in `tests/unit/test_tool_schema.py` (create if absent) that asserts `describe_tool_for_builder(entry).args` is a list of typed objects.

### Integration Tests

Extend `tests/integration/test_builder_routes.py` with:

- `test_palette_pipeline_args_shape_is_iterable` — palette pipeline entries can be iterated like commands.
- `test_palette_includes_known_primitives` — assert `press`, `click`, `type`, `focus`, `open` all appear in `palette["pipeline"]`. This catches regressions where the registrar filter changes or a primitive gets deregistered (this would have caught the close_window collision earlier in this session).

### Edge Cases

- A graph saved before this redesign hydrates cleanly (no missing port-name attributes break wiring).
- A pipeline primitive with zero args (e.g. `wait`, `read_clipboard`) drags onto canvas without errors.
- A `value.input` node on a graph with zero inputs renders with zero output ports and does not crash.
- A `control.branch` with `cond` wired from a data port renders the `true` / `false` outputs in the correct positions and colors after hydration.
- A graph with a `control.foreach` whose `item` port feeds a downstream node hydrates with the cyan rhombus tint preserved on the connection.
- Switching the daemon's `kind` selector mid-session (command ↔ workflow) does not strand orphan nodes from the prior kind.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness.

### Level 1: Syntax & Style

```
ruff check src tests
ruff format --check src tests
```

### Level 2: Unit Tests

```
pytest tests/unit -q
```

Baseline before this work: 765 passed, 7 skipped. Must remain ≥ 765 passing (more if you add unit tests).

### Level 3: Integration Tests

```
pytest tests/integration -q
```

Specifically include the new `test_palette_pipeline_args_shape_is_iterable` and `test_palette_includes_known_primitives` cases.

### Level 4: Manual Validation

Run the daemon with `./start.bat` (or `python -m voice_commander` on POSIX-equivalent stacks). Open `http://127.0.0.1:8765/page/builder?kind=command` and confirm:

1. Palette renders all five sections (Pipeline, Commands, Workflows, Control, Value) with collapsible headers and a filter input.
2. Each palette button shows a colored left-border stripe matching its category gradient.
3. Clicking each pipeline primitive (`press`, `click`, `type`, `focus`, `open`, `wait`, `read_clipboard`, `get_active_window_title`, `get_cursor_pos`, `scroll`) drops a node on the canvas. Each gets the amber/bronze gradient and rounded-rect shape.
4. Clicking a command (`close_window`, `next_tab`, etc.) drops a teal/cyan pill-shaped node.
5. Clicking a workflow drops a violet/indigo node with the chevron banner.
6. Clicking `control.branch` drops a rose/maroon node with hexagonal accent on the right; its outputs are visibly `true` (green square) and `false` (orange square).
7. Clicking `control.foreach` drops a rose/maroon node; outputs are `item` (cyan rhombus) and `after` (cyan rhombus).
8. Clicking each `value.*` drops an emerald/forest node with parallelogram tint.
9. Connecting `ok` → `in` between two nodes draws a green line; `error` → `in` draws red; `true`/`false`/`item`/`after` use their port colors.
10. Hovering any port shows its name as a label.
11. Selecting a node populates the right rail; the canonical id is shown there (and only there + the node tooltip — not on the node body).
12. Saving a graph then reloading the page hydrates all nodes with correct colors, shapes, port tints, and connection tints.
13. Open an existing graph from `commands.json` (e.g. `close_window`); visually verify it renders correctly under the new scheme.
14. Toggle a graph's enabled state via the existing endpoint; daemon hot-reloads; primitives still draggable.

### Level 5: Additional Validation (Optional)

- Use `playwright-cli` skill to script a smoke test that programmatically clicks each palette primitive and asserts a corresponding canvas DOM node appears. Run against `http://127.0.0.1:8765/page/builder?kind=command`.

---

## ACCEPTANCE CRITERIA

- [ ] Pipeline primitives (`press`, `click`, `type`, `focus`, `open`, all others in the `internal=true & enabled=true & origin=primitive` set) drop on the canvas when clicked from the palette.
- [ ] Each of the five node categories renders with a distinct gradient + shape per the Phase 2 spec.
- [ ] Each port type (`in`, `ok`, `error`, `true`, `false`, `item`, `after`, data fallback) renders in the specified color/shape.
- [ ] Connection lines tint to match the source port color.
- [ ] Canonical node IDs no longer render on the node body; they remain visible in the right rail and the node hover tooltip.
- [ ] Title text contrast ≥ 7:1 against node background per WCAG AAA on default body sizes.
- [ ] Palette has a search filter and collapsible category sections.
- [ ] Existing graphs in `commands.json` and `workflows.json` open in the builder with no visual regressions or broken wires.
- [ ] All Level 1–3 validation commands pass.
- [ ] Manual checks 1–14 above all pass.
- [ ] `commands.json.bak` (or any other side artifact) is not introduced; the canonical graph schema is untouched.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order across phases 1 → 5.
- [ ] Each task's `VALIDATE` step passed before moving on.
- [ ] All validation commands (`ruff check`, `ruff format --check`, `pytest unit`, `pytest integration`) pass with zero errors.
- [ ] Manual validation in a real browser confirms every check 1–14.
- [ ] No regressions in existing functionality — open at least three pre-existing graphs and confirm they work.
- [ ] An ADR is filed at `docs/decisions/0069-builder-visual-taxonomy.md` (or next available number) summarising the per-category palette and port-coding decisions; row added to `docs/agents/technical-decisions.md`.
- [ ] Per-PR issue rule observed: file one GitHub issue per phase before opening PRs; close issues via `Closes #N` in PR bodies.
- [ ] CLAUDE.md root entry refreshed if any of the architecture-at-a-glance details change (none expected — this is pure UI).

---

## NOTES

**Why frontend normalisation in Task 1.1 over backend.** The backend `args` dict shape is referenced by other consumers (LLM tool schema generation, validators). Changing it means updating multiple call sites and re-checking the generated OpenAI tool schema. The frontend-only fix is one helper, two call sites, and zero risk to the LLM router. If a future cleanup wave wants to standardise to array shape, do it deliberately as its own change — not folded into a UI redesign.

**Why move inline styles into a dedicated stylesheet.** ADR 0022 keeps us off React/SPA toolchains, but it does not preclude separating CSS from HTML — that is just basic hygiene. The current inline `<style>` block is short enough to be tolerable but is about to grow significantly. A dedicated file makes review diffs clean, enables browser caching, and lets future contributors find styling rules without grepping templates.

**Why color/shape coding instead of icons.** Icons require an icon font or SVG sprite, which adds asset weight and toolchain complexity. Color + shape achieves the same legibility goal with pure CSS and survives without a network round-trip.

**Drawflow shape constraints.** Drawflow assumes rectangular nodes for hit-testing, port placement, and connection routing. Apply shape modifiers (parallelogram tilt, hexagonal accent, chevron banner) via `::before` / `::after` pseudo-elements on a transparent background overlay — never via `clip-path` on the parent `.drawflow-node`. If during Phase 2 you find a shape modifier breaks port grabs, fall back to a 6 px accent stripe on one edge instead.

**Bug discovered earlier this session.** A separate `DuplicateToolError: 'close_window'` was found and fixed in `src/voice_commander/commands/registrar.py` (added `_drop_shadowed_primitive` helper). That fix is on the working tree and not yet committed; it is unrelated to this redesign and should be committed as its own change before this redesign work begins, so the daemon can actually start during validation.

**Confidence score for one-pass implementation: 8/10.** Visual work has natural ambiguity around shape modifiers and Drawflow's CSS quirks, but the bug fix and CSS work are well-scoped and the runtime/schema changes are zero. Most of the residual risk is in Task 3.3 (connection line tinting) where Drawflow's internal SVG handling may require a small reverse-engineering pass. Budget one extra hour there.
