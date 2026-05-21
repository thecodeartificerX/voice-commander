# ADR 0036: Startup Validator — Fail-Loud on Sig↔TOML Drift

**Status:** Accepted
**Date:** 2026-04-21

## Context

ADR 0034 establishes that the Python function signature and the sidecar TOML are jointly the single source of truth for tool arguments. Both must be consistent for the daemon to run correctly: the runtime calls the Python function with the types the signature declares, the LLM is sent a JSON schema derived from those types, and descriptions in the TOML make the schema useful to the model.

Without enforcement, several drift scenarios are possible:

1. A developer adds a parameter to the Python signature and forgets to add `[tool.args.<name>]` to the TOML. The startup validator would catch this.
2. A TOML sub-table names a parameter that was later renamed in Python. The schema is sent to the LLM with a stale argument name. The LLM may call the tool with the wrong key; the runtime crashes.
3. A tool is marked `llm_only = true` but also has a `phrases` list. The phrases are never matched (the tool is invisible to rapidfuzz), but their presence is confusing and signals a misconfiguration.
4. A `settle_ms` of -50 is authored (typo). The dispatcher would call `time.sleep(-0.05)` — harmless in Python but meaningless and probably unintended.
5. The `no_match` or `wait` primitives are absent or misconfigured. The LLM router cannot function without them.

These errors are all detectable at load time, before any utterance is processed. A fail-loud policy at startup prevents any of them from reaching production. The cost is a non-starting daemon, which is visible and recoverable. The alternative — a daemon that starts but misbehaves on the first LLM call — is much harder to debug.

The validator is also exposed as a CLI flag (`python -m voice_commander --validate`) so it can be run in CI without starting the full daemon.

## Decision

A startup validator runs as part of `build_streaming_daemon()` (and as a standalone CLI invocation via `--validate`). It exits non-zero (or raises `ConfigurationError`) on any of the following conditions:

1. A `@tool`-decorated function lacks a TOML entry.
2. A function parameter (excluding `self`, `cls`, `*args`, `**kwargs`) lacks `[tool.args.<name>].description` in TOML.
3. A TOML `[tool.args.<name>]` sub-table names a parameter absent from the Python signature.
4. A parameter uses a type not in the supported set (`str`, `int`, `float`, `bool`, `Literal[...]`, `X | None`).
5. `settle_ms` is negative or greater than 5000.
6. A tool marked `llm_only = true` declares a non-empty `phrases` list.
7. Required primitives (`no_match`, `wait`) are absent from the registry or are not flagged `llm_only = true`.

Error messages include the tool name and parameter name so the operator knows exactly what to fix. Each rule is independently testable: `test_validation.py` exercises every rejection condition in isolation.

Rules 1 through 3 apply regardless of whether the LLM router is enabled. They are universal consistency guarantees. Rules 4 through 7 apply whenever any tool uses arguments or primitives; rule 7 applies only when `[llm_router].enabled = true`.

For the zero-argument MVP tools, all rules pass trivially (no args, no `[tool.args]` sub-tables required). The validator is backwards-compatible with the Phase 4 tool set.

## Consequences

### Positive

- Drift between Python sig and TOML is caught before the daemon runs, before the LLM is ever called. No silent misbehaviour in production.
- `--validate` in CI makes the check machine-enforceable on every commit. The feature branch adds this step to the CI pipeline.
- Error messages are actionable: they name the specific tool and parameter, not just a generic "configuration error".
- The commander skill's post-write `--validate` step (ADR 0035) leverages this same command, so skill-generated files are validated by the same logic as human-authored files.
- Zero-argument tools are unaffected: the validator adds no ceremony for the existing Phase 4 toolset.

### Negative

- A badly-formed tool file prevents the entire daemon from starting, even if the bad tool is not the one the user needs right now. There is no partial-load mode.
- Rule 7 (required primitives check) only applies when `llm_router.enabled = true`. If a user enables the LLM router without the primitives present, the daemon fails to start. This is the desired fail-loud behaviour, but it may surprise a user who enabled the flag without reading the setup steps.
- Validation adds a few hundred milliseconds to daemon startup (reflecting all tool signatures). Acceptable; startup is a one-time cost per daemon run.

### Neutral

- The validator does not check that descriptions are non-empty strings or that `settle_ms` values are empirically correct for the hardware. It enforces structural correctness, not semantic quality.
- When `--validate` exits non-zero, the exit code is 1 and the error is written to stderr. Standard CI pipelines will catch this.

## Alternatives considered

### Warn-only (log drift, continue startup)
Rejected. A warn-only policy means drifted tools reach production silently. The LLM router would call a tool with stale argument names; the dispatcher would throw a `TypeError`; the chain would fail mid-execution. The user experience is worse than a daemon that refuses to start with a clear error.

### Validate only in CI (not at daemon startup)
Rejected. CI validates the committed state of the repo, but config files can be edited locally without committing. A developer editing a TOML directly would not hit the CI check until they push. Startup validation catches local edits immediately.

### Pydantic model validation
Rejected. Pydantic is not a current dependency. Adding it for validation alone when `inspect` and `typing` cover the same cases introduces unnecessary weight. See R5 §Approach A for why Pydantic was rejected for schema generation too.

## References

- R5 — Python sig introspection; type-walking approach used by the validator
- ADR 0034 — Python sig + TOML as single source of truth; defines what "drift" means
- ADR 0035 — commander skill; post-write `--validate` usage
- ADR 0031 — `no_match` primitive; rule 7 enforces its presence
- ADR 0033 — `settle_ms`; rule 5 enforces its range
