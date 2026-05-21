# ADR 0033: Per-Tool `settle_ms` Metadata and LLM-Callable `wait(ms)` Primitive

**Status:** Accepted
**Date:** 2026-04-21

## Context

When a plan executes a chain of tools — focus browser, open new tab, type text — each step changes system state (window focus, DOM state, cursor position) that the next step depends on. If steps fire back-to-back without any delay, the OS has not finished processing the previous step and the next keystroke or focus change lands on the wrong window or the wrong element.

This is not a hypothetical: R7 §Windows 11 Specifics notes that `SetForegroundWindow` has measurable latency before the window is actually ready to receive keystrokes, and R6 §Limitations notes that `pyautogui.hotkey()` can silently fail if the target application has not yet received focus. Empirically, UI automation suites universally insert sleeps between steps for exactly this reason.

There are two distinct sources of delay in a plan:

1. **Tool-inherent settle time** — the minimum delay after a specific tool fires before the next step can safely proceed. This is a property of the tool, not the utterance. `focus_browser` might need 150 ms; `new_tab` might need 200 ms; `copy` needs 0 ms because it writes to the clipboard synchronously. This delay should be authored once, at the tool level, not repeated at every call site in every plan.

2. **Utterance-specific extra delay** — the LLM may determine that a particular chain needs more pacing than the per-tool defaults provide, e.g., "open a new tab and wait for it to fully load before typing". This is utterance-specific and unknowable at tool-authoring time. An explicit `wait(ms)` tool call lets the LLM express this intent without guessing whether a tool's settle time is sufficient.

## Decision

### `settle_ms` — per-tool metadata

Each tool TOML entry carries a `settle_ms` integer key (default 0). After `Dispatcher.run_plan` calls a tool function successfully, it sleeps `settle_ms` milliseconds before advancing to the next step. The value is authored empirically by the tool author and validated at startup (must be 0–5000 — ADR 0036 rule 5).

Example:
```toml
[focus_browser]
settle_ms = 150
```

`settle_ms` is stored on `ToolEntry` and surfaced in the web UI alongside other tool metadata.

### `wait` — LLM-callable primitive

A dedicated `wait(ms: int)` tool in `tools/primitives.py`, flagged `llm_only = true`. When the LLM inserts `wait(ms=500)` as a plan step, the dispatcher sleeps `ms` milliseconds at that point in the chain. This is additive: if the preceding step has `settle_ms = 150` and the next step is `wait(ms=200)`, the total pause is 350 ms.

`wait` is validated like any other tool: `ms` must be `int`, minimum 0, maximum bounded by `settle_ms` upper limit (5000). Negative or non-integer values are rejected by the startup validator.

## Consequences

### Positive

- Chain-pacing logic lives with the tool, not scattered across LLM prompts or dispatcher code. Tool authors tune `settle_ms` once; all plans using that tool benefit.
- The `wait` primitive gives the LLM a clean, explicit way to add extra pauses when the utterance demands it ("wait for the page to load").
- Both mechanisms are auditable in `outputs/last_plan.json` (the plan artifact) and in structured logs.
- Zero overhead on the hot path: `settle_ms` is only read by `Dispatcher.run_plan`, which is only invoked by the LLM path.

### Negative

- `settle_ms` values must be tuned empirically per tool, per machine class. Values that work on an RTX 4090 may be too short on an integrated-GPU machine where UI transitions are slower. There is no automatic calibration.
- The `wait` primitive gives the LLM the ability to insert arbitrary pauses up to 5000 ms. A confused model that inserts `wait(ms=5000)` between every step would make a chain feel very slow. Mitigation: `max_plan_steps` cap and the 5000 ms upper bound on `ms`.
- Per-tool `settle_ms` and LLM-inserted `wait` are additive but independent; the combined delay is not capped. A plan with many `wait` calls plus high `settle_ms` tools could exceed 30 s for an 8-step chain. This is accepted as an edge case; normal plans will be 1–3 steps.

### Neutral

- `time.sleep` in the dispatcher is patched in unit tests so tests do not incur actual delays (confirmed by the spec's test strategy §Unit (new): `test_dispatcher_plan.py` — `time.sleep` patched for settle).

## Alternatives considered

### Fixed global inter-step delay
Rejected. A single global value cannot accommodate the wide range of tool settle times (copy = 0 ms, focus browser = 150 ms, open new tab = 200 ms). It would either be too slow for fast tools or too fast for slow ones.

### LLM-authored settle time on every step (no per-tool default)
Rejected. Requires the LLM to know the settle time of every tool. This is fragile: the LLM may not get it right, and it must be specified in the system prompt, bloating the stable prefix. Per-tool defaults managed by the tool author are more reliable.

### Async settle (pipeline proceeds; next step waits for OS signal)
Rejected. No reliable OS signal indicates "window is ready to receive keystrokes" or "tab is fully open". A time-based settle is practical and sufficient for the MVP.

## Also see: ADR 0037

ADR 0037 (Focus-Window Hardening — AttachThreadInput + Verified Raise Pattern) adds a clarification on how `settle_ms` interacts with the new `FocusWindowError` raise contract:

- `settle_ms` for focus tools (`focus_browser`, `focus_terminal`, `focus_window`) is set to **200 ms** in ADR 0037. This timer is started only when `SetForegroundWindow` succeeds and is verified via `GetForegroundWindow()`.
- If focus fails, `FocusWindowError` is raised. `Dispatcher.run_plan` catches it, halts the plan, and plays the miss chime. The `settle_ms` sleep is never reached.
- In other words: `settle_ms` handles the "focus succeeded, now wait for the OS to paint" case; `FocusWindowError` handles the "focus never succeeded" case. The two mechanisms are complementary and non-overlapping.

This relationship is important for future tool authors: `settle_ms` is not a substitute for focus verification. A focus tool must verify that the target window is actually in the foreground before returning; the `settle_ms` delay that follows is for safe downstream keystroke delivery, not for retrying a failed focus.

## References

- R6 — pyautogui limitations and settle time rationale for keystroke primitives
- R7 — focus window latency on Windows 11 (`SetForegroundWindow` settle)
- ADR 0032 — one-shot plan execution; `run_plan` step loop
- ADR 0034 — TOML as single source of truth; `settle_ms` lives in TOML
- ADR 0036 — startup validator; `settle_ms` range check
- ADR 0037 — focus-window hardening; `FocusWindowError` raise contract; 200 ms `settle_ms` on focus tools
