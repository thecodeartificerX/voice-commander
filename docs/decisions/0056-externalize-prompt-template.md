# ADR 0056 — Externalize system prompt template to text file

**Status**: Accepted
**Date**: 2026-04-24
**Supersedes**: ADR 0044 deferral ("Externalizing prompt to `.txt` file — deferred")

## Context

The system prompt is a Python string constant (`_SYSTEM_PROMPT_TEMPLATE` in `llm_router.py:20-43`) with a single `{default_browser}` placeholder resolved at `LLMRouter.__init__`. Editing the prompt requires modifying Python source and restarting the daemon. There is no way to see the full composed prompt (system text + tools JSON) from the web UI, and no hot-reload path for prompt iteration.

ADR 0044 previously deferred externalization, noting "unit tests would require filesystem reads." This ADR supersedes that deferral — the benefit of UI-driven editing and hot-reload now outweighs the minor test complexity.

## Decision

Move the system prompt template from a Python constant in `llm_router.py` to a standalone text file at `src/voice_commander/prompt_template.txt`. The `LLMRouter` reads this file at startup and on demand via `reload_prompt()`. A fallback constant (`_FALLBACK_TEMPLATE`) preserves daemon bootability if the file is missing.

Key changes:
1. `_load_template()` reads `prompt_template.txt`, falls back to `_FALLBACK_TEMPLATE` with a warning log.
2. `reload_prompt()` re-reads the file and rebuilds `self._system_prompt` under `reload_lock`.
3. `composed_prompt_data()` exposes structured prompt data (raw template, resolved text, placeholders, tools array) for the web UI prompt inspector.
4. The web layer (`web/prompt.py`) provides inspect/edit/save endpoints; saving writes the file and triggers `reload_prompt()`.
5. Placeholder validation on save rejects templates missing `{default_browser}`.

## Rationale

- **Text file is natural for multi-line prose.** Config.toml would require TOML multiline escaping. A database adds unnecessary complexity.
- **Hot-reload without daemon restart.** `reload_prompt()` rebuilds only the cached string, preserving the httpx connection pool.
- **Round-trip safe.** What you edit is what the LLM receives — no intermediate compilation or escaping.
- **Tests use `tmp_path` fixtures.** Monkeypatching `_TEMPLATE_PATH` keeps tests isolated without filesystem side effects.
- **Package-safe path resolution.** `Path(__file__).resolve().parent / "prompt_template.txt"` works in both dev repo and installed package.

## Consequences

- `prompt_template.txt` must be included in the wheel (`pyproject.toml` build includes).
- Tests that exercise prompt loading use `tmp_path` + `monkeypatch.setattr` on `_TEMPLATE_PATH`.
- If the template file is deleted at runtime, the daemon continues with the fallback constant and a warning log — degraded but functional.
- The `{default_browser}` placeholder is mandatory; the save endpoint validates its presence.

## Alternatives Considered

| Alternative | Why Rejected |
|---|---|
| Keep as Python constant | No UI editing, no hot-reload, requires daemon restart |
| Store in `config.toml` | TOML multiline string escaping is awkward for prose |
| Store in database/SQLite | Over-engineered for a single text blob |
| Edit Python source via regex | Fragile, injection risk, breaks on `.pyc` compilation |
