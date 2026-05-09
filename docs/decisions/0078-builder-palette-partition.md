# ADR 0078 — Builder Palette Pipeline / Perception Partition + system Flag

**Status:** Accepted
**Date:** 2026-05-09
**Extends:** ADR 0069 (Builder visual taxonomy), ADR 0071 (Builder React SPA), ADR 0075 (Primitives-only MVP), ADR 0066 (Perception primitives layer)

## Context

The Builder palette had four compounding problems:

1. **Mixed pipeline section.** The palette's Pipeline section listed all primitives together — action primitives (`click`, `press`) alongside perception primitives (`read_clipboard`, `ocr_region`) — creating visual noise. A separate Perception section did exist in the frontend, but it was populated from a hardcoded `PERCEPTION_SCHEMAS` constant using `perception.X` refs. The backend graph runtime knows no `perception.*` namespace; every tool executes via `pipeline.<name>`. Dragging from the Perception section therefore produced graphs with broken refs that failed at runtime.

2. **`internal` flag incorrectly gating the palette.** The backend `/graph/palette` filter required `e.internal == True` to surface a primitive. The `internal` flag was intended to mean "hide from LLM tool list"; it was never meant to gatekeep the Builder UI. Because `press` and `ocr_region` are LLM-visible (`internal=false`), they were silently absent from the palette. Symptom: "why is `press` not a primitive in the builder?"

3. **`no_match` leaking into user-facing surfaces.** The only filter excluding entries from the palette and `/page/primitives` was `system=True`, but `no_match` had never been marked `system=true` in `primitives.toml`. As a result, `no_match` — the LLM-only escape hatch — appeared in the Builder palette and the Primitives page, where it has no valid use.

4. **User-authored commands leaking into `/page/primitives` and `/api/tools`.** Both endpoints filtered `system==False` but did not require `origin=="primitive"`, so user-authored command and workflow entries mixed with the engine primitives on those surfaces.

## Decision

- **Backend `/graph/palette` partition.** The endpoint returns two separate arrays: `pipeline` (action primitives) and `perception` (observation primitives). The split is name-based via a new frozenset `tool_schema.PERCEPTION_PRIMITIVE_NAMES = {"read_clipboard", "get_active_window_title", "get_cursor_pos", "ocr_region"}`. Both arrays emit the same canonical `pipeline.<name>` ref — there is one registry path for all primitives, perception included.

- **Backend palette filter.** Changed from `e.internal == True` to `e.origin == "primitive" and e.enabled and not e.system`. The `internal` flag now exclusively governs LLM tool-list visibility; it no longer controls Builder UI membership.

- **`no_match` marked `system = true`.** The `no_match` entry in `primitives.toml` gains `system = true`. It remains `internal = false` (i.e., still LLM-visible) but is hidden from the palette, Primitives page, and `/api/tools`.

- **`/page/primitives` and `/api/tools` filter hardened.** Both surfaces now require `origin == "primitive" and not system`, ensuring user-authored commands and workflows never appear alongside engine primitives.

- **Frontend palette section renamed "Primitives".** The hardcoded `PERCEPTION_SCHEMAS` constant is removed. The Perception section reads `data.perception` from the backend response and primes `schemaStore` using the canonical `pipeline.<name>` refs returned by the API. `refToNodeType()` in `graphSerialize.ts` maps the four perception primitive refs to the `perception` React Flow node type for visual treatment while keeping the canonical ref intact. `PerceptionNode.tsx` label map is keyed by `pipeline.<name>` refs to match.

## Alternatives considered

- **Separate `perception.X` ref namespace** — rejected. Duplicate registry entries for identical code, two execution paths to maintain, and refs that break any graph authored before the rename.
- **Exclude perception nodes from the palette entirely** — rejected. Perception primitives are valid first-class nodes in graphs (e.g., a branch reads the clipboard then types a reply); users must be able to drag them from the palette.

## Consequences

- **Single source of truth:** the backend partitions; the frontend renders. No frontend constant can diverge from the registry.
- **`internal` and `system` are orthogonal.** `internal = true` means "hide from LLM tool list." `system = true` means "hide from all user-facing surfaces (palette, Primitives page, `/api/tools`)." A tool may be any combination of the two.
- **Adding a new perception primitive** requires: implement the function, add a `.toml` entry, add the name to `PERCEPTION_PRIMITIVE_NAMES`. No frontend constant to update.
- **`press` and `ocr_region`** now appear in the Builder palette as expected.

## Implementation pointers

- `src/voice_commander/web/builder.py` — `palette()` endpoint partition logic.
- `src/voice_commander/web/app.py` — `/page/primitives` + `/api/tools` filters (`origin == "primitive" and not system`).
- `src/voice_commander/tool_schema.py` — `PERCEPTION_PRIMITIVE_NAMES` frozenset.
- `src/voice_commander/tools/primitives.toml` — `no_match` block: `system = true`.
- `web/builder-ui/src/palette/Palette.tsx` — section renaming, `PERCEPTION_SCHEMAS` removal, schema priming from `data.perception`.
- `web/builder-ui/src/lib/graphSerialize.ts` — `refToNodeType()` perception-name mapping.
- `web/builder-ui/src/canvas/nodes/PerceptionNode.tsx` — label map keyed by `pipeline.<name>` refs.
