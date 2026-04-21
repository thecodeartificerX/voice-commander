# LM Studio Tool Calling for Voice Commander Router

**Status:** Research complete, April 2026  
**Goal:** Evaluate LM Studio's `/v1/chat/completions` endpoint for tool-calling route inference (MoE models like Gemma 4 E4B, prefix KV cache reuse)

---

## 1. Request Shape for `/v1/chat/completions` with `tools` Array

### OpenAI Compatibility
LM Studio implements [OpenAI-compatible Chat Completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions) at `/v1/chat/completions`. All standard OpenAI parameters are supported, including `tools` and `tool_choice`.

### Request JSON Structure
The endpoint accepts the following fields:

```json
{
  "model": "string",
  "messages": [
    {
      "role": "system|user|assistant",
      "content": "string"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "string",
        "description": "string",
        "parameters": {
          "type": "object",
          "properties": { },
          "required": ["array", "of", "required", "fields"]
        }
      }
    }
  ],
  "tool_choice": "auto|required|none",
  "temperature": 0.7,
  "top_p": 1.0,
  "max_tokens": 2048,
  "stream": false
}
```

**Key fields for tool calling:**
- `tools`: Array of function definitions following [OpenAI Function Calling API format](https://lmstudio.ai/docs/developer/openai-compat/tools)
- `tool_choice`: Controls whether the model *must* call a tool (`"required"`), *may* call one (`"auto"`), or *cannot* (`"none"`)
- `stream`: Boolean, controls Server-Sent Events response streaming (see §7)

**Supported inference parameters:** `temperature`, `top_p`, `top_k`, `repeat_penalty`, `presence_penalty`, `frequency_penalty`, `logit_bias`, `stop`, `seed` ([LM Studio Chat Completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions))

---

## 2. `tool_choice="required"` Support Across Models and Backends

### Universal OpenAI-Compatible Support
LM Studio honors the `tool_choice` parameter for all models via its OpenAI-compatible endpoint. **However, response quality depends on model training and backend:**

- **Native tool support** (best): Qwen2.5, Llama-3.1/3.2, Ministral, and Gemma 4 were trained for tool use and emit correctly-formatted `tool_calls` consistently.
- **Default fallback support** (acceptable): Other models receive a custom system prompt and attempt to parse tool requests, but quality varies.

### Backend-Specific Behavior
- **llama.cpp (GGUF models):** Uses grammar-based constraint sampling to enforce tool JSON schema, ensuring syntactically valid output.
- **MLX backend (Apple Silicon):** Outlines library handles constraints.
- **Hybrid/sliding-window architectures (e.g., Qwen3.5-35B-A3B):** [Prefix cache reuse is reportedly broken](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1563) on MLX, forcing full prompt recompute per request (critical latency impact).

### Recommendation for Voice Commander
**Use Gemma 4 E4B or E2B:** Both have [native function-calling support](https://lmstudio.ai/blog/gemma-4/) and are small enough for real-time router latency on CPU/small GPU.

---

## 3. Multiple Tool Calls in a Single Response

### Yes—Fully Supported
LM Studio allows the model to emit **multiple tool calls in a single response**. The response structure is:

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "function_name",
              "arguments": "{\"param\": \"value\"}"
            }
          },
          {
            "id": "call_def456",
            "type": "function",
            "function": {
              "name": "another_function",
              "arguments": "{\"param\": \"value\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

### Streaming Considerations
[Tool calls stream as events](https://lmstudio.ai/docs/developer/rest/streaming-events) when `stream: true`. Each tool call emits:
- `tool_call.start` event
- `tool_call.arguments` events (argument tokens streamed)
- `tool_call.success` or `tool_call.failure` event

A [streaming bug in earlier versions](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1066) (empty function name after first chunk) has been fixed.

---

## 4. `response_format` JSON Schema / GBNF Grammar Constraints

### LM Studio Structured Output (JSON Schema Mode)
LM Studio supports [constrained structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output) via `response_format`, using:
- **llama.cpp (GGUF):** Grammar-based sampling (GBNF)
- **MLX:** Outlines library

### Syntax in LM Studio
Pass a JSON schema in the `response_format` parameter:

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "my_schema",
    "schema": {
      "type": "object",
      "properties": {
        "tool_name": { "type": "string" },
        "arguments": { "type": "object" }
      },
      "required": ["tool_name", "arguments"]
    }
  }
}
```

Response is returned as a JSON string in `choices[0].message.content` (requires parsing).

### Coexistence with `tools`
**Important:** `response_format` (JSON schema mode) and `tools` (tool calling) are **separate mechanisms**. Using both in the same request may produce undefined behavior. **Recommendation: Use `tools` + `tool_choice="required"` for voice routing; `response_format` is for raw JSON extraction only.**

### GBNF Grammars
[llama.cpp's GBNF](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md) is an extension of Backus-Naur Form supporting regex-like syntax. When LM Studio converts JSON schemas to GBNF for llama.cpp, it handles:
- Object, array, string, number, integer, boolean types
- Enums and nested structures
- anyOf / allOf for unions

---

## 5. Prefix KV Cache Reuse Between Calls

### Current Limitation
**Prefix KV cache reuse does NOT work reliably in LM Studio as of April 2026.** [Reported issues](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1563):

- Full-attention models (e.g., Llama 3, Mistral) may reuse KV if identical system prompt + tools array are sent.
- Hybrid-architecture models (Qwen3.5, sliding-window, SSM) have KV reuse **silently disabled**, forcing full prompt recompute (~200s for 40K tokens vs. ~5s with cache).
- MLX backend behavior is inconsistent; MiniMax M2.5 is reportedly the only model with reliable cache reuse.

### Practical Impact for Voice Router
**Expect ~500ms–2s latency per route inference** with a small model like Gemma 4 E4B, *even with identical system prompt and tools array across calls*, because KV cache reuse is unreliable. This is acceptable for interactive voice commands (VAD already segments on silence).

### Configuration Knobs
LM Studio does not expose a direct `cache_reuse` or `kv_cache_prefix` config in the API. The behavior is backend/model dependent. No client-side knob exists to force or disable reuse.

### Workaround (for future)
[LMCache](https://docs.lmcache.ai/) is an external project that adds persistent KV layer, achieving 3×–10× speedups, but requires instrumenting the inference engine outside LM Studio's scope.

---

## 6. Health/Probe Endpoint

### `GET /v1/models` Response
The [models endpoint](https://lmstudio.ai/docs/developer/rest/endpoints) lists loaded models:

```bash
curl http://localhost:1234/v1/models
```

**Response structure:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "model-name",
      "object": "model",
      "owned_by": "lm-studio",
      "permission": []
    }
  ]
}
```

### Health Check Usage
Simple HTTP GET to `/v1/models`:
- **200 OK + non-empty `data` array** = LM Studio is running and model is loaded.
- **200 OK + empty `data` array** = Server is running but no model loaded.
- **Connection refused** = Server is offline.

### Caveat
[LM Studio's HTTP server returns 200 for invalid paths](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1323) (security quirk). Always parse the response body, not just the status code.

---

## 7. Known Gotchas

### Streaming Tool Calls
- Streaming (`stream: true`) is **fully supported** with [SSE event structure](https://lmstudio.ai/docs/developer/rest/streaming-events).
- Streaming emits `tool_call.start`, `tool_call.arguments`, and `tool_call.success|failure` events.
- Parsing SSE requires line-buffered reading; async frameworks handle this automatically.

### Concurrent Requests
- LM Studio 0.4.0+ supports concurrent requests; default `Max Concurrent Predictions = 4`.
- Requests beyond the limit are queued.
- Each request gets its own KV cache slot (Unified KV Cache, enabled by default).

### Timeout Defaults
- **Python SDK default:** 60 seconds (no activity).
- **Underlying engine (mlx-lm) hard limit:** 300 seconds.
- For long prompts or slow hardware, design for timeout handling.

### Model Capability Constraints
- [Structured output requires models ≥7B parameters](https://lmstudio.ai/docs/developer/openai-compat/structured-output). MoE densities (E4B = "effective 4B") may not qualify.
- Gemma 4 E4B and E2B support native function calling and perform well for tool routing despite small size.

### Streaming + Structured Output
Using `stream: true` with `response_format: json_schema` is **not recommended**. Use non-streaming for guaranteed schema compliance.

---

## Conclusions for Voice Commander Router

### Viable Path ✅
1. Load **Gemma 4 E4B** (or E2B) in LM Studio.
2. POST to `/v1/chat/completions` with:
   - `tool_choice: "required"` (forces tool call response)
   - `tools` array (tool definitions from registry)
   - Short system prompt describing routing task
   - User utterance transcript
3. Parse `choices[0].message.tool_calls` array; execute matched tool(s).
4. Multi-turn: return tool results via `messages` + role "tool" for refinement.

### Expected Latency
- **500ms–2s per inference** (no KV cache reuse due to MLX/Gemma limitations).
- **Acceptable** for voice (VAD already buffers silence).
- **Monitor via health endpoint:** `GET /v1/models` before each session.

### Limitations Acknowledged
- Prefix KV cache reuse unreliable; consider LMCache in Phase 6+ if sub-500ms latency required.
- No per-model `tool_choice` fallback; ensure model is trained for tool use.
- Structured output mode (`response_format`) separate from `tools`; don't mix.

---

## References

- [LM Studio Tool Use](https://lmstudio.ai/docs/developer/openai-compat/tools)
- [LM Studio Chat Completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions)
- [LM Studio Structured Output](https://lmstudio.ai/docs/developer/openai-compat/structured-output)
- [LM Studio Streaming Events](https://lmstudio.ai/docs/developer/rest/streaming-events)
- [LM Studio Models Endpoint](https://lmstudio.ai/docs/developer/rest/endpoints)
- [Gemma 4: Native Function Calling](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
- [LM Studio KV Cache Reuse Issue #1563](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1563)
- [llama.cpp GBNF Grammars](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
- [LMCache: KV Cache Acceleration (external)](https://docs.lmcache.ai/)
- [OpenAI Cookbook: Run GPT with LM Studio](https://developers.openai.com/cookbook/articles/gpt-oss/run-locally-lmstudio)
