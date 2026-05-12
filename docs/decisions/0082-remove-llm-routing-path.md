# ADR 0082 — Remove LLM routing path

**Status:** Accepted  
**Date:** 2026-05-12

## Context

The LLM routing path (Merlin toggle → `LLMRouter.route()` → LM Studio) was added in ADR 0040 and incrementally refined through ADRs 0044, 0056, 0067, 0074, and 0075. In practice:

- The Merlin toggle was never activated in daily use — `VerbRouter` + user-authored graphs covered 100% of realistic utterances.
- LM Studio introduced a ~500 ms cold-start penalty on every first call and required a background model server.
- The prompt template, warmup thread, `no_match` escape tool, `all_llm_visible()` registry method, LLM-visible flag in the Builder, and related web-UI surfaces (Prompt Inspector, `/llm` observability endpoint) added maintenance surface without measurable benefit.
- `httpx` and LM Studio became the only production dependencies on a networked service, contradicting the "local-only, zero cloud" principle.
- The 4-bucket error taxonomy (`program`/`wiring`/`llm`/`infra`) carried an `llm` bucket that could never fire post-removal.

## Decision

Remove the LLM routing path entirely:

1. **Deleted files:** `llm_router.py`, `prompt_template.txt`, `web/prompt.py`, `web/prompt_inspector.html`.
2. **Removed from `daemon.py`:** `llm_router` constructor parameter, `_merlin_mode` flag, `_is_merlin_toggle()`, Merlin toggle block in `_process_utterance()`, `log_llm_sources()` call.
3. **Removed from `config.py`:** `LLMConfig` dataclass and all `[llm]` fields; `[llm]` section now emits a deprecation `WARNING` and is silently dropped (migration shim for existing config files).
4. **Removed from `registry.py`:** `all_llm_visible()` method.
5. **Removed from `observability/`:** `replay-llm` endpoint, `/llm` GET endpoint, `llm_call` span lookup; `llm` bucket removed from error taxonomy (now 3-bucket: `program`/`wiring`/`infra`).
6. **Removed from `primitives.py`/`primitives.toml`:** `no_match` tool.
7. **Removed from Builder SPA:** `llmVisible`/`toggleLlmVisible` store state, LLM toggle button in Toolbar, `findLlmPlan` in markdown export, `llm` category pill in RunsFilterBar, `Brain` icon in RunRow, `[data-error-category="llm"]` CSS rule.
8. **`llm_visible` field in graph JSON:** retained in the `Graph` type and schema for backward compatibility with existing `commands.json` / `workflows.json` files; the registrar now always passes `internal=False` regardless of the flag value.
9. **`httpx`:** removed from production imports (can be removed from `pyproject.toml` if no other consumer needs it).

## Consequences

- **Positive:** daemon starts without LM Studio; cold-start penalty eliminated; maintenance surface shrinks by ~30 files; test suite loses all LLM-specific fixtures.
- **Negative:** open-ended free-form requests that don't match any authored command or primitive verb produce a miss chime. Users must author a graph for any non-trivial command.
- **Migration:** existing `config.toml` files with `[llm]` sections load correctly (warning emitted); `llm_visible` fields in graph JSON files are silently ignored by the registrar.

## Superseded ADRs

ADRs 0028, 0029, 0030, 0031, 0032, 0033 (LLM-specific aspects), 0038, 0040, 0044, 0052, 0056, 0067, 0074 (Merlin-gated LLM path only), 0075 (no_match tool).
