# ADR 0047: Char-Sheet Grid Format with Sidecar TOML

**Date:** 2026-04-21
**Status:** Accepted
**References:** ADR 0021 (sidecar TOML per tool)

## Context

Sprite animations need to be regeneratable without manual frame splicing. The art pipeline must be "generate a grid PNG + fill in a TOML" — no code changes needed for new art.

## Decision

Single `charsheet.png` (N columns × M rows) with a sidecar `charsheet.toml` mapping state names to rows. Validator at startup confirms every state has a row and every row fits within the PNG bounds.

## Consequences

- Art regeneration is a single image generation step + TOML edit.
- Hot-reload watches both files for mtime changes.
- Unknown TOML keys warn (not fail) for forward compatibility.
