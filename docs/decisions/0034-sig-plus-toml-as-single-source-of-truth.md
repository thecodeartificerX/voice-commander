# ADR 0034: Python Signature + TOML Sub-Tables as Single Source of Truth for Tool Arguments

**Status:** Accepted
**Date:** 2026-04-21

## Context

Before the LLM router, tools are zero-argument functions. The only metadata per tool is the phrase list, description, category, and enabled flag — all in the sidecar TOML (ADR 0021). The LLM router introduces typed arguments, and with them a new challenge: the same argument information must appear in three places with consistent types and descriptions:

1. **Python function signature** — defines parameter names, types, and defaults. This is what the runtime calls.
2. **OpenAI tool-calling JSON schema** — sent to the LLM in every request. Must reflect the Python types exactly. Must include natural-language descriptions so the LLM knows what each argument means.
3. **Web UI** — displays tool metadata to the user. Should show arg names, types, and descriptions.

If argument metadata lives in multiple places (e.g., types in Python, descriptions in a docstring, schema hand-written in a separate file), they will drift. A rename on the Python side breaks the schema. A description change in the docstring is not reflected in the schema. The validator (ADR 0036) would catch some drift, but only if the authoritative source is clearly defined.

R5 evaluated three approaches to schema generation: (A) Pydantic `TypeAdapter`, (B) hand-rolled `inspect.signature` + `typing` introspection, (C) OpenAI SDK `pydantic_function_tool`. R5 recommends approach B: it has zero new dependencies, full control over the type mapping, and clean description injection from a sidecar source. Crucially, R5 identifies the sidecar TOML as the right place for descriptions: they are not scraping docstrings (fragile, loses structure) and not stored in `Annotated[...]` metadata (verbose at the call site).

## Decision

**One tool change = one place. Two files: `.py` + `.toml`.**

- **Python signature** is the authority for parameter names, types, and defaults. Supported types: `str`, `int`, `float`, `bool`, `typing.Literal[...]`, `X | None`. Any other type fails startup validation (ADR 0036 rule 4).
- **TOML `[tool.args.<name>]` sub-tables** are the authority for per-argument descriptions, human-readable `type` hints (for the web UI), and `required`/`default` documentation. The `[tool.args.<name>].description` field is mandatory for any tool with arguments — the startup validator enforces this (ADR 0036 rule 2).

At daemon startup, `tool_schema.py` reflects each tool's Python signature using `inspect.signature` + `typing.get_type_hints`, walks the type tree via `get_origin`/`get_args`, and merges TOML descriptions. The result is an OpenAI-compatible JSON schema stored on `ToolEntry.params_schema`. Web UI, LLM request builder, and rapidfuzz all read from `ToolEntry` — they never re-parse the Python signature or TOML directly.

### Canonical layout

```python
# src/voice_commander/tools/filesystem.py
@tool
def open_file(name: str, folder: str | None = None) -> None: ...
```

```toml
# src/voice_commander/tools/filesystem.toml
[open_file]
phrases = ["open file", "open"]
description = "Open a file by name, optionally from a specific folder."
settle_ms = 200
llm_only = false

[open_file.args.name]
type = "str"
description = "File name to open (without extension)."
required = true

[open_file.args.folder]
type = "str"
description = "Optional subfolder under the user's projects directory."
required = false
default = ""
```

The startup validator cross-checks: every Python parameter has a TOML sub-table, every TOML sub-table names a Python parameter, types in the sub-table are consistent with the Python annotation (informational — the schema uses Python types as authoritative).

## Consequences

### Positive

- One rename in Python is the only change needed; the validator rejects any state where Python and TOML are out of sync.
- LLM schema, web UI, and phrase index all derive from the same registry entry. No hand-maintained JSON schema files.
- TOML descriptions are natural-language prose, separate from code. Non-developers can improve them without touching Python.
- R5 approach B requires no new runtime dependencies. `inspect`, `typing`, and `tomllib` (stdlib in Python 3.11+) cover all cases.

### Negative

- Every parameter requires a TOML `[tool.args.<name>]` sub-table with a description. This is more ceremony than a plain Python function with a docstring. However, the startup validator makes missing entries a hard error, so the ceremony is enforced consistently.
- Supported types are deliberately limited to a small set (six types from R5). Tools that need complex types (`pathlib.Path`, `dict`, `list`) must either simplify their signatures or use `str` and document the expected format in the description. This is a deliberate constraint to keep schema generation simple and model accuracy high.
- TOML `type` field in `[tool.args.<name>]` is informational for the web UI. The runtime uses the Python type hint. If they differ, the Python type wins — the validator does not enforce type-field agreement, only presence of required keys.

### Neutral

- Zero-argument tools are unchanged. The `[tool.args]` section is omitted for them; the validator does not require it.
- The commander skill (ADR 0035) is the only path for writing tool files. It generates the `.py` + `.toml` pair atomically and in the correct format.

## Alternatives considered

### Pydantic `TypeAdapter` for schema generation (R5 Approach A)
Rejected. Requires adding pydantic as a runtime dependency. Descriptions cannot be injected without post-processing. Pydantic's schema output requires cleanup to match the OpenAI format. See R5 §Approach A for full analysis.

### Docstrings as description source
Rejected. Docstrings are unstructured; extracting per-parameter descriptions requires parsing Google/NumPy/Sphinx style conventions. Descriptions in TOML are structured, tool-schema-aware, and editable by non-developers without touching Python.

### Separate hand-authored JSON schema files
Rejected. Would be a third file per tool, guaranteed to drift from the Python signature. Exactly the problem this ADR is designed to prevent.

## References

- R5 — Python sig → JSON schema research; approach B recommendation (full reference doc)
- ADR 0021 — sidecar TOML per tool (existing design this extends)
- ADR 0035 — commander skill as sole tool author
- ADR 0036 — startup validator; enforces sig↔TOML consistency
