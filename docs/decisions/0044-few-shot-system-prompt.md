# ADR 0044: Few-Shot System Prompt with Default-Browser Templating

**Status:** Accepted
**Date:** 2026-04-21

## Context

The spec at `docs/superpowers/specs/2026-04-21-llm-default-no-rapidfuzz-design.md` Section 3 called for a few-shot system prompt that teaches the model how to compose the nine-verb catalog (ADR 0043) into multi-step plans. Earlier iterations of the LLM router shipped a prompt that (a) referenced tool names from the pre-catalog era (`focus_browser`, `new_tab`, `type_text`, `press_keys`) and (b) hard-coded "chrome" as the browser. Both were regressions against the spec and caused the LLM to emit invalid tool calls or route web intents to the wrong browser.

The spec assigned this decision to ADR 0043, but the catalog ADR already covers the verb inventory. Splitting the system-prompt design into its own ADR avoids overloading 0043 and keeps the prompt's evolution tracked separately.

## Decision

### Module-level template

`src/voice_commander/llm_router.py` exports a `_SYSTEM_PROMPT_TEMPLATE` module constant. It is a triple-quoted string containing prose rules and exactly three few-shot examples. The `{default_browser}` placeholder appears twice — once in the rules block ("Prefer the user's default browser (`{default_browser}`)") and once inside the first example's `focus()` call.

```python
_SYSTEM_PROMPT_TEMPLATE = """You are a Windows voice command executor. …

Rules
- Prefer the user's default browser ('{default_browser}') for web or search intents.
- Emit tool calls in strict execution order. …
- Call `no_match(reason)` only when the utterance is not an executable command …

Examples

User: "search how to lose weight"
Tools: focus(target="{default_browser}"),
       press(combo="ctrl+t"),
       press(combo="ctrl+l"),
       type(text="how to lose weight"),
       press(combo="enter")

User: "copy that and paste it in notepad"
Tools: press(combo="ctrl+c"),
       focus(target="notepad"),
       press(combo="ctrl+v")

User: "open spotify"
Tools: open(target="spotify")
"""
```

### Substitution at construction

`LLMRouter.__init__` calls `self._build_system_prompt()`, which formats the template:

```python
def _build_system_prompt(self) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        default_browser=self._config.default_browser,
    )
```

The result is cached on `self._system_prompt` and reused for every `route()` request and the `warmup()` request. The system prompt is never re-rendered at runtime — changing `default_browser` requires a daemon restart.

### Token budget

Measured on `cl100k_base` tokenizer (LM Studio's default for most local models), the rendered system prompt is **~680 prefill tokens**. Breakdown:

- Prose rules: ~85 tokens.
- Three few-shot examples: ~110 tokens.
- Surrounding structure (header, separators): ~35 tokens.
- Tools array JSON schema (serialized alongside the prompt in the request body): ~450 tokens.

Warmup (ADR 0038) POSTs a real chat-completion with `max_tokens=1` to seed LM Studio's prefix KV cache with exactly this ~680-token prefix. The first real user utterance lands on a warm cache and incurs only generation cost.

### Legacy-name guard

The prompt must not reference any tool name from the pre-ADR-0043 catalog (`focus_browser`, `new_tab`, `type_text`, `press_keys`, `open_url`, `launch`, `focus_window`). A unit test `test_prompt_no_legacy_tool_names` asserts this by scanning `_SYSTEM_PROMPT_TEMPLATE` for each forbidden token. The test guards against regression if a future edit re-imports an old example.

### `default_browser` config field

A `default_browser: str = "chrome"` field lives on `LLMConfig` (see `config.py`). Overridable via `config.local.toml`, `config.toml`, or `VC_LLM_DEFAULT_BROWSER` env var — same precedence chain as every other `[llm].*` field. The value is logged at startup by `log_llm_sources()` so users can see which source won.

## Consequences

### Positive

- One place to edit the prompt. All three examples and the browser-preference rule are in one module constant.
- Browser preference is per-user without code changes. A Firefox user edits `config.local.toml` and restarts the daemon.
- Warmup seeds the exact prefix the real requests use — first-call latency matches steady-state latency.
- Legacy-name regression is caught by a deterministic unit test, not by live traffic.

### Negative

- Template substitution uses Python's `str.format`, which means literal `{` / `}` inside the prompt would need doubling. The current prompt has none, but future edits must respect the constraint.
- Changing `default_browser` at runtime has no effect on the prompt until the daemon restarts. Acceptable — config changes already require a restart for most fields.
- Three examples may not cover every idiom. Empirically the LLM extrapolates fine to verbs not shown (`wait`, `click`, `scroll`), but if misses cluster around a specific pattern, add a fourth example and re-measure the prefill budget.

### Neutral

- The prompt does not carry a tool reference section — the tools array already encodes that. Prose would be duplicate surface area and risk drift.
- Prompt versioning is implicit: the git history of `llm_router.py` is the change log.

## Alternatives considered

### Inline the default browser as a literal
Rejected. Even if the repo's author always uses Chrome, shipping a Chrome-hardcoded prompt fails any contributor whose default is Edge / Firefox / Arc. The field costs 40 bytes in `config.toml` and a single `.format()` call.

### Externalise the prompt into a `.txt` resource
Rejected for the MVP. Keeping the prompt in the source file lets unit tests assert its content without reading the filesystem, and the diff history is where prompt-engineering changes should live.

### Fetch default browser from the registry at runtime
Deferred. Windows' registered default-browser lookup requires reading `HKCU\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice\ProgId` and mapping the ProgID to a display name. Doable, but the user must still express the name the way the LLM should say it (e.g. Chrome Canary's ProgID is `ChromeSSHTM...`). A config field is the lowest-friction solution for now.

## Related

- ADR 0031 — `tool_choice="required"` + `no_match` (the prompt's third rule is what makes the model reliably call `no_match` rather than emit natural-language refusals).
- ADR 0038 — Warmup via real chat completion (the mechanism that turns this prefix into a KV-cache hit on the first real call).
- ADR 0043 — Nine-verb catalog (the set of tool names that MUST be the only ones referenced in the prompt).
