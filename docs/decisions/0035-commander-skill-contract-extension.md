# ADR 0035: Commander Skill Contract Extension for Arguments, Settle, and LLM-Only Flag

**Status:** Accepted
**Date:** 2026-04-21

## Context

The existing `.claude/skills/commander/` skill creates, edits, renames, moves, disables, and deletes voice-commander tools. For Phase 4 tools (zero-argument macros), this means writing a Python function decorated with `@tool`, a sidecar TOML with phrases and description, and a test file. The skill is the single authorised author of tool files, and the automation ensures that registry, phrases, and tests stay in sync after every change.

The LLM router (ADR 0026) extends the tool contract: tools can now have typed arguments, a `settle_ms` timing value, and an `llm_only` flag. Each of these has a correlated artifact:

- **Arguments** → Python type hints on the function signature + `[tool.args.<name>]` TOML sub-tables with descriptions (ADR 0034).
- **`settle_ms`** → TOML `[tool].settle_ms` key (ADR 0033).
- **`llm_only`** → TOML `[tool].llm_only` key.

If the commander skill is not updated, operators who use it to create or edit argument-bearing tools will get incomplete output: a Python sig without TOML sub-tables, or a TOML without type hints on the function. The startup validator (ADR 0036) will catch this at daemon start, but only after the files are already wrong. The skill should prevent the drift, not just detect it.

In addition, the post-write validation sequence needs extending: after generating files, the skill must run `pytest tests/unit/test_tools_<module>.py` and then `uv run python -m voice_commander --validate`. If either fails, all three generated files (`.py`, `.toml`, test) must be rolled back atomically.

## Decision

The commander skill interview is extended with three new question groups, applied to the create and edit operations:

1. **Arguments interview** — "Does this tool take arguments? (e.g., file path, window title)"
   - For each argument: name, Python type (from supported set), description, required?, default value if optional.
   - This drives: typed parameter on the Python function signature + `[tool.args.<name>]` TOML sub-table.

2. **Settle time interview** — "Does this tool change window focus, open an app, or fire keystrokes into another application? If yes, how long to wait after? (default 0 ms)"
   - This drives: `settle_ms = <value>` in the TOML `[tool]` block.

3. **Routing tier interview** — "Should this tool be callable by voice phrase (rapidfuzz), by LLM only, or both? (default: both)"
   - Maps to: `llm_only = true/false` in TOML. If `llm_only = true`, the `phrases` array in TOML must be empty (startup validator enforces this — ADR 0036 rule 6).

Code generation produces:
- Python file: `@tool`-decorated function with full type hints on the signature. Return type `-> None`.
- TOML file: `[tool]` block with all keys (phrases, description, category, enabled, settle_ms, llm_only) + one `[tool.args.<name>]` sub-table per parameter.
- Test file: three assertions — (a) module imports without error, (b) function signature parameter names match TOML `[tool.args]` keys, (c) one happy-path call with mocked side effects.

Post-write sequence (all three must pass or files are rolled back):
1. `pytest tests/unit/test_tools_<module>.py`
2. `uv run python -m voice_commander --validate`

The rollback is atomic: the skill writes to temporary files first, then renames them into place only after both checks pass. On any failure, temporary files are deleted and the originals (if editing) are restored.

## Consequences

### Positive

- Drift between Python sig and TOML is structurally impossible when the skill is used: it writes both atomically and validates before committing.
- The `--validate` post-write step surfaces any issue the skill's own generation logic missed, e.g., an unsupported type that slipped through the interview.
- Operators do not need to know the TOML sub-table format; the skill generates it from interview answers.
- Renaming a parameter requires one skill invocation; the skill updates `.py`, `.toml`, and the test atomically.

### Negative

- The skill interview becomes longer for argument-bearing tools. Three extra question groups per tool. For zero-argument macros, the added questions are skipped with "no" answers, so there is no regression for the common case.
- Rollback on `--validate` failure means the operator must re-run the skill. The error message from `--validate` is shown so the operator can correct the interview answer.
- The skill is a Claude Code skill, not a standalone tool. Operators who edit tool files manually (outside the skill) bypass the atomic write and rollback. They are still caught by the startup validator, but the rollback feature does not apply.

### Neutral

- The skill contract extension does not change the Python or TOML file format. Files produced by the old skill for zero-argument tools remain valid. The extension is purely additive.
- The commander skill writes test assertions that check sig↔TOML consistency. These tests serve double duty as regression guards if someone edits the Python or TOML manually later.

## Alternatives considered

### Manual tool file authoring (no skill updates)
Rejected. The skill exists precisely to prevent drift. Leaving it un-updated while the contract expands ensures every new argument-bearing tool is one manual mistake away from a broken daemon.

### Separate skill for argument-bearing tools
Rejected. Two skills for the same concept split the mental model and the codebase. One skill, extended, is simpler.

### Schema generation at edit time (no TOML sub-tables)
Rejected. Moving the source of truth into Python-only (with post-hoc schema generation from docstrings or `Annotated`) violates the single-source design of ADR 0034. TOML sub-tables are required for human-readable descriptions.

## References

- Spec §Commander skill contract extension — `docs/superpowers/specs/2026-04-21-llm-router-design.md`
- ADR 0034 — Python sig + TOML as single source of truth
- ADR 0036 — startup validator; what the post-write `--validate` checks
- ADR 0021 — sidecar TOML per tool (original skill contract)
