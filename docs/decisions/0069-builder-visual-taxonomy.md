# ADR 0069 — Builder Visual Taxonomy + Primitive Drag Bug Fix

**Date**: 2026-04-27
**Status**: Accepted

---

## Context

The Drawflow-based node graph builder at `/page/builder` had two problems:

1. **Broken primitive interaction.** Clicking any pipeline primitive in the palette
   (e.g. `press`, `click`, `type`) threw a `TypeError` in `addNodeToCanvas`.
   Root cause: `describe_tool_for_builder` emits `args` as a **dict**
   `{name: {type, description, required}}` while commands/workflows emit `inputs` as
   an **array** `[{name, type, required}]`. The JS iterated with `for...of args`,
   which is not iterable on a plain object.

2. **Visual illegibility.** All five node categories (pipeline, command, workflow,
   control, value) rendered identically — same cyan-on-dark-gray box, no shape
   differentiation, no port semantic coloring. Wiring intent was invisible.

---

## Decision

### Bug fix — frontend normalisation (Task 1.1)

Add a `normalizeArgs(rawArgs)` helper in `builder.js` that accepts either the dict
shape (pipeline) or array shape (commands/workflows) and returns a uniform array
`[{name, type, description, required}]`. Apply at every call site that reads
`desc.args || desc.inputs`.

**Why frontend, not backend**: `describe_tool_for_builder` is also consumed by the
LLM tool-schema generator and other callers. Changing the dict→array shape there
would require auditing all downstream consumers and risks breaking the OpenAI schema
path. A single frontend helper isolates the fix with zero backend risk.

### Visual taxonomy — per-category gradients + shapes (Phase 2)

A dedicated stylesheet `builder.css` (replacing inline `<style>`) defines five dark
gradient backgrounds plus shape modifiers via `::before`/`::after` pseudo-elements:

| Category | Gradient | Shape modifier |
|----------|----------|----------------|
| Pipeline | amber → bronze `#d97706 → #92400e` | Standard `border-radius: 6px` |
| Command | teal → deep-cyan `#0d9488 → #134e4a` | Pill `border-radius: 14px` |
| Workflow | violet → indigo `#7c3aed → #3730a3` | `▶▶▶` banner via `::before` |
| Control | rose → maroon `#e11d48 → #881337` | Right-edge chevron via `::after` |
| Value | emerald → forest `#059669 → #064e3b` | Parallelogram tint via `::before` |

Shape modifiers never use `clip-path` on the parent `.drawflow-node` (would cut off
ports). All shapes are decorative overlays; the underlying rectangle handles
hit-testing.

Canonical node IDs are removed from the node body and surfaced only in the right
config rail and the node's `title=""` hover tooltip.

### Port semantics — color + shape via `data-port-name` (Phase 3)

After each `editor.addNode()` call, a `queueMicrotask` pass stamps every `.input_N`
and `.output_N` DOM element with `data-port-name="<port>"`. CSS attribute selectors
then apply per-port colors and shapes:

| Port | Color | Shape |
|------|-------|-------|
| `in` | #9ca3af (neutral grey) | Square |
| `ok` | #22c55e (green) | Circle |
| `error` | #ef4444 (red) | Circle |
| `true` | #22c55e (green) | Square |
| `false` | #f97316 (orange) | Square |
| `item`, `after` | #06b6d4 (cyan) | Rhombus (rotate 45°) |
| data / other | #3b82f6 (blue) | Circle |

Connection lines are tinted to match the source port's color via Drawflow's
`connectionCreated` / `connectionRemoved` events + a `tintAllConnections()` pass that
sets `stroke` on each SVG `.main-path`.

### Palette polish — collapsible sections + search (Phase 4)

Palette sections wrapped in native `<details>` / `<summary>` elements (no JS for
collapse). A `<input type="search" id="palette-filter">` filters buttons across all
sections. Each button gets a `palette-<category>` class with a 4 px colored
left-border strip.

---

## Consequences

- **Fixed**: Pipeline primitives (`press`, `click`, `type`, `focus`, `open`, etc.)
  can now be dropped onto the canvas.
- **Added**: `builder.css` separates visual rules from the template, enables
  browser caching, and makes styling diffs clean.
- **Preserved**: Canonical graph JSON schema, runtime, registrar, and LLM tool
  schema are untouched.
- **Preserved**: `describe_tool_for_builder` dict shape — no backend callers changed.
- **Tests added**: `test_palette_pipeline_args_shape_is_iterable`,
  `test_palette_includes_known_primitives` in `tests/integration/test_builder_routes.py`.
