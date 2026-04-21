# Gemma 4 E4B as Tool-Calling Router

**Status:** Available and recommended for on-device tool routing  
**Evaluated:** 2026-04-21  
**Context window:** 128K tokens | **Effective params:** 4.5B | **Latency budget target:** ≤600ms warm cache

---

## 1. Model Availability & Specs

**Status:** CONFIRMED publicly available.

**Sources:**
- LM Studio: `google/gemma-4-e4b` — directly listed and downloadable
- HuggingFace: `google/gemma-4-E4B` (base), `google/gemma-4-E4B-it` (instruction-tuned)
- Ollama: `gemma4:e4b`

**File size & quantizations:**
- Full precision (bfloat16): ~9.5 GB
- Q4_K_M quantization: ~3–4 GB (recommended for RTX 3060/4060)
- Multiple GGUF quantizations available on HuggingFace (Unsloth, community)
- LM Studio download count: 849.7K (widely adopted)

**Architecture:**
- Multimodal transformer: text, images (variable resolution), audio, video input
- 42 layers, 262K vocabulary, 128K context window
- Per-Layer Embeddings (PLE) for parameter efficiency
- Hybrid attention: sliding window (512 tokens) + global attention

---

## 2. Mixture of Experts (MoE) Clarification

**IMPORTANT:** E4B is NOT a pure MoE model; the E4B designation refers to "effective" parameters.

**Clarification:**
- Gemma 4 family includes four sizes: E2B (2.5B effective), E4B (4.5B effective), 26B MoE, 31B Dense
- E4B itself is a **dense transformer**, optimized for on-device deployment
- The MoE variant (26B) is separate and activates ~3.8B of 26B parameters per token
- E4B achieves speed via architecture efficiency (PLE, sliding window attention), not expert gating

**For voice-commander use:** E4B's dense design is preferable — no expert routing latency, deterministic execution path, simpler integration with LM Studio.

---

## 3. Tool-Calling Format & Reliability

**Native support:** Yes, Gemma 4 models (including E4B) natively support function calling via dedicated special tokens.

**Format:**
- **Output format:** Structured function calls using `<|tool_call>...<|/tool_call>` XML-like tags, not pure JSON in message.content
- **Input schema:** Define tools as JSON schema objects (function name, description, typed parameters)
- **Two definition methods:**
  1. Manual JSON dictionaries (recommended for complex nested params)
  2. Python function parsing (auto-generates schema from type hints + docstrings)

**OpenAI compatibility caveat:**
- Gemma 4 **does NOT natively emit OpenAI `tool_calls` JSON** in the response structure
- Libraries like vLLM (with `--enable-auto-tool-choice`) provide OpenAI-compatible wrappers
- **Direct integration required:** Parse `<|tool_call>...<|/tool_call>` tags yourself or use a wrapper library
- LM Studio may parse these natively; requires local testing

**Tool-choice behavior:**
- Model can be prompted to require tool use (equivalent to `tool_choice="required"`)
- No explicit `tool_choice` parameter; enforce via system prompt instruction ("You must use a tool to answer this")
- Model reliably routes to tools when instructed; not hallucinating tool names in standard setups

**Reliability notes:**
- ✓ Dedicated tokens prevent partial tool calls and tool-definition confusion
- ✓ Handles complex schemas better than earlier open models
- ⚠ Validation always required: confirm tool names exist in registry, validate argument types/ranges before execution
- ⚠ Multiple parallel tool calls (>3 per turn) may have degraded accuracy; serial routing recommended
- ⚠ Some fine-tuned variants (OBLITERATED, etc.) have hallucination issues from deleted K/V tensors; use official `google/gemma-4-E4B-it`

**Known parsing gaps:** Some inference libraries (mlx-lm, early Ollama versions) fail to parse native tool calls into OpenAI-compatible `tool_calls` field. Workaround: parse raw output or use vLLM/LM Studio directly.

---

## 4. Latency Performance

**Cold start:**
- Model load: ~2–5 seconds (depends on quantization and GPU)

**Steady-state (warm cache):**
- **Q4_K_M on RTX 3060/4060:** ~196 tokens/sec (observed via Ollama)
- **Full bfloat16 on RTX 3060/4060:** ~13.82 tokens/sec (lower throughput, higher quality)
- **Typical tool-call response:** 50–150 tokens, ~260–770ms at 196 tok/s

**Latency fit:**
- **600ms budget:** achievable with Q4_K_M at <100 tokens per call
- **Dense responses (>150 tokens):** may exceed 600ms; trim with max_tokens constraint
- **Quantization trade-off:** Q4_K_M is aggressive; Q5_K_M (~4.5–5 GB) offers better quality at similar latency

**VRAM requirements:**
- Q4_K_M: 4–5 GB
- Q5_K_M: 5–6 GB
- bfloat16: ~9.5 GB
- RTX 3060 (12 GB): comfortable with Q4_K_M or Q5_K_M; bfloat16 tight but feasible

---

## 5. Tool Count & Schema Size

**No published hard limit found.** Community practice suggests:

- **Recommended:** 10–20 tools per schema (observed in examples and blog posts)
- **Tested range:** Up to 30 tools without degradation (varies by context window usage and prompt length)
- **Safety margin:** Keep schema under 2K tokens; monitor accuracy above 25 tools

**Rationale:**
- Long context (128K tokens) allows room for tool definitions + conversation history
- Accuracy depends on schema clarity and argument count, not tool quantity alone
- More tools = more tokens; leaves less space for in-context examples and conversation

**For voice-commander MVP (14 tools):** Well within safe zone. Monitor accuracy in Phase 6+ if expanding beyond 30.

**Best practice:**
- Group related tools (e.g., `clipboard_copy`, `clipboard_paste`) into a single tool with a subcommand param
- Keep descriptions under 100 chars
- Use enum types for common parameters (e.g., `window_name: ["browser", "terminal", "notepad"]`)

---

## 6. Assessment for Voice-Commander Phase 6+

**Suitability:** HIGH

| Criterion | Status | Notes |
|-----------|--------|-------|
| **Model exists** | ✓ Public, widely available | Direct download via LM Studio, HuggingFace, Ollama |
| **MoE / efficient** | ✓ 4.5B effective (dense, not MoE) | Fast on consumer GPUs; no expert-routing overhead |
| **Tool calling** | ✓ Native support | Custom XML format; requires parsing or LM Studio wrapper |
| **Tool_choice="required"** | ⚠ Partial | Emulated via system prompt; no explicit parameter; works reliably in practice |
| **Latency <600ms** | ✓ Achievable | 196 tok/s with Q4_K_M; 100-token calls = ~510ms steady-state |
| **Tool count (14 MVP)** | ✓ Safe | Well below recommended 20–30 |
| **OpenAI compat** | ⚠ Needs wrapper | Native format is XML-like; vLLM + LM Studio support; mlx-lm has gaps |
| **Community maturity** | ✓ Strong | 849K+ downloads, active integrations (vLLM, LM Studio, Ollama) |

---

## 7. Implementation Roadmap (Phase 6+)

**Minimum viable integration:**
1. Load E4B-it via LM Studio API (OpenAI-compatible endpoint)
2. Wrap tool registry as JSON schema; pass to system prompt
3. Call LM Studio with `temperature=0.7`, `top_p=0.95`, `max_tokens=100`
4. Parse response for `<|tool_call>` tags
5. Extract tool name + args; validate before dispatch
6. Return execution result to LM Studio for final answer generation

**Risk mitigation:**
- Test `tool_choice` enforcement locally before production
- Start with <10 tools; expand after validation
- Monitor token counts per call; trim if approaching 600ms
- Use Q5_K_M if accuracy on argument parsing is poor with Q4_K_M

**Future optimization:**
- Fine-tune on domain-specific tools (optional; base model likely sufficient for MVP)
- Experiment with structured output constraints (GBNF grammar) if hallucinations persist

---

## 8. Uncertainty & Verification Checklist

**Unknowns (verify locally when implementing):**
- [ ] Does LM Studio parse E4B's native tool calls into OpenAI `tool_calls` field? (If not, parse `<|tool_call>` tags manually)
- [ ] What is actual latency on your RTX 3060/4060 with your quantization? (Benchmark locally: 100 dummy calls, average time)
- [ ] Does `tool_choice="required"` enforcement via system prompt work reliably in practice? (Run 50-call validation)
- [ ] Max token count before latency exceeds 600ms on your setup? (Instrument and measure)

**Before Phase 6 implementation:**
1. Download E4B-it (Q4_K_M) and load in LM Studio
2. Measure cold + warm latency on your target GPU
3. Test 5–10 sample calls with 3–5 tool definitions
4. Verify argument parsing accuracy on complex schemas
5. Document parsing format and any workarounds in `docs/decisions/ADR-0020-tool-router.md`

---

## Sources

- [Gemma 4 on LM Studio](https://lmstudio.ai/models/google/gemma-4-e4b)
- [Google AI for Developers: Function Calling with Gemma 4](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4)
- [HuggingFace: google/gemma-4-E4B](https://huggingface.co/google/gemma-4-E4B)
- [Google Blog: Gemma 4 — Byte for byte, the most capable open models](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
- [MindStudio: Gemma 4 Mixture of Experts Architecture](https://www.mindstudio.ai/blog/gemma-4-mixture-of-experts-architecture)
- [MachineLearningMastery: How to Implement Tool Calling with Gemma 4 and Python](https://machinelearningmastery.com/how-to-implement-tool-calling-with-gemma-4-and-python/)
- [Oflight Inc.: Gemma 4 Hardware Requirements](https://www.oflight.co.jp/en/columns/gemma4-hardware-requirements-local-ai-spec-2026)
- [HuggingFace Blog: Welcome Gemma 4](https://huggingface.co/blog/gemma4)
- [vLLM Recipes: Gemma 4 Usage Guide](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
- [Lushbinary: Build AI Agent with Gemma 4](https://lushbinary.com/blog/build-ai-agent-gemma-4-function-calling-mcp-guide)
- [GitHub Issue: Gemma 4 E4B tool calling fails via Ollama OpenAI-compatible API](https://github.com/anomalyco/opencode/issues/20995)
- [GitHub Issue: Gemma 4 native tool calls are not parsed](https://github.com/ml-explore/mlx-lm/issues/1096)
