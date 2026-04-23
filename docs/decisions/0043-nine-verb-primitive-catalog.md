# ADR 0043: Nine-Verb Primitive Catalog

**Status:** Accepted (amended by [ADR 0054](0054-last-primitive.md), which adds a tenth verb `last`)
**Date:** 2026-04-21

## Context

With LLM-only routing (ADR 0040), the tools array sent to LM Studio is the sole surface the model can compose from. A sprawling catalog hurts small-MoE accuracy; a threadbare one forces the LLM to work around missing verbs (or emit `no_match`). The spec at `docs/superpowers/specs/2026-04-21-llm-default-no-rapidfuzz-design.md` Section 2 locked a minimal, orthogonal set of **nine** primitives as the catalog the LLM sees.

Two of the spec's verb names collide with Python builtins (`type`, `open`). A previous draft of this ADR listed invented verbs such as `press_keys(*keys)`, `hscroll`, `focus_window`, and `open_url` — names that were never implemented. This rewrite documents the catalog as it actually ships.

## Decision

### The nine canonical verbs

| Verb (LLM-visible) | Python symbol | Arguments | Implementation |
|---|---|---|---|
| `focus` | `focus` | `target: str` | `resolver.resolve_window(target)` → `SetForegroundWindow` with `AttachThreadInput` workaround + `_verify_foreground` |
| `type` | `type_text` | `text: str` | `pyautogui.write(text, interval=0.02)`; truncated to 500 chars with WARNING log |
| `open` | `open_target` | `target: str` | `resolver.resolve_app(target)` → blocklist check → `os.startfile(token)` + `_verify_open` poll |
| `close` | `close` | *(none)* | `pyautogui.hotkey("ctrl", "w")` + foreground-change verify (closes current tab/doc) |
| `close_window` | `close_window` | *(none)* | `pyautogui.hotkey("alt", "f4")` + foreground-change verify |
| `press` | `press` | `combo: str` | Split on `+` → `pyautogui.hotkey(*keys)` |
| `wait` | `wait` | `ms: int` | `time.sleep(ms / 1000.0)` |
| `click` | `click` | `button: str = "left"` | `pyautogui.click(button=button)` (validates `{left, right, middle}`) |
| `no_match` | `no_match` | `reason: str` | No-op body; `LLMRouter._parse_response` intercepts the first `no_match` step and returns `None` to signal a miss |

Total: **9 LLM-visible verbs**.

### Symbol-to-name mapping

Two verbs use `@tool(name=...)` to register under a name that differs from the Python symbol:

```python
@tool(name="type")
def type_text(text: str) -> None: ...

@tool(name="open")
def open_target(target: str) -> None: ...
```

The Python symbols (`type_text`, `open_target`) avoid shadowing the builtins `type` and `open` inside `primitives.py`. The `name=` argument was added to the `@tool` decorator in `registry.py` for exactly this purpose. Every other verb registers under its Python symbol via the bare `@tool` form.

### Bonus verb (retained from the pre-ADR catalog)

`scroll(direction: str, amount: int = 3)` is still registered and still `llm_only = true`. It predates this ADR and is not part of the canonical nine, but it is harmless, documented, and occasionally used by the LLM for "scroll down" / "page up" utterances. Left in place; a future ADR may merge it into `press` (via `page_down` / `page_up` keys) if tool-count pressure rises.

### Registration surface

All ten tools live in `src/voice_commander/tools/primitives.py` with sidecar metadata in `primitives.toml`. Every entry carries `phrases = []` and `llm_only = true` — both mandatory since ADR 0040. `settle_ms` is per-tool:

| Tool | `settle_ms` |
|---|---|
| `focus` | 200 |
| `type` | 50 |
| `open` | 500 |
| `close` | 100 |
| `close_window` | 100 |
| `press` | 50 |
| `wait` | 0 |
| `click` | 50 |
| `no_match` | 0 |
| `scroll` | 0 |

## Consequences

### Positive

- The LLM sees exactly 10 tool signatures (9 canonical + `scroll`). Well under the 25-tool soft ceiling for small MoE tool-calling reliability.
- Every verb is composable: "search for cats" decomposes into `focus(target="chrome") → press(combo="ctrl+t") → press(combo="ctrl+l") → type(text="cats") → press(combo="enter")` with no verb left wanting.
- Parameter resolution is delegated to `resolver.resolve_window` / `resolve_app` (ADR 0042), keeping the primitive bodies small and testable.
- `no_match` is a first-class tool call rather than a null response. `tool_choice="required"` (ADR 0031) ensures the LLM always returns *some* valid call.
- Naming: short strings ("focus", "type", "open") minimise prefill tokens in the system prompt and tool-call outputs.

### Negative

- Shadowed builtins inside `primitives.py` required the `@tool(name=...)` overload and two Python-symbol / LLM-name pairs. Mildly surprising to readers but well-documented.
- `close` vs `close_window` is a semantic split the LLM must keep straight (close-current-tab vs close-current-window). System prompt does not explicitly cover the distinction yet; guarded by tool descriptions in `primitives.toml`.

### Neutral

- `scroll` is kept for backward compatibility with pre-ADR plans; future catalog audits may retire it.
- No horizontal-scroll verb. The old ADR draft mentioned "hscroll"; it was never implemented and has been removed from this document.

## Alternatives considered

### Keep `press_keys(*keys)` variadic form
Rejected. Variadic parameters do not round-trip cleanly through OpenAI tool-call JSON schemas. A single string with `+` separators (`combo: str`) is the simpler contract and matches the way LM Studio actually emits keystroke plans.

### Collapse `close` and `close_window` into one verb with a `scope` parameter
Rejected. Adds an enum argument the LLM must choose correctly. Two short verbs with distinct names is more predictable and needs less prompt explanation.

### Rename Python symbols to match verb names (`type`, `open` at module scope)
Rejected. Python lets you shadow builtins inside a module but the ergonomic cost (type-checker warnings, surprising error messages) outweighs the symmetry benefit. `@tool(name=...)` is a one-liner and preserves clarity.
