# ADR 0080 — Builder Graph Settings Panel + Synonyms Editor

**Status:** Accepted
**Date:** 2026-05-09
**Extends:** ADR 0071 (Builder React SPA), ADR 0076 (Registry-aware VerbRouter)

## Context

The VerbRouter has matched user-authored command/workflow `synonyms` (alongside their canonical names) since ADR 0076 — the very feature that lets `paste` also fire on noisy Whisper transcriptions like `P.A.C.T.` (see `verb_router.py:117-168`, which names the paste/PACT case in its docstring). The canonical graph schema persists `synonyms: tuple[str, ...]` per graph, the on-disk JSON has the field on every entry, and the `POST /graph/{name}` save endpoint already round-trips it through `parse_graph` / `serialise_graph`.

What was missing: the React Builder SPA had no UI to edit `synonyms`. Users hitting Whisper mistranscriptions had to hand-edit `commands.json`, restart-friendly behaviour notwithstanding. The legacy HTMX `/page/primitives` surface (ADR 0022) already provides a working phrase editor for TOML-backed primitives (`app.py:281-354`), so that side was covered — but graph commands and workflows had no equivalent.

The TypeScript `Graph` interface (`web/builder-ui/src/types/graph.ts`) omitted `synonyms` entirely. The `graphStore` blindly spread the backend payload through `graphMeta`, so the field actually round-tripped invisibly — it just had no editor wired to it.

The Builder's `PropertiesPane` (`web/builder-ui/src/properties/PropertiesPane.tsx`) had an unused empty state ("Select a node to edit its properties.") whenever no node was selected. That dead screen real estate was a natural home for graph-level metadata.

## Decision

- **Repurpose the PropertiesPane empty state as a graph-settings editor.** When `selectedNodeId == null`, render a new `GraphSettingsPanel` instead of the placeholder. When a node is selected, the existing `KwargsForm` continues to render unchanged.

- **`GraphSettingsPanel` edits `description` + `synonyms` only.** Other graph metadata (rename, LLM-visible toggle, save) already lives in the Toolbar and is untouched. The panel also surfaces a "Edit primitive phrases →" link that opens `/page/primitives` in a new tab, so authors discover the existing TOML editor for the other half of the registry.

- **`SynonymsEditor` is a chip-style tag input.** Enter or comma commits the trailing draft; Backspace on an empty draft removes the last chip; clicking ✕ removes a specific chip. Empty / whitespace-only / case-insensitive duplicates are silently ignored. Phrases are stored verbatim — match logic is already case-insensitive via `_normalize_spoken` in `verb_router.py`, so there is no need to lowercase at write time.

- **Two new graphStore mutations.** `setSynonyms(string[])` and `setDescription(string)` shallow-merge into `graphMeta` and flip `dirty: true`, mirroring `toggleLlmVisible`. The Toolbar's existing Save button drives persistence; no toolbar changes.

- **Legacy graphs without `synonyms` normalise to `[]` on load.** The `Graph` TS interface marks `synonyms?: string[]` optional to reflect the wire format. `graphStore.load()` then coerces missing values to `[]` so the editor always renders against an array.

- **No backend changes.** The save/load round-trip already supports `synonyms`; only the frontend needed the field plumbed through and an editor mounted.

## Alternatives considered

- **Inline phrases editor in the Toolbar.** Rejected. Toolbar is already crowded with name input, dirty marker, LLM toggle, Prompt button, and Save. A chip input grows vertically and would push the canvas down on long synonym lists.

- **Modal dialog opened from a "Phrases" button.** Rejected. Authors typically iterate phrases while watching the canvas (e.g., reviewing what the command actually does); a modal would obstruct that flow.

- **Dedicated right-side metadata panel separate from PropertiesPane.** Rejected. Adds layout complexity for a feature that fits naturally in dead space the user is already trained to look at when nothing is selected.

- **Add a per-node "this primitive's phrases" editor when a primitive node is selected in the canvas.** Rejected for now. Primitive phrases are a global property of the registry, not a property of the node-in-this-graph; editing them from inside a single graph would be misleading. The link to `/page/primitives` covers that need.

## Consequences

- **Author can fix Whisper mistranscriptions without leaving the Builder.** Open the graph, click empty canvas, type the bad transcription as a phrase, hit Save.

- **`synonyms` is now a fully visible, edited field in the round-trip.** Anyone debugging matching behaviour can see what's registered without tailing JSON.

- **The PropertiesPane has a permanent default view.** Empty states are now a useful surface, not a placeholder.

- **Legacy graphs (those without the `synonyms` key) keep loading.** `?? []` normalisation in `graphStore.load()` plus the `?` on the type makes the field forward-compatible without a schema bump.

- **No backend deploy needed.** Pure frontend change; the registry, store, and validator already understood `synonyms`.

## Implementation pointers

- `web/builder-ui/src/types/graph.ts` — `synonyms?: string[]` on `Graph`.
- `web/builder-ui/src/store/graphStore.ts` — `setSynonyms` / `setDescription` mutations; `load()` normalises missing `synonyms` to `[]`; `makeBlankGraphMeta()` seeds `synonyms: []`.
- `web/builder-ui/src/properties/SynonymsEditor.tsx` — chip-input component.
- `web/builder-ui/src/properties/GraphSettingsPanel.tsx` — empty-state panel.
- `web/builder-ui/src/properties/PropertiesPane.tsx` — empty-state wiring.
- `web/builder-ui/tests/unit/graphStore.test.ts` — tests for new mutations + legacy normalisation.
- `web/builder-ui/tests/unit/components/SynonymsEditor.test.tsx` — chip-input behaviour tests.
- `web/builder-ui/tests/unit/components/GraphSettingsPanel.test.tsx` — panel render tests.
