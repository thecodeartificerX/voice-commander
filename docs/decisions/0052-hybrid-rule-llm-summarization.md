# 0052. Hybrid Rule + LLM Summarization for HUD Entries

**Status:** Superseded by ADR 0075 (rule-table summarizer deleted; HUD now renders raw `tool_fired.name` directly)
**Date:** 2026-04-22

## Context

The HUD (ADR 0051) needs to turn a `PlanOutcome` into a short summary.
Four candidates were considered:
1. Rule-based mapping only — cheap, deterministic, dumb on novel chains.
2. Second LLM call per command — rich, but +300-500 ms per utterance and
   token cost on every command.
3. Piggyback on the first LLM call (add `summary` field to response
   schema) — near-zero cost, but breaks OpenAI tool-calling shape.
4. Hybrid: rules by default, LLM fallback only on `status=error` or
   unknown verb.

## Decision

Use option (4). A rule table in `src/voice_sprite/summary_rules.py` covers
the nine-verb primitive catalog (ADR 0043) plus `scroll` and `no_match`.
Chain detectors (starting with `detect_search_chain`) recognise canonical
multi-step patterns and replace the last-step-wins default with a more
natural phrase. LLM fallback (`LLMSummaryClient`) is called only when the
rule table cannot handle the outcome — errors or tool names outside the
catalog. LLM failure / timeout / empty response degrades to a raw rule
fallback so the HUD always renders a line.

## Consequences

- ~95 % of commands never hit the LLM — hot path stays cheap.
- Errors and unusual chains get rich explanations.
- Adding a new primitive verb means one rule-table entry + one test — lowest
  friction.
- Raw-rule fallback bounds the worst case to "predictable but terse".
- Per-summary LLM timeout (`[hud].llm_summary_timeout_ms`, default 800 ms)
  is independent of the routing LLM's `[llm].timeout_ms` because
  summarization is off the hot path.
