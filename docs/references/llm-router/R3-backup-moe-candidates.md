# R3: Backup MoE Candidates for Local Voice-Command Tool-Calling Router

**Date**: 2026-04-21  
**Context**: Primary choice is Gemma 4 E4B. This research identifies drop-in replacements if it underperforms.  
**Hardware Target**: Single RTX 3060–4090 (RTX 4090 preferred). LM Studio compatible. OpenAI `tool_choice="required"` compatible.  
**Latency Budget**: 600ms/call warm (model loaded, CUDA initialized).

---

## Comparison Matrix

| Model | Total / Active Params | Tool-Call Template | LM Studio Support | Community Tool-Call Rep | Disk (Q4/Q5) | Est. Latency (4090) | Recommendation |
|-------|------------------------|-------------------|-------------------|------------------------|--------------|--------------------|-----------------|
| **Qwen3-30B-A3B-Instruct** | 30.5B / 3.3B | OpenAI tools + Hermes style | ✓ (catalog) | Excellent, native support | 18.6 GB / 21.7 GB | ~250–400ms | ⭐⭐⭐ Primary drop-in |
| **Qwen3-Coder-30B-A3B** | 30.5B / 3.3B | OpenAI tools + Hermes (fixed 2507+) | ✓ (catalog) | Excellent for coding tasks | 18.6 GB / 21.7 GB | ~250–400ms | ⭐⭐⭐ Best if code routing needed |
| **Phi-3.5-MoE-Instruct** | 16B / 6.6B | Hermes, limited native OpenAI | ✓ (high demand) | Good, but tool-call grammar quirks | ~8–10 GB (est.) | ~300–450ms | ⭐⭐ Backup for low VRAM |
| **Mixtral-8x7B-Instruct** | 56B / 12.9B | Hermes/Llama format (not native) | ✓ | Fair; most providers only support inference | 28.4 GB / 33.2 GB | ~400–600ms (at budget limit) | ⭐ Older, consider if budget allows |
| **Granite-3.1-MoE** | 3B–8B dense + MoE | OpenAI tools (tested) | ✓ (via Ollama) | Excellent; hallucination detection in 3.1 | ~2–8 GB | ~150–250ms | ⭐⭐⭐ Excellent if latency critical |
| **Ling-mini-2.0** | 16B / 1.4B | Function calling (via vLLM) | ⚠️ Limited, emerging | Solid, newer; minimal community history | ~4–6 GB | ~200–300ms | ⭐⭐ Promising new option |

---

## Model Deep Dives

### **Qwen3-30B-A3B-Instruct** (Alibaba, 2024)

**Params**: 30.5B total, 3.3B activated per token. 128 experts, 8 active.  
**Tool Calling**: Native OpenAI tools API support via `Qwen-Agent` library; Hermes-style tool use also works well. Documented in [Qwen Function Calling docs](https://qwen.readthedocs.io/en/latest/framework/function_call.html).  
**Disk**: Q4_K_M = 18.6 GB, Q5_K_M = 21.7 GB.  
**Latency**: RTX 4090 achieves ~110 tokens/sec (87 t/s on RTX 3090), placing tool-call latency at 250–400ms warm.  
**LM Studio**: Confirmed in [LM Studio catalog](https://lmstudio.ai/models).  
**Quirks**: Requires `transformers >= 4.51.0` for MoE routing. Function-call parser must be explicitly wired (Qwen-Agent handles this). Context window is massive (262K native, 1M with Yarn) — useful for long conversation history.  
**License**: Qwen License (permissive for research, commercial restrictions apply).  
**Community**: Heavy adoption in Chinese/Asian AI circles; GitHub issues show robust tool-call deployments.

**Why it's the primary backup**: Directly comparable to Gemma 4 in size and active params, proven agentic use, LM Studio support, and explicit tool-calling optimizations.

---

### **Qwen3-Coder-30B-A3B** (Alibaba, 2025)

**Params**: Identical to base (30.5B / 3.3B).  
**Tool Calling**: Same as Qwen3-30B-A3B, with additional instruction-tuning for coding tasks. Hermes-style fixed in 2507+ release.  
**Disk**: Same (18.6 GB Q4, 21.7 GB Q5).  
**Latency**: Identical (~250–400ms).  
**LM Studio**: Yes, [listed explicitly](https://lmstudio.ai/models/qwen/qwen3-coder-30b-a3b).  
**Quirks**: Specialized for browser automation and coding-heavy agentic tasks. May overfit to code-style outputs even for non-code tools. Tool-call parser known to have quirks in vLLM but fixed in llama.cpp/LM Studio.  
**License**: Qwen License.  
**Community**: Smaller community than base model, but strong GitHub activity (GitHub issue #6913 shows active discussion of local tool-call use).

**When to use**: If your voice-command suite includes code-generation or complex logic routing (e.g., "write a batch script then run it"), Qwen3-Coder outperforms the base.

---

### **Phi-3.5-MoE-Instruct** (Microsoft, August 2024)

**Params**: 16B total, 6.6B activated (using 2 of 8 experts). Far fewer experts than Qwen but more active.  
**Tool Calling**: Hermes format supported; no native OpenAI tools API, but grammar can be adapted. Community reports tool-use works but requires prompt engineering.  
**Disk**: Estimated 8–10 GB Q4, 10–12 GB Q5 (smaller footprint than Qwen).  
**Latency**: RTX 4090 achieves better throughput per active param due to smaller expert count, ~300–450ms estimated.  
**LM Studio**: High demand; search results show active community builds ([bartowski/Phi-3.5-MoE-instruct-GGUF](https://huggingface.co/bartowski/Phi-3.5-MoE-instruct-GGUF)), though not yet in official LM Studio catalog at time of writing.  
**Quirks**: Function-call grammar is brittle; output often includes extraneous tokens before `<tool_call>`. Smaller active param count means weaker semantic understanding for complex routing decisions. Reasoning scores competitive but not elite.  
**License**: MIT (fully permissive).  
**Community**: Strong, well-documented (see [Phi-3.5 Cookbook](https://github.com/microsoft/Phi-3CookBook)).

**When to use**: If VRAM is severely limited (RTX 3060 with other apps) or inference latency is critical. Trades off reasoning quality for speed.

---

### **Mixtral-8x7B-Instruct** (Mistral, v0.1 EOL)

**Params**: 56B total, 12.9B activated (8 of 8 experts, 2 routed per token).  
**Tool Calling**: No native OpenAI tools. Hermes/Llama format possible but requires post-processing. Most inference providers (Together, DeepInfra) do NOT expose function-calling APIs.  
**Disk**: Q4_K_M = 28.4 GB, Q5_K_M = 33.2 GB (nearly 2× Qwen, approaches 4090 VRAM ceiling).  
**Latency**: 379–387ms time-to-first-token reported across major providers, plus ~200ms for 20-token tool-call response = ~580–600ms, **at budget limit**. RTX 4090 local estimate: 400–600ms.  
**LM Studio**: Yes, widely supported.  
**Quirks**: Older MoE design (2023). Many experts activated per token, higher overhead. Tool-calling requires custom post-processing (not a drop-in replacement). Community reports degraded performance vs. Qwen3 on function-call fidelity.  
**License**: Mistral Research License (non-commercial research).  
**Community**: Still widely deployed, but being superseded by Mixtral-8x22B (larger, better) and newer MoEs.

**When to use**: Only if Gemma 4 fails AND RTX 4090 only (no VRAM limit). Even then, prefer Qwen3-30B-A3B or Granite. Mixtral is EOL-track.

---

### **Granite-3.1-MoE** (IBM, 2024)

**Params**: Two sizes: **3B-A800M** (3B total, 0.8B active) and **8B-A1.3B** (8B total, 1.3B active).  
**Tool Calling**: Explicitly optimized. IBM published [LM Studio tutorial](https://www.ibm.com/think/tutorials/use-lm-studio-to-build-automatic-tool-calling-granite) for local tool-calling with Granite. Granite-3.0 8B Instruct outperformed open models on Berkeley's Function Calling Leaderboard. Granite-3.1 adds function-call hallucination detection.  
**Disk**: 8B-A1.3B at Q4 ≈ 5 GB, Q5 ≈ 6 GB (smallest footprint).  
**Latency**: Extremely fast due to small active param count. Estimated 150–250ms on RTX 4090.  
**LM Studio**: Available via Ollama integration; native LM Studio support emerging.  
**Quirks**: Smaller model means weaker reasoning on complex multi-step routing. 128K context length is robust. Less widespread community adoption vs. Qwen/Phi.  
**License**: Apache 2.0 (fully permissive).  
**Community**: Growing; IBM published [granite-snack-cookbook](https://github.com/ibm-granite-community/granite-snack-cookbook) with tool-calling recipes.

**Why consider it**: If latency is paramount (e.g., sub-200ms for voice command responsiveness), Granite-3.1 8B is unbeaten. Trade-off is weaker reasoning.

---

### **Ling-mini-2.0** (InclusionAI / Ant Group, September 2025)

**Params**: 16B total, 1.4B activated (1/32 activation ratio, extremely sparse).  
**Tool Calling**: Supports function calling via vLLM, emerging support in other frameworks. Trained on 20T+ tokens with focus on reasoning and instruction-following.  
**Disk**: ~4–6 GB estimated (smallest in cohort).  
**Latency**: Fastest reported: 300+ tokens/sec on H20 (commercial GPU), implies ~180–250ms for 20-token tool-call on RTX 4090 (estimated).  
**LM Studio**: ⚠️ Not yet in official catalog; requires manual GGUF conversion or vLLM/SGLang. Emerging support.  
**Quirks**: Newest model (Sept 2025); minimal community history for tool-calling specifically. GitHub repositories exist but deployment patterns still stabilizing. High sparsity (1.4B active) is novel; behavior under edge cases unknown.  
**License**: MIT (permissive).  
**Community**: Active but small. See [inclusionAI/Ling GitHub](https://github.com/inclusionAI/Ling-V2).

**When to use**: Promising for future use once community validates tool-call fidelity. Currently experimental; not recommended as primary fallback yet.

---

## Ranked Top-3 Recommendation

### **Tier 1: Primary Backup**
**Qwen3-30B-A3B-Instruct**
- Mature ecosystem (Qwen-Agent library, vLLM/SGLang support, extensive docs)
- Proven tool-calling, native OpenAI API compatibility
- Params/latency/disk all within budget, easily drops in for Gemma 4
- LM Studio support confirmed
- Trade-off: Larger than Granite (21.7 GB Q5)

### **Tier 2: Best if Latency Critical OR Reasoning Strong Needed**
**Granite-3.1-8B-A1.3B** (if latency urgent) OR **Qwen3-Coder-30B-A3B** (if code-aware routing needed)
- Granite: 150–250ms latency, smallest footprint, explicit tool-call leaderboard wins
- Qwen3-Coder: Identical latency/disk to base, superior code reasoning
- Either trades off against Qwen3-30B-A3B only on specific use cases, not general quality

### **Tier 3: Future Monitor**
**Ling-mini-2.0**
- Best-in-class latency and disk footprint (~200ms, 4–6 GB)
- Too early; await 2–3 months of community validation on tool-call fidelity
- Consider in Q3 2026

---

## Implementation Notes for Your Use Case

1. **Tool-Calling Format**: All candidates support Hermes-style tool grammar or OpenAI tools via wrapper. Qwen3 models have the smoothest integration (test with `Qwen-Agent` library or raw function-call format).

2. **LM Studio Workflow**:
   - Qwen3-30B-A3B-Instruct: Catalog → download → ready
   - Qwen3-Coder: Catalog → download → ready
   - Granite: Via Ollama → load in LM Studio → requires manual config
   - Phi-3.5-MoE: Search GGUF on HF, load manually (not in catalog yet)
   - Ling-mini-2.0: Manual GGUF or vLLM server, not LM Studio native (yet)

3. **600ms Budget**:
   - All candidates fit comfortably except Mixtral (bottoms out at 580–600ms)
   - **Recommendation**: Qwen3-30B-A3B-Instruct = safe, Granite-3.1 = aggressive

4. **Hardware Validation**:
   - RTX 3060 (12 GB): Qwen3-30B-A3B at Q4 (18.6 GB) does NOT fit. **Use Phi-3.5-MoE or Granite-3.1 8B.**
   - RTX 4090 (24 GB): All candidates fit; Qwen3-30B-A3B or Qwen3-Coder recommended.

---

## Sources

- [Qwen3-30B-A3B-Instruct HF Card](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507)
- [Qwen Function Calling Docs](https://qwen.readthedocs.io/en/latest/framework/function_call.html)
- [Phi-3.5-MoE-Instruct HF Card](https://huggingface.co/microsoft/Phi-3.5-MoE-instruct)
- [IBM Granite Tool Calling (LM Studio)](https://www.ibm.com/think/tutorials/use-lm-studio-to-build-automatic-tool-calling-granite)
- [Granite-snack-cookbook](https://github.com/ibm-granite-community/granite-snack-cookbook)
- [Ling-mini-2.0 HF Card](https://huggingface.co/inclusionAI/Ling-mini-2.0)
- [Qwen3-Coder-30B-A3B GGUF (Unsloth)](https://huggingface.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF)
- [Mixtral-8x7B-Instruct GGUF (TheBloke)](https://huggingface.co/TheBloke/Mixtral-8x7B-Instruct-v0.1-GGUF)
- [LM Studio Model Catalog](https://lmstudio.ai/models)
- [Local AI Models 2026 Guide (Carthage Electronics)](https://carthageelectronics.com/latest-local-ai-models-2026-run-on-your-own-machine/)
- [Qwen3-30B Benchmark Analysis (Arxiv 2512.23029)](https://arxiv.org/html/2512.23029v1)
- [Phi-3 Cookbook (Microsoft)](https://github.com/microsoft/Phi-3CookBook)
- [InclusionAI Ling GitHub](https://github.com/inclusionAI/Ling-V2)

