# ADR 0056 — Schema-driven kwargs form for the web UI

**Status**: Accepted
**Date**: 2026-04-24
**Issue**: #44 (schema-driven kwargs form replaces raw JSON input)

## Context

Before this ADR, the admin UI accepted command `kwargs` as a raw JSON textarea.
Users had to know the exact argument names and types for each primitive, with no
in-form guidance. Any typo produced a runtime dispatch error, not an inline
validation message.

## Decision

Introduce a schema-driven guided kwargs form with three interlocked choices:

1. **`ToolEntry.args_meta` as web-UI metadata source of truth** — `args_meta`
   is a `dict[str, ArgMetadata]` populated from TOML `[tools.<name>.args.*]`
   sections during `bind_metadata` / `reload_metadata`. It is stored on the
   live `ToolEntry` so the web UI reads it from the already-loaded registry
   without a second TOML parse per request.

2. **Dedicated `GET /command/kwargs-form` HTMX fragment endpoint** — when the
   user changes the primitive `<select>`, HTMX fires a `GET /command/kwargs-form
   ?primitive=<name>&mode=guided` request. The server renders `_kwargs_fields.html`
   with the matching `args_meta` and swaps only the `#kwargs-section` div. This
   keeps the remaining form state intact and avoids a full page reload.

3. **`kwargs_mode=guided|advanced` POST field for backwards compatibility** —
   the `POST /command/{name}` handler reads `kwargs_mode` from the form. When
   `kwargs_mode == "guided"` and `args_meta` is non-empty, individual
   `kwarg_<name>` fields are parsed and type-coerced (integer via `int()`,
   boolean via checkbox presence-check). When `kwargs_mode == "advanced"` or
   `args_meta` is empty (unknown primitive), the handler falls back to parsing
   `kwargs_json` as a JSON object. This ensures existing API consumers and
   primitives without TOML `[args]` sections continue to work unchanged.

## Rationale

- **`args_meta` on `ToolEntry`** (not re-fetched per request) keeps the hot
  path free of TOML I/O. The registry is already loaded and reload-guarded;
  piggybacking on it is correct.
- **Dedicated kwargs-form endpoint** produces minimal HTML (no full-page
  template), fits naturally into HTMX's `hx-get` / `hx-target` / `hx-swap`
  pattern, and decouples the schema display from the save path.
- **Two modes (`guided` / `advanced`)** preserve backwards compatibility for:
  (a) primitives that pre-date TOML `[args]` sections;
  (b) direct API consumers that POST raw JSON;
  (c) users who prefer a free-form JSON editor for complex nested kwargs.

## Alternatives Rejected

- **Fetch `args_meta` from TOML on every form request** — adds disk I/O on the
  hot UI path; `args_meta` is already current on the `ToolEntry` after reload.
- **Single mode (always guided)** — breaks existing commands with no TOML `[args]`
  section; forces a one-time migration of all primitives before the feature ships.
- **Inline JSON schema annotation in the form** (no separate endpoint) — would
  require shipping the entire command form HTML on every primitive change;
  HTMX fragment swap is cheaper and keeps scope narrow.

## Consequences

- Every new primitive that exposes kwargs to users should add a TOML `[args]`
  section so the guided form renders correctly; the fallback textarea remains
  available if the section is absent.
- `_parse_command_form` now accepts `kwargs_mode`, `kwarg_fields`, and
  `args_meta` parameters. Callers that do not supply `args_meta` or pass
  `kwargs_mode="advanced"` get the previous JSON-parse behaviour unchanged.
- The web admin route count increased from 6 to 7 (`GET /command/kwargs-form`
  added). `docs/architecture.md` §6 updated accordingly.
