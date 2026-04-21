# ADR 0029: Gemma 4 E4B as the First Router Model

**Status:** Accepted
**Date:** 2026-04-21

## Context

The LLM router requires a local model that satisfies four constraints simultaneously: (1) native tool-calling support, (2) latency ≤ 600 ms warm on a consumer GPU (RTX 3060–4090), (3) available in LM Studio's model catalog for one-click download, and (4) small enough to fit in VRAM alongside the CUDA Whisper model already loaded by the daemon.

R2 and R3 evaluated six candidates against these constraints. The key findings:

- **Gemma 4 E4B** (`google/gemma-4-e4b`): 4.5 B effective parameters (dense transformer, not MoE despite the "E4B" designation). Native function-calling via dedicated special tokens. Q4_K_M quantisation: ~4 GB VRAM, ~196 tok/s on RTX 3060/4090, giving ~510 ms for a 100-token tool-call response. 849 K+ downloads on LM Studio. R1 confirms LM Studio wraps Gemma 4's native tool format in the standard `tool_calls` field for the OpenAI-compatible endpoint.
- **Qwen3-30B-A3B-Instruct**: Excellent tool-calling, but 18.6 GB Q4. Does not fit on an RTX 3060 alongside `small.en` Whisper.
- **Granite-3.1-8B-A1.3B**: Fastest (150–250 ms), smallest (5 GB Q4), IBM-certified tool-calling accuracy. Primary backup if Gemma 4 E4B fails reliability tests.
- **Phi-3.5-MoE-Instruct**: Brittle function-call grammar, not in official LM Studio catalog at time of writing. Second-tier backup.
- **Mixtral-8x7B-Instruct**: 28.4 GB Q4, at latency budget limit, EOL-track. Not recommended.
- **Ling-mini-2.0**: Promising latency (~200 ms) but community tool-call validation too thin in April 2026. Future candidate (Q3 2026).

R2 notes an important clarification: Gemma 4 E4B is a *dense* model, not a pure MoE. "E4B" means "effective 4 B parameters" via per-layer embeddings and sliding-window attention, not expert gating. This matters for integration: no expert-routing latency, deterministic execution path.

R2 §3 also flags that Gemma 4's native output format uses `<|tool_call>...<|/tool_call>` XML-like tags, not raw JSON `tool_calls`. LM Studio's llama.cpp backend parses these into the standard OpenAI `tool_calls` field. The `--validate` CI step (ADR 0036) will catch any regression if the parsing breaks.

## Decision

Gemma 4 E4B (`google/gemma-4-e4b`, Q4_K_M quantisation, instruction-tuned variant) is the first router model. `[llm_router].model_id = "google/gemma-4-e4b"` is the default in `config.toml`. The model ID is a config knob so operators can swap to any backup without code changes.

Selection criteria, in priority order:
1. Native tool-calling support (not prompt-engineered).
2. Fits on RTX 3060 12 GB with `small.en` Whisper in VRAM.
3. Available in LM Studio catalog (one-click download).
4. Warm latency ≤ 600 ms for ≤ 100 output tokens at Q4_K_M.

If Gemma 4 E4B fails reliability testing in Phase L2, the backup ladder is:
- **Tier 1 (RTX 4090 only):** Qwen3-30B-A3B-Instruct — 18.6 GB Q4, proven agentic tool-calling, 250–400 ms.
- **Tier 2 (latency critical / low VRAM):** Granite-3.1-8B-A1.3B — 5 GB Q4, 150–250 ms, IBM function-call leaderboard winner.
- **Tier 3 (future):** Ling-mini-2.0 — await Q3 2026 community validation.

## Consequences

### Positive

- One-click install for the target user. Gemma 4 E4B is already the most-downloaded small model on LM Studio.
- Dense architecture eliminates expert-routing latency and makes execution time more predictable.
- 128 K context window is far larger than needed for tool definitions + a single utterance, leaving room for extended tool sets.
- Model config is a single string; switching to a backup requires one config line change.

### Negative

- R2 §3 notes that LM Studio's parsing of Gemma 4's native tool-call tokens must be verified locally before Phase L2. If LM Studio does not map the native format to `tool_calls` correctly, manual parsing of raw output is required.
- `tool_choice="required"` is emulated via system prompt for Gemma 4 (no explicit API parameter), not enforced by the inference engine. The `no_match` escape tool (ADR 0031) is the primary guard against the model emitting plain text.
- Accuracy above ~25 tools has not been validated for E4B. The primitive set is kept minimal (< 25 total) to stay in the validated range (R2 §5).

### Neutral

- Total tool count for the MVP router is 14 existing tools plus up to 6 primitives = ~20. Well within the safe zone identified in R2 §5.

## Alternatives considered

### Qwen3-30B-A3B-Instruct as primary
Rejected for primary. Does not fit on RTX 3060 12 GB alongside Whisper. Remains Tier 1 backup for RTX 4090 deployments.

### Granite-3.1-8B-A1.3B as primary
Not rejected outright — it is Tier 2 backup. However, Gemma 4 E4B has broader community adoption on Windows/LM Studio and a larger context window, making it more suitable as the default recommendation.

### No default model (user must configure)
Rejected. A missing default forces every user to read documentation before the feature works. A sensible default with clear upgrade guidance is better UX.

## References

- Spec §Architecture, §Config, §Risks — `docs/superpowers/specs/2026-04-21-llm-router-design.md`
- R2 — Gemma 4 E4B deep-dive (full reference doc)
- R3 — Backup MoE candidates comparison matrix (full reference doc)
- ADR 0026 — hybrid routing design
- ADR 0028 — LM Studio endpoint
