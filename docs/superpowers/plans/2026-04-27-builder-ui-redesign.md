# Plan: Builder UI Visual + Interaction Overhaul

**Date:** 2026-04-27  
**Branch:** `archon/task-feat-builder-ui-redesign`  
**Spec:** N/A (task defined in branch name + task description)

---

## Summary

Visual and interaction overhaul of the Builder UI (`/page/builder`). Three independent changes:

1. **Per-category gradients/shapes** — each node category (pipeline, command, workflow, control, value) gets a distinct gradient background and border color so users can instantly distinguish node types on a dense canvas.
2. **Port semantic colors** — ports named `ok`/`true` render green, `error`/`false` render red, `in` renders sky-blue, data/arg ports render neutral. Applied in both the on-canvas node header area (via CSS classes added in `addNodeToCanvas`) and in the config-rail port list.
3. **Primitive drag bug fix** — palette items currently use `onclick` with a random canvas position. Replace with HTML5 `draggable` + `ondragstart` on buttons and `ondragover`/`ondrop` on the canvas wrapper so users can drag-drop to exact positions. `onclick` retained as fallback (center-ish drop).

---

## Files to Change

| File | Action | What |
|---|---|---|
| `src/voice_commander/web/templates/page_builder.html` | UPDATE | Per-category CSS gradient rules + port-semantic CSS classes |
| `src/voice_commander/web/static/builder.js` | UPDATE | Drag-and-drop, port-color class helpers |

No new files. No Python changes. No schema changes.

---

## Step-by-Step Tasks

### Task 1 — CSS: per-category gradients + port semantics (`page_builder.html`)

Replace the existing `<style>` block with expanded rules:

**Category rules** (one gradient per category prefix):

| Category | Gradient from | Gradient to | Border color |
|---|---|---|---|
| `pipeline` | `#0c1e28` | `#0f2d3d` | `#0e7490` (teal-700) |
| `command` | `#0d1b2a` | `#0f2440` | `#1d4ed8` (blue-700) |
| `workflow` | `#1a0533` | `#2a0e4e` | `#7c3aed` (violet-600) |
| `control` | `#1c0b30` | `#2e1050` | `#a855f7` (purple-500) |
| `value` | `#0b1f10` | `#0f2e18` | `#059669` (emerald-600) |

Shape: all keep `border-radius: 6px`.  
Selected state: bright white border `#f9fafb`.

**Port-semantic CSS** (applied via `vc-port-ok`, `vc-port-error`, etc.):

```css
.vc-port-ok, .vc-port-true  { color: #4ade80; }  /* green-400 */
.vc-port-error, .vc-port-false { color: #f87171; }  /* red-400 */
.vc-port-in  { color: #38bdf8; }  /* sky-400 */
.vc-port-data { color: #94a3b8; }  /* slate-400 — arg/data ports */
```

### Task 2 — JS: port color helper + config rail update (`builder.js`)

Add a `portColorClass(name, direction)` helper that maps port name → CSS class:

```js
function portColorClass(name, direction) {
  if (name === 'ok' || name === 'true')    return 'vc-port-ok';
  if (name === 'error' || name === 'false') return 'vc-port-error';
  if (name === 'in')                        return 'vc-port-in';
  return 'vc-port-data';
}
```

In `renderConfigRail`, replace the hard-coded `text-teal-400` / `text-sky-400` on port rows with `portColorClass(name, direction)`.

### Task 3 — JS: drag-and-drop from palette (`builder.js`)

In `renderPaletteSection`, add to each button:
```js
btn.draggable = true;
btn.ondragstart = (e) => {
  e.dataTransfer.setData('application/vc-ref', ref);
  e.dataTransfer.effectAllowed = 'copy';
};
```

After `editor.start()`, attach canvas drop handlers:
```js
canvasEl.addEventListener('dragover', (e) => {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'copy';
});
canvasEl.addEventListener('drop', (e) => {
  e.preventDefault();
  const ref = e.dataTransfer.getData('application/vc-ref');
  if (!ref) return;
  const rect = canvasEl.getBoundingClientRect();
  const x = (e.clientX - rect.left - editor.canvas_x) / editor.zoom;
  const y = (e.clientY - rect.top  - editor.canvas_y) / editor.zoom;
  addNodeToCanvas(ref, x, y);
});
```

`onclick` on palette buttons is retained unchanged (click still adds at random position — existing behavior, no regression).

---

## Validation Commands

```bash
# Python tests (the only test suite in this repo)
uv run pytest tests/integration/test_builder_routes.py -v

# Full suite
uv run pytest -x -q

# JS: no build step — visual validation must be done in browser
```

---

## Acceptance Criteria

- [ ] Each node category has a distinct gradient background visible on canvas
- [ ] `ok`/`true` port labels in config rail are green; `error`/`false` are red; `in` is sky-blue
- [ ] Dragging a palette item and dropping on canvas places the node at drop coordinates
- [ ] Clicking a palette item still places node (existing behavior)
- [ ] All existing Python tests pass

---

## Assessment

| Metric | Value |
|---|---|
| Complexity | Low — CSS + two JS helpers + event listeners |
| Risk | Low — no Python changes, no schema changes |
| Confidence | High — all changes are self-contained in two files |
