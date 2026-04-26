# ADR 0066 — Perception primitives layer

**Date:** 2026-04-26
**Status:** Accepted

## Context

The graph runtime (ADR 0064) introduces branch nodes that route execution based on the result of a condition. A branch node is only useful if there is something to read — some observable state from the desktop environment that can be compared to a predicate. Without read-side primitives, the branch node has no inputs, and the entire data-wire model is limited to passing window handles between action nodes.

The primary motivating use cases are: branching on whether a clipboard string matches a pattern, branching on whether the currently active window title contains a keyword, and conditional execution based on cursor position (e.g. "if cursor is on the right monitor"). A secondary use case — useful for scripting and accessibility — is optical character recognition of a screen region.

Leaving perception out of v1 was considered but rejected, because it removes the main reason for introducing typed data wires at all. Without perception primitives, the graph runtime is strictly less expressive than the existing linear `steps[]` model.

## Decision

Ship four new pipeline primitives in the `[perception]` tool group:

- `read_clipboard() -> str` — returns the current text clipboard content.
- `get_active_window_title() -> str` — returns the title string of the current foreground window.
- `get_cursor_pos() -> tuple[int, int]` — returns `(x, y)` in screen coordinates.
- `ocr_region(x: int, y: int, w: int, h: int) -> str` — captures the given screen region and returns recognised text.

`ocr_region` takes four separate `int` arguments (not a dict or tuple) for LLM-friendliness: small models reliably pass four named integers; they are less reliable with nested structures.

OCR engine selection: `winrt` screen-capture + Windows OCR engine (via `winrt-*` packages, available on Windows 10 1803+) is the preferred backend. Tesseract (`pytesseract` + `tesseract` binary on `PATH`) is the fallback. A new `[perception]` section in `config.toml` exposes `ocr_engine = "winrt" | "tesseract" | "auto"` (default `"auto"`: try `winrt`, fall back to Tesseract).

All four primitives register via `@tool` with `llm_visible = True`; they are accessible both to the LLM (for direct utterance routing) and to graph branch nodes (via data wires).

## Consequences

- `winrt-*` packages are optional; the package is listed in `[project.optional-dependencies.perception]` in `pyproject.toml`. Tesseract is the fallback and requires a system install.
- A new `[perception]` section in `config.toml` controls engine preference; the key is documented in `README.md`.
- `ocr_region` takes `(x, y, w, h)` as four separate `int` args to maximise LLM reliability; callers in graph branch nodes pass them via typed input wires.
- The four primitives expand the LLM's tool list; combined with the `llm_visible` flag (ADR 0067), perception-only helper graphs can be hidden from the LLM if the tool count becomes a concern.
- Perception primitives are pure read operations; they have no `settle_ms` and no side effects on the desktop.
